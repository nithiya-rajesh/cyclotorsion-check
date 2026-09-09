"""``POST /detect``: the core clinical cyclotorsion-measurement endpoint.

Implements PRD US-1.1/1.2/2.3/5.1 image handling and the off-hot-path
analytics write (architecture review Major). ``_DETECTOR``/``_WRITER``/
``_ANALYTICS_WORKER`` are injected as FastAPI dependencies (``get_detector``/
``get_writer``/``get_analytics_worker``, defined in ``cyclotorsion.app`` —
real DI rather than reading the module's globals directly; staff review). The
tracer's begin/end lifecycle spans the whole handler body in a ``finally``, so
it stays a direct ``app_state.get_tracer()`` call rather than a
``Depends(...)`` — that shape doesn't fit a resource acquired once per request
and released only after the response is formed.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

import cyclotorsion.app as app_state
from cyclotorsion.app import (
    get_analytics_worker,
    get_detector,
    get_patient_store,
    get_writer,
)
from cyclotorsion.auth import UserContext, current_approved_user
from cyclotorsion.background import BackgroundWorker
from cyclotorsion.concurrency import acquire_concurrency, release_concurrency
from cyclotorsion.detector import LandmarkDetector
from cyclotorsion.geometry import corrected_axis_deg
from cyclotorsion.logging_config import current_request_id
from cyclotorsion.metrics import analytics_write, detection_recorded
from cyclotorsion.patients import DEFAULT_DEV_FACILITY, PatientStore
from cyclotorsion.pipeline import DetectionOutcome, run_detection
from cyclotorsion.rate_limit import rate_limit
from cyclotorsion.storage import (
    CaseRow,
    ResultRow,
    ResultWriter,
    new_test_id,
    utc_now_iso,
)
from cyclotorsion.text import looks_like_pii, sanitize_text
from cyclotorsion.tracing import span

# get_tracer is read via app_state (not imported directly) at call time: tests
# monkeypatch `cyclotorsion.app.get_tracer` as the single source of truth.

logger = logging.getLogger("cyclotorsion.app")

router = APIRouter()

# Input validation policy (TDD Section 5.5).
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB (PRD US-1.1)
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MISSING_SLOT_MESSAGE = "Both an upright and a supine image are required."

# Epic 6 (Case Reference & Toric Alignment Context) — validated optional inputs.
ALLOWED_EYE_LATERALITIES = {"OD", "OS"}
MAX_CASE_REF_CHARS = 80  # facility code, not a free-text essay

# Image decoding guard (MEDIUM-4 fix): cap the decoded pixel count so a tiny
# encoded file declaring huge dimensions cannot exhaust memory (decompression
# bomb / remote DoS). ~4096x4096 is ample for clinical eye photos.
MAX_IMAGE_PIXELS = 16_000_000
# Also bound Pillow's own decoder so any downstream full decode (detector mock,
# Gemini forwarding) fails fast with DecompressionBombError rather than
# materializing enormous arrays.
try:
    from PIL import Image as _PILImage

    _PILImage.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
except Exception:  # noqa: BLE001 - Pillow is optional in exotic installs
    pass


def _classify_storage_error(write_error: Exception) -> str:
    """Map a persistence exception to a stable, internal-safe classification.

    Never include the raw exception text here — it can embed SQL fragments,
    GCP resource names, or paths that would leak internals to a caller
    (MEDIUM-3 fix). This is a finite allow-list of generic codes.
    """
    name = type(write_error).__name__.lower()
    if name in {"timeoutexception", "timeouterror", "readtimeout"}:
        return "storage_timeout"
    if "quota" in name or "ratelimit" in name or "429" in name:
        return "storage_rate_limited"
    return "storage_unavailable"


def _validate_and_read(upload: UploadFile, slot_name: str) -> bytes:
    """Validate MIME type and size, then read the upload fully into memory."""
    if upload.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"{slot_name}: unsupported file type '{upload.content_type}'. "
            "Allowed: JPEG, PNG, WebP.",
        )
    try:
        data = upload.file.read(MAX_IMAGE_BYTES + 1)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail=f"{slot_name}: could not read file"
        ) from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{slot_name}: file exceeds the 10MB size limit",
        )
    if not data:
        raise HTTPException(status_code=400, detail=f"{slot_name}: file is empty")
    return data


def _image_dimensions(image_bytes: bytes) -> tuple[int, int]:
    """Return (width, height) of an image, enforcing a decoded-pixel cap.

    Raises 413 for decompression bombs (huge declared dimensions /
    DecompressionBombError) so a crafted file can't exhaust the replica's
    memory (MEDIUM-4 fix). Raises 400 for a corrupt/undecodable image instead
    of silently falling back to a placeholder and continuing (architecture
    review Minor): processing a corrupt upload with fabricated dimensions would
    produce a misleading clinical result, so the request must fail fast.
    """
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            w, h = img.size
            if w * h > MAX_IMAGE_PIXELS:
                raise HTTPException(
                    status_code=413,
                    detail="Image dimensions exceed the safe pixel limit",
                )
            img.verify()  # integrity check; forces safe decode-parse validation
            return (w, h)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - classified below, never propagated raw
        # Bomb detection surfaces as DecompressionBombError during open/layout.
        if type(exc).__name__ == "DecompressionBombError":
            raise HTTPException(
                status_code=413, detail="Image exceeds the safe decompressed size"
            ) from exc
        # Corrupt / undecodable image: fail fast rather than fabricate dimensions.
        logger.warning(
            "detect.image_decode_failed",
            extra={
                "event": "detect.image_decode_failed",
                "error_type": type(exc).__name__,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=400, detail="Image is corrupt or could not be decoded"
        ) from exc


class _CaseFields:
    """Normalised, validated optional Epic 6 inputs (or None)."""

    __slots__ = ("case_ref", "eye_laterality", "target_axis_deg", "patient_id")

    def __init__(
        self,
        case_ref: str | None,
        eye_laterality: str | None,
        target_axis_deg: float | None,
        patient_id: str | None = None,
    ) -> None:
        self.case_ref = case_ref
        self.eye_laterality = eye_laterality
        self.target_axis_deg = target_axis_deg
        self.patient_id = patient_id


def _validate_case_fields(
    case_ref: str | None,
    eye_laterality: str | None,
    target_axis_deg: float | None,
    patient_id: str | None = None,
) -> _CaseFields:
    """Validate and normalise the optional Epic 6 inputs (US-6.1/6.2/6.3).

    All fields are fully optional — the core Epic 1-3 detection workflow
    is unchanged when none are supplied (US-6.1/6.2/6.3 AC). Enforced rules:

      - ``eye_laterality`` must be ``OD`` or ``OS`` (case-insensitive).
      - ``target_axis_deg`` must be in ``[0, 180)`` (toric axes are 0-180 mod).
      - ``case_ref`` is free-form facility text, capped in length and sanitized
        (untrusted input) — never used verbatim in SQL.
      - ``patient_id`` (PRD Epic 7) is a UUID that, if supplied, must be a
        valid UUID shape; its *facility* membership is validated against the
        patient store in the handler body (TDD 3.2a note — no enforced FK
        across Cloud SQL/BigQuery, so the application validates it).
    """
    if case_ref is not None:
        case_ref = sanitize_text(case_ref, max_chars=MAX_CASE_REF_CHARS)
        if not case_ref:
            case_ref = None

    if eye_laterality is not None:
        eye_laterality = eye_laterality.strip().upper()
        if eye_laterality not in ALLOWED_EYE_LATERALITIES:
            raise HTTPException(
                status_code=422,
                detail="eye_laterality must be 'OD' (right eye) or 'OS' (left eye).",
            )

    if target_axis_deg is not None:
        if not (0.0 <= target_axis_deg < 180.0):
            raise HTTPException(
                status_code=422,
                detail="target_axis_deg must be a degrees value in [0, 180).",
            )
        target_axis_deg = round(target_axis_deg, 2)

    if patient_id is not None:
        patient_id = (patient_id or "").strip()
        if not patient_id:
            patient_id = None
        elif not _is_uuid(patient_id):
            raise HTTPException(status_code=422, detail="patient_id must be a UUID.")

    return _CaseFields(case_ref, eye_laterality, target_axis_deg, patient_id)


def _is_uuid(value: str) -> bool:
    try:
        from uuid import UUID

        UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


@router.post("/detect")
async def detect(
    request: Request,
    upright: UploadFile = File(...),
    rotated: UploadFile = File(...),
    case_ref: str | None = Form(None),
    eye_laterality: str | None = Form(None),
    target_axis_deg: float | None = Form(None),
    patient_id: str | None = Form(None),
    user: UserContext = Depends(current_approved_user),
    _rate: None = Depends(rate_limit),
    detector: LandmarkDetector = Depends(get_detector),
    writer: ResultWriter = Depends(get_writer),
    patient_store: PatientStore = Depends(get_patient_store),
    analytics_worker: BackgroundWorker = Depends(get_analytics_worker),
) -> dict[str, Any]:
    """Compute the cyclotorsion angle between two eye photographs.

    Requires a verified, approved Firebase account when auth is enabled.

    Optional Epic 6 inputs (all fully optional — US-6.1/6.2/6.3): ``case_ref``
    (facility pseudonymous code), ``eye_laterality`` (``OD``/``OS``), and
    ``target_axis_deg`` (planned toric-IOL axis from pre-op biometry). When a
    ``target_axis_deg`` is supplied, the response's ``corrected_axis_deg`` uses
    it instead of any placeholder, so the corrected axis is clinically real
    (US-6.2 AC). ``case_ref`` is sanitized (untrusted text) and nudged for
    possible PII (US-6.4).

    The per-user concurrency slot is acquired *inside* this handler (not as a
    dependency) so that a 429 from ``rate_limit`` — which FastAPI resolves
    before this body runs — can never leave an acquired slot unreleased. The
    ``finally`` below releases it whenever the body has begun.
    """
    upright_bytes = _validate_and_read(upright, "upright")
    rotated_bytes = _validate_and_read(rotated, "rotated")

    case_fields = _validate_case_fields(
        case_ref=case_ref,
        eye_laterality=eye_laterality,
        target_axis_deg=target_axis_deg,
        patient_id=patient_id,
    )

    # PRD Epic 7 / TDD 3.2a note: ``cases.patient_id`` is a logical FK across
    # two database systems with no enforced constraint, so the application
    # validates that the referenced patient exists AND belongs to the same
    # facility before a case is written. Fail closed with 404 if not.
    if case_fields.patient_id is not None:
        scoped_facility = user.facility_id or DEFAULT_DEV_FACILITY
        patient = patient_store.get_patient(case_fields.patient_id, scoped_facility)
        if patient is None:
            raise HTTPException(
                status_code=404,
                detail="patient_id does not exist in this facility",
            )

    tracer = app_state.get_tracer()
    trace_token = tracer.begin(current_request_id())
    try:
        acquire_concurrency(request, user)
        # Transient handling only (PRD US-5.1): images exist purely in memory for
        # the duration of this request and are discarded when it ends. No file is
        # ever written to persistent storage anywhere in this path.
        width, height = _image_dimensions(upright_bytes)
        logger.info(
            "detect.start", extra={"event": "detect.start", "w": width, "h": height}
        )

        with span("detect.run"):
            outcome: DetectionOutcome = run_detection(
                detector, upright_bytes, rotated_bytes, width, height
            )
        logger.info(
            "detect.computed",
            extra={
                "event": "detect.computed",
                "angle_deg": outcome.angle_deg_rounded,
                "passed_sanity": outcome.sanity.passed,
                "sanity_flags": outcome.sanity.flags_csv,
            },
        )

        # Best-effort analytics write — OFF the request hot path (architecture
        # review Major): a BigQuery insert must never add to the clinical-result
        # tail latency. The row is enqueued on a bounded background worker, so
        # a slow/saturated analytics backend can only cost us telemetry, not the
        # surgeon's response. The clinical result is complete regardless.
        row = ResultRow(
            test_id=new_test_id(),
            timestamp=utc_now_iso(),
            angle_deg=outcome.angle_deg,
            upright_landmark=outcome.upright_landmark.description,
            rotated_landmark=outcome.rotated_landmark.description,
            passed_sanity_check=outcome.sanity.passed,
            sanity_flags=outcome.sanity.flags_csv,
            user_uid=user.uid,  # Firebase UID of the clinician, not a patient id
            facility_id=user.facility_id,  # facility scoping for /stats (TDD 5.2)
            case_ref=case_fields.case_ref,  # pseudo case code (PRD Epic 6)
        )

        # US-6.4 safety nudge (server side, defense in depth): log + count a
        # flagged case_ref. This is a *nudge*, not a hard guarantee — the
        # primary control is the facility's data-handling policy — and it never
        # blocks the clinical result. The client performs the interactive
        # confirm-before-proceed nudge; here we just record the signal.
        if case_fields.case_ref and looks_like_pii(case_fields.case_ref):
            logger.warning(
                "detect.case_ref_looks_like_pii",
                extra={"event": "detect.case_ref_looks_like_pii"},
            )

        def _background_write() -> None:
            # Runs on the background worker: perform the inserts and record the
            # failure metric with the *actual* outcome. Failures are logged
            # server-side (full exception kept internal — MEDIUM-3) and counted
            # in bigquery_write_failure_rate; they are never returned to a
            # client because this job runs after the response is formed.
            write_error = writer.write_row(row)
            analytics_write(failed=write_error is not None)
            if write_error is not None:
                logger.warning(
                    "detect.log_write_failed",
                    extra={
                        "event": "detect.log_write_failed",
                        "error_type": type(write_error).__name__,
                        "code": _classify_storage_error(write_error),
                    },
                )
            # Case-level context (US-6.2) is written to the separate `cases`
            # table keyed by case_ref (one case -> many results rows, TDD 3.2).
            if case_fields.case_ref:
                case_error = writer.upsert_case(
                    CaseRow(
                        case_ref=case_fields.case_ref,
                        created_by=user.uid,
                        created_at=row.timestamp,
                        eye_laterality=case_fields.eye_laterality,
                        target_axis_deg=case_fields.target_axis_deg,
                        facility_id=row.facility_id,
                        patient_id=case_fields.patient_id,
                    )
                )
                if case_error is not None:
                    logger.warning(
                        "detect.case_upsert_failed",
                        extra={
                            "event": "detect.case_upsert_failed",
                            "error_type": type(case_error).__name__,
                            "code": _classify_storage_error(case_error),
                        },
                    )

        # Non-blocking enqueue. If the bounded queue is full the job is dropped
        # (and logged by the worker); the result is still correct — analytics
        # is best-effort by design (TDD 2.1). The attempt/failure metric is
        # recorded inside the job once the outcome is known.
        analytics_worker.submit(_background_write)
        detection_recorded(
            passed=outcome.sanity.passed, flagged=bool(outcome.sanity.flags_csv)
        )

        response = outcome.to_client_dict()
        response["test_id"] = row.test_id
        if case_fields.case_ref:
            response["case_ref"] = case_fields.case_ref
            if case_fields.eye_laterality:
                response["eye_laterality"] = case_fields.eye_laterality
            if case_fields.patient_id:
                response["patient_id"] = case_fields.patient_id
        # US-6.2 AC: the "corrected axis" uses the entered target axis instead
        # of any hardcoded placeholder — computed server-side, clinically real.
        if case_fields.target_axis_deg is not None:
            response["corrected_axis_deg"] = round(
                corrected_axis_deg(case_fields.target_axis_deg, outcome.angle_deg),
                2,
            )
        return response
    finally:
        tracer.end(trace_token, current_request_id())
        release_concurrency(request)
