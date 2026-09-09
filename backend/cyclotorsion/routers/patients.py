"""``/patients/*``: patient-profile CRUD, search, and right-to-erasure (PRD Epic 7).

Implements PRD US-7.1 (create profile), US-7.2 (search by name/MRN), US-7.3
(patient case/result history), US-7.4 (consent capture), US-7.5 (cascading
right-to-erasure), US-7.6 (facility-scoped access), and keeps ``/stats``
architecturally isolated from the patient store (US-7.7 — the stats router has
no path into this store at all).

Access control (TDD Section 5.2, "Added by PRD Epic 7"): patient endpoints are
for ``surgeon`` and ``facility_admin`` only — a ``program_officer`` may see
aggregate ``/stats`` but must NOT reach patient records. Every read/write is
scoped server-side to the caller's ``facility_id`` (US-7.6), derived from the
authenticated user, never from the request body.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from cyclotorsion.app import get_cfg, get_patient_store, get_writer
from cyclotorsion.auth import (
    ROLE_FACILITY_ADMIN,
    ROLE_SURGEON,
    UserContext,
    require_any_role,
)
from cyclotorsion.patients import (
    DEFAULT_DEV_FACILITY,
    MAX_FULL_NAME_CHARS,
    MAX_MRN_CHARS,
    MAX_PHONE_CHARS,
    ErasureLogRow,
    PatientRow,
    generate_mrn_suggestion,
    new_erasure_id,
)
from cyclotorsion.rate_limit import rate_limit

logger = logging.getLogger("cyclotorsion.patients")

router = APIRouter()


def _facility_scope(user: UserContext, cfg) -> str | None:
    """Resolve the facility the caller may operate on (US-7.6).

    With auth enabled, returns the caller's own ``facility_id`` — and requires
    it to be present (a surgeon with no facility has no patients). In
    open/dev mode, falls back to ``DEFAULT_DEV_FACILITY`` so the flow runs.
    """
    if cfg.auth_enabled:
        if not user.facility_id:
            raise HTTPException(
                status_code=403,
                detail="Authenticated caller has no facility scope",
            )
        return user.facility_id
    return DEFAULT_DEV_FACILITY


def _validate_dob(value: str) -> None:
    from datetime import date

    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="date_of_birth must be an ISO date (YYYY-MM-DD)"
        ) from exc


def _validate_name(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail="full_name is required")
    if len(value) > MAX_FULL_NAME_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"full_name exceeds {MAX_FULL_NAME_CHARS} characters",
        )
    return value


def _validate_mrn(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail="mrn is required")
    if len(value) > MAX_MRN_CHARS:
        raise HTTPException(
            status_code=422, detail=f"mrn exceeds {MAX_MRN_CHARS} characters"
        )
    return value


def _validate_phone(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > MAX_PHONE_CHARS:
        raise HTTPException(
            status_code=422, detail=f"phone exceeds {MAX_PHONE_CHARS} characters"
        )
    return value


@router.post("/patients", status_code=201)
def create_patient(
    body: dict,
    user: UserContext = Depends(require_any_role(ROLE_SURGEON, ROLE_FACILITY_ADMIN)),
    _rate: None = Depends(rate_limit),
    store=Depends(get_patient_store),
    cfg=Depends(get_cfg),
) -> dict:
    """Create a patient profile (US-7.1) and record explicit consent (US-7.4).

    Consent is captured from this same request: the caller asserts that the
    patient (or guardian) consented to storing identity for the single stated
    purpose of linking this facility's toric-IOL alignment tests to their own
    patient record. ``consent_captured_by`` is the caller's UID,
    ``consent_captured_at`` is now.
    """
    facility_id = _facility_scope(user, cfg)

    consent = body.get("consent")
    if consent is not True:
        raise HTTPException(
            status_code=422,
            detail="Explicit consent ('consent': true) is required to create a patient profile",
        )

    full_name = _validate_name(body.get("full_name"))
    mrn = _validate_mrn(body.get("mrn"))
    dob = body.get("date_of_birth")
    if not dob:
        raise HTTPException(status_code=422, detail="date_of_birth is required")
    _validate_dob(dob)
    phone = _validate_phone(body.get("phone"))

    try:
        row = store.create_patient(
            full_name=full_name,
            date_of_birth=dob,
            mrn=mrn,
            facility_id=facility_id,
            created_by=user.uid or "local",
            consent_captured_by=user.uid or "local",
            consent_captured_at=datetime.now(UTC).isoformat(),
            phone=phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    logger.info(
        "patients.created",
        extra={
            "event": "patients.created",
            "patient_id": row.patient_id,
            "facility_id": facility_id,
            "created_by": user.uid,
        },
    )
    return _patient_payload(row)


@router.get("/patients/search")
def search_patients(
    q: str = Query("", max_length=MAX_MRN_CHARS + MAX_FULL_NAME_CHARS),
    limit: int = Query(10, ge=1, le=50),
    user: UserContext = Depends(require_any_role(ROLE_SURGEON, ROLE_FACILITY_ADMIN)),
    _rate: None = Depends(rate_limit),
    store=Depends(get_patient_store),
    cfg=Depends(get_cfg),
) -> dict:
    """Search patients by name or MRN prefix (US-7.2), facility-scoped."""
    facility_id = _facility_scope(user, cfg)
    rows = store.search_patients(facility_id, q, limit=limit)
    return {"facility_id": facility_id, "results": [_patient_payload(r) for r in rows]}


@router.get("/patients/suggest-mrn")
def suggest_mrn(
    user: UserContext = Depends(require_any_role(ROLE_SURGEON, ROLE_FACILITY_ADMIN)),
    _rate: None = Depends(rate_limit),
    cfg=Depends(get_cfg),
) -> dict:
    """Suggest a placeholder MRN for the create-patient form (US-7.1).

    A convenience default only — pre-filled but always editable client-side.
    Never a substitute for the hospital's own MRN when the surgeon has it;
    the create endpoint still enforces facility-scoped uniqueness the same
    way regardless of where the submitted value came from.
    """
    _facility_scope(user, cfg)
    return {"mrn": generate_mrn_suggestion()}


@router.get("/patients/{patient_id}")
def get_patient(
    patient_id: str,
    user: UserContext = Depends(require_any_role(ROLE_SURGEON, ROLE_FACILITY_ADMIN)),
    _rate: None = Depends(rate_limit),
    store=Depends(get_patient_store),
    cfg=Depends(get_cfg),
) -> dict:
    """Return one patient (US-7.3), facility-scoped."""
    facility_id = _facility_scope(user, cfg)
    row = store.get_patient(patient_id, facility_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return _patient_payload(row)


@router.get("/patients/{patient_id}/cases")
def patient_cases(
    patient_id: str,
    user: UserContext = Depends(require_any_role(ROLE_SURGEON, ROLE_FACILITY_ADMIN)),
    _rate: None = Depends(rate_limit),
    store=Depends(get_patient_store),
    writer=Depends(get_writer),
    cfg=Depends(get_cfg),
) -> dict:
    """Return the case/result history for one patient (US-7.3), facility-scoped."""
    facility_id = _facility_scope(user, cfg)
    row = store.get_patient(patient_id, facility_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    case_refs, err = writer.case_refs_for_patient(patient_id, facility_id)
    if err is not None:
        raise HTTPException(
            status_code=503,
            detail="Could not load patient history (analytics store unavailable)",
        )

    # Read rows without a facility filter and keep only those whose case_ref
    # belongs to this (already facility-scoped) patient. The case_refs were
    # resolved from the patient row's own facility, so this cannot leak another
    # facility's results into the response.
    rows_all, rerr = writer.read_rows()
    if rerr is not None:
        raise HTTPException(
            status_code=503,
            detail="Could not load patient history (analytics store unavailable)",
        )
    ref_set = set(case_refs)
    rows = [r for r in rows_all if r.case_ref in ref_set]
    return {
        "patient_id": patient_id,
        "case_refs": case_refs,
        "results": [_result_payload(r) for r in rows],
    }


def _result_payload(r) -> dict:
    return {
        "test_id": r.test_id,
        "timestamp": r.timestamp,
        "angle_deg": r.angle_deg,
        "case_ref": r.case_ref,
        "passed_sanity_check": r.passed_sanity_check,
        "sanity_flags": r.sanity_flags,
    }


@router.delete("/patients/{patient_id}", status_code=200)
def erase_patient(
    patient_id: str,
    user: UserContext = Depends(require_any_role(ROLE_FACILITY_ADMIN)),
    _rate: None = Depends(rate_limit),
    store=Depends(get_patient_store),
    writer=Depends(get_writer),
    cfg=Depends(get_cfg),
) -> dict:
    """Cascading right-to-erasure (US-7.5), facility-scoped, admin-only.

    Orchestrates the cross-database cascade described in TDD Section 3.2a:
      1. find every ``case_ref`` where ``cases.patient_id`` matches;
      2. DELETE matching ``results`` then ``cases`` in BigQuery (retry to
         completion — incomplete erasure is a DPDP compliance failure);
      3. DELETE the row from ``patients`` in Cloud SQL;
      4. write one ``patient_erasure_log`` row with the counts.
    """
    facility_id = _facility_scope(user, cfg)
    row = store.get_patient(patient_id, facility_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Step 1 + 2: remove BigQuery cases/results, retrying until clean.
    cases_deleted = 0
    results_deleted = 0
    attempts = 0
    while True:
        attempt_err = None
        for _ in range(3):
            cases_deleted, results_deleted, attempt_err = (
                writer.delete_cases_for_patient(patient_id, facility_id)
            )
            if attempt_err is None:
                break
        attempts += 1
        if attempt_err is None:
            break
        if attempts >= 3:
            raise HTTPException(
                status_code=503,
                detail="Erasure could not complete (analytics store unavailable). "
                "No patient row was deleted; please retry.",
            )

    # Step 3: delete the patient row from the Cloud SQL store.
    removed = store.delete_patient(patient_id, facility_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Step 4: record the PII-free erasure event (never deleted).
    store.record_erasure(
        ErasureLogRow(
            erasure_id=new_erasure_id(),
            patient_id=patient_id,
            requested_by=user.uid or "local",
            facility_id=facility_id,
            erased_at=datetime.now(UTC).isoformat(),
            cases_deleted=cases_deleted,
            results_deleted=results_deleted,
        )
    )

    logger.info(
        "patients.erased",
        extra={
            "event": "patients.erased",
            "patient_id": patient_id,
            "facility_id": facility_id,
            "requested_by": user.uid,
            "cases_deleted": cases_deleted,
            "results_deleted": results_deleted,
        },
    )
    return {
        "erased_patient_id": patient_id,
        "cases_deleted": cases_deleted,
        "results_deleted": results_deleted,
        "erasure_recorded": True,
    }


@router.get("/admin/erasure-log")
def erasure_events(
    limit: int = Query(50, ge=1, le=200),
    user: UserContext = Depends(require_any_role(ROLE_FACILITY_ADMIN, ROLE_SURGEON)),
    _rate: None = Depends(rate_limit),
    store=Depends(get_patient_store),
    cfg=Depends(get_cfg),
) -> dict:
    """List facility-scoped erasure events (PII-free audit trail, US-7.5)."""
    facility_id = _facility_scope(user, cfg)
    events = store.list_erasure_events(facility_id, limit=limit)
    return {
        "facility_id": facility_id,
        "events": [
            {
                "erasure_id": e.erasure_id,
                "patient_id": e.patient_id,
                "requested_by": e.requested_by,
                "erased_at": e.erased_at,
                "cases_deleted": e.cases_deleted,
                "results_deleted": e.results_deleted,
            }
            for e in events
        ],
    }


def _patient_payload(row: PatientRow) -> dict:
    """Serialize a patient row for the API (never expose internal state)."""
    return {
        "patient_id": row.patient_id,
        "full_name": row.full_name,
        "date_of_birth": row.date_of_birth,
        "mrn": row.mrn,
        "phone": row.phone,
        "facility_id": row.facility_id,
        "created_at": row.created_at,
        "consent_captured_by": row.consent_captured_by,
        "consent_captured_at": row.consent_captured_at,
    }
