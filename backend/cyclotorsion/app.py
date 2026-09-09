"""FastAPI service: /health, /detect, /stats, /admin/*, /metrics.

Implements the REST surface (TDD Sections 2.1, 2.3, 5.2) with a deliberate
focus on the PRD's hard requirements:

  - US-1.1/US-1.2: accept exactly two images (upright, supine).
  - US-5.1: images are processed transiently and never persisted — the
    uploads are read into memory and any temp file is removed immediately.
  - US-2.3: bounded latency (retries handled by the detector, not here).
  - US-4.2/4.3: aggregate, de-identified stats with zero patient fields.

Authentication is wired via ``auth.current_approved_user`` (Firebase ID-token
verification + the PRD 8.2 admin-approval gate) on /detect and /stats. It is
gated by ``CC_AUTH_ENABLED``: disabled (default) for frictionless local dev,
enabled for production/pilot. When enabled, /stats is scoped per facility (TDD
5.2) and the /admin/* endpoints drive the facility-admin approval flow.
/health and /metrics remain open for uptime monitoring and scraping (TDD 5.5).

This module owns app construction, middleware, and the process-wide runtime
state (``_DETECTOR``, ``_WRITER``, ``_ANALYTICS_WORKER``); the actual routes
live in ``cyclotorsion.routers.*`` (staff review Minor: split out of a single
485-line file so each domain's diff surface stays small). Routers receive that
state as FastAPI dependencies (``Depends(get_writer)`` etc., defined below) —
real DI rather than reaching into this module's globals directly — while each
provider still reads the *current* module attribute at call time, so tests
that ``monkeypatch.setattr(app_mod, "_WRITER", ...)`` etc. continue to work
unchanged.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cyclotorsion.background import get_worker
from cyclotorsion.config import cfg
from cyclotorsion.detector import LandmarkDetector, detector_from_config
from cyclotorsion.logging_config import RequestIdMiddleware, configure_logging
from cyclotorsion.patients import PatientStore, patient_store_from_config
from cyclotorsion.resp_boundary import register_exception_handlers
from cyclotorsion.storage import ResultWriter, writer_from_config
from cyclotorsion.tracing import (
    get_tracer,  # noqa: F401 - re-exported for routers/tests
)

logger = logging.getLogger("cyclotorsion.app")

app = FastAPI(
    title="CyclotorsionCheck API",
    version="0.1.0",
    description="Toric IOL alignment verification via cyclotorsion measurement.",
)

# Central response/error boundary (architecture review Major): installs
# exception handlers on `app` that scrub HTTPException/validation details of any
# internal fragments before they reach a client. Register before routes run.
register_exception_handlers(app)

# Structured JSON logging with per-request correlation ids (TDD Section 6.2).
configure_logging()

# Production guard (HIGH-1 fix): if this is a production deployment but auth is
# disabled, emit an immediately-visible CRITICAL so operators notice the
# misconfiguration. The auth layer additionally fails closed per-request (401).
if cfg.is_production and not cfg.auth_enabled:
    logger.critical(
        "app.production_without_auth",
        extra={"event": "app.production_without_auth"},
    )

# Production CORS guard (architecture review Major): if this is production and
# the operator left CC_CORS_ORIGINS unset, cors_origin_list is empty (fail
# closed) — no credentialed cross-origin requests are permitted. Log CRITICAL so
# the misconfig is visible, since the SPA's own page must be added explicitly.
if cfg.is_production and not cfg.cors_origins:
    logger.critical(
        "app.production_cors_empty",
        extra={"event": "app.production_cors_empty"},
    )

# RequestIdMiddleware must be registered before CORS so the request_id is set
# as early as possible in the middleware chain (every later log line carries it).
app.add_middleware(RequestIdMiddleware)

# CORS: tightened from the prototype's wide-open "*" (TDD Section 5.5).
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Runtime dependencies resolved once from the environment (config.Config).
_DETECTOR: LandmarkDetector = detector_from_config()
_WRITER: ResultWriter = writer_from_config()
_PATIENT_STORE: PatientStore = patient_store_from_config()

# Bounded background worker for the off-hot-path analytics write (architecture
# review Major). Submitting never blocks; a full bounded queue drops the job and
# logs instead of growing memory or stalling the request. Tests may swap this for
# an isolated worker and call ``flush_analytics`` to observe writes deterministically.
_ANALYTICS_WORKER = get_worker()


def flush_analytics(timeout: float | None = 10.0) -> None:
    """Drain pending analytics writes to completion.

    Used by tests (to make read-after-write deterministic) and on shutdown
    (registered below) so in-flight rows are not lost. Never called on the
    hot path.
    """
    _ANALYTICS_WORKER.flush(timeout)


@app.on_event("shutdown")
def _flush_analytics_on_shutdown() -> None:
    """Drain the background analytics queue before the process exits.

    Without this, a platform that tears down idle instances between requests
    (Cloud Run with min-instances=0, in particular) can kill the container
    while the /detect handler's off-hot-path BigQuery write is still enqueued
    or in flight — the clinical result already reached the surgeon (the write
    is deliberately decoupled from the response, Section 2.1), but the
    analytics row is silently lost. ``flush_analytics`` existed for exactly
    this purpose but was never wired to a shutdown hook — confirmed missing
    by observing a real write vanish against a live Cloud Run deployment.
    Bounded by SIGTERM's own grace period; a still-pending write after the
    timeout is dropped, same as a full queue (best-effort by design).
    """
    flush_analytics()


# FastAPI dependency providers for the runtime state above (staff review:
# routers previously reached into this module's globals directly via
# `import cyclotorsion.app as app_state; app_state._WRITER` — a Service
# Locator-flavored read rather than real dependency injection). Routes now
# declare these as `Depends(...)` parameters instead. Each still reads the
# *current* module attribute at call time — same as before — so tests that
# `monkeypatch.setattr(app_mod, "_WRITER", ...)` etc. continue to work
# unchanged; only how a route *receives* the value changed, not how tests
# override it.
def get_writer() -> ResultWriter:
    return _WRITER


def get_patient_store() -> PatientStore:
    return _PATIENT_STORE


def get_detector() -> LandmarkDetector:
    return _DETECTOR


def get_analytics_worker():
    return _ANALYTICS_WORKER


def get_cfg():
    return cfg


# Routes live in per-domain routers; imported after the globals above exist
# because each router reads them (at call time) via `cyclotorsion.app`.
from cyclotorsion.routers import admin, detect, health, patients, stats  # noqa: E402

app.include_router(health.router)
app.include_router(detect.router)
app.include_router(stats.router)
app.include_router(admin.router)
app.include_router(patients.router)

# Backward-compatible re-exports: several tests and the OpenAPI-adjacent
# tooling import these names from `cyclotorsion.app` directly. Kept as aliases
# so the router split is a pure refactor (staff review Minor), not an API break.
MAX_IMAGE_BYTES = detect.MAX_IMAGE_BYTES
MAX_IMAGE_PIXELS = detect.MAX_IMAGE_PIXELS
ALLOWED_MIME_TYPES = detect.ALLOWED_MIME_TYPES
MISSING_SLOT_MESSAGE = detect.MISSING_SLOT_MESSAGE
