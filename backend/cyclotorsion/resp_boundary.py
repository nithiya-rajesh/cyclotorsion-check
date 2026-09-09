"""Central response & error-boundary policy (architecture review Major).

Ensures that **no internal detail ever reaches a client** regardless of which
handler produced it. The prior MED-3 fix sanitized one endpoint's write path;
this module makes the guarantee a framework-wide boundary:

  - ``sanitize_detail`` scrubs an error ``detail`` string of internal fragments
    (GCP resource names, SQL keywords/fragments, filesystem paths, etc.) and
    returns a stable generic message when anything suspicious is found.
  - ``register_exception_handlers`` installs a FastAPI ``HTTPException`` handler
    that applies ``sanitize_detail`` to every raised HTTP error's ``detail``, so
    a future handler that accidentally interpolates internal text is neutralized
    at the boundary instead of leaked.
  - ``RESPONSE_ALLOW_LIST`` documents the canonical field set clients may
    receive, making the API surface audit-friendly.

Design notes / tradeoffs:

  - HTTP errors raised with an intentional, user-facing message are almost always
    preserved (they do not match the internal-fragment signatures). The scrub is
    best-effort and conservative: when in doubt, replace with a generic message
    (fail closed on leakage).
  - FastAPI's own 422 validation errors have a structured ``detail`` (a list of
    dicts); we sanitize each string leaf defensively but keep the structure so
    the client can still render field errors.
  - Raw exception ``detail``s from ``StarletteHTTPException`` are sanitized too.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("cyclotorsion.resp_boundary")

# Fields clients are allowed to receive from the API (documented audit surface).
# Anything else in a response is a defect. Keep this tight; add fields only as a
# deliberate, reviewed decision.
RESPONSE_ALLOW_LIST = frozenset(
    {
        "angle_deg",
        "upright_landmark",
        "rotated_landmark",
        "passed_sanity_check",
        "sanity_flags",
        "warning",
        "test_id",
        "logging_warning",
        "logging_warning_code",
        # /stats
        "total_tests",
        "average_abs_angle_deg",
        "sanity_check_pass_rate",
        "scoped_by_facility_id",
        "analytics_warning",
        # /admin/*
        "uid",
        "email",
        "role",
        "facility_id",
        "approved",
        # infra / shared
        "status",
        "service",
    }
)

# Substrings that indicate an internal detail leaked. Conservative; prefer
# false-positive scrubbing (generic message) over leaking.
_INTERNAL_MARKERS = (
    "projects/",
    "SELECT ",
    "FROM ",
    "WHERE ",
    "INSERT INTO",
    "bigquery",
    "secretmanager",
    "gcloud",
    "cloudtrace",
    "storage.googleapis",
    "Traceback",
    'File "',
    "\\",
)


def _contains_internal_fragment(text: str) -> bool:
    lowered = text.lower()
    return any(m.lower() in lowered for m in _INTERNAL_MARKERS)


def _sanitize_leaf(value: Any) -> Any:
    """Recursively scrub strings in a 422-style structured detail."""
    if isinstance(value, str):
        if _contains_internal_fragment(value):
            return "Request could not be processed"
        return value
    if isinstance(value, list):
        return [_sanitize_leaf(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize_leaf(v) for k, v in value.items()}
    return value


def assert_allowed_fields(payload: Any, *, context: str = "") -> None:
    """Raise ``AssertionError`` if ``payload`` carries a field outside
    ``RESPONSE_ALLOW_LIST`` (staff review Minor: turns the allow-list from
    documentation into an enforced contract).

    Intended for test-time use against real endpoint response bodies — see
    ``tests/test_resp_boundary.py::test_success_responses_match_allow_list``.
    Recurses into lists (each item checked) and dict values that are
    themselves dicts/lists; scalar values are not inspected, only field names.
    """
    if isinstance(payload, dict):
        unexpected = set(payload) - RESPONSE_ALLOW_LIST
        assert not unexpected, (
            f"Response{f' ({context})' if context else ''} contains field(s) "
            f"not in RESPONSE_ALLOW_LIST: {sorted(unexpected)}. Add them to "
            "RESPONSE_ALLOW_LIST as a deliberate, reviewed decision if intended."
        )
        for value in payload.values():
            if isinstance(value, (dict, list)):
                assert_allowed_fields(value, context=context)
    elif isinstance(payload, list):
        for item in payload:
            assert_allowed_fields(item, context=context)


def sanitize_detail(detail: Any) -> Any:
    """Return a client-safe version of an error ``detail``.

    Strings that appear to embed internal fragments are replaced with a generic
    message. Structured (list/dict) details are scrubbed recursively.
    """
    return _sanitize_leaf(detail)


def _build_response(exc: HTTPException | StarletteHTTPException) -> JSONResponse:
    detail = sanitize_detail(exc.detail)
    if isinstance(detail, str) and _contains_internal_fragment(detail):
        detail = "Request could not be processed"
    headers = getattr(exc, "headers", None)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": detail},
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> FastAPI:
    """Install centralized, leak-preventing error handlers on ``app``.

    Call once at app construction. Both the FastAPI ``HTTPException`` and the
    Starlette base (so non-route / middleware errors are scrubbed too) are
    covered. RequestValidationError (422) is covered via the FastAPI handler.
    """

    @app.exception_handler(HTTPException)
    async def _http_handler(request, exc: HTTPException):
        return _build_response(exc)

    @app.exception_handler(StarletteHTTPException)
    async def _starlette_handler(request, exc: StarletteHTTPException):
        return _build_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": sanitize_detail(exc.errors())},
        )

    return app
