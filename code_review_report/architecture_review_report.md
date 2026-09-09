# 🏛️ Architecture Review Report — CyclotorsionCheck

**Reviewer role:** Staff Software Engineer / Principal Code Architect  
**Scope:** Full repository — backend (FastAPI/Python), frontend (vanilla JS SPA), IaC (Terraform/Shell), scripts, docs.  
**Method:** Exhaustive, file-by-file audit against DDD, Hexagonal/Ports-&-Adapters, SOLID, and Clean Code principles.

---

## Executive Summary

This codebase is **well above the median for a real-world medical decision-support service**. There is genuine architectural intent here: a clean **Ports & Adapters** core (`LandmarkDetector`, `ResultWriter`, `PatientStore`, `ApprovalStore` ABCs with multiple backends), pure-domain geometry/sanity engines, a sound `Config` boundary, and disciplined observability. The domain is thoughtfully decomposed, and the security posturing (fail-closed auth, response allow-listing, PII hygiene) is production-grade.

The most striking observations are the **two opposing quality slopes**:

- **Excellent** pure-domain modules: `geometry.py`, `sanity.py`, `pipeline.py`, `circuit_breaker.py`, `resp_boundary.py`, `logging_config.py`.
- **Critical debt** where that discipline is not consistently applied: **application/use-case logic leaking into HTTP routers** (`routers/patients.py`, `routers/detect.py`, `routers/admin.py`), **HTTP exceptions raised from what should be domain validation** (`provisioning.py`), a **fat `api.js` transport layer** (major DRY violation), weak request typing (`body: dict`), and — critically — **two broken/unverifiable Terraform files** that would prevent the Cloud SQL patient store (PRD Epic 7) from every deploying correctly.

The single highest-leverage recommendation: **introduce an Application/Use-Case layer (Domain Services) as a true hexagon core**, with routers reduced to thin transport adapters. Section 3 blueprints the three most critical rewrites.

---

# 1. 📊 Exhaustive File-by-File Coverage Index & Scorecard

*Legend: ★ Excellent · ◐ Acceptable · ✖ Critical Debt*

### Backend — `backend/cyclotorsion/`

| File Path & Name | DDD/Arch Grade | Clean Code/SOLID | Primary Structural Finding |
|---|---|---|---|
| `backend/cyclotorsion/__init__.py` | ★ Excellent | ★ Excellent | A model-minimal package init with a clear domain docstring and no re-export bloat. |
| `backend/cyclotorsion/__main__.py` | ★ Excellent | ★ Excellent | Correct, minimal uvicorn entry point; explicit `reload=False`, dev-only `127.0.0.1` binding. |
| `backend/cyclotorsion/app.py` | ◐ Acceptable | ◐ Acceptable | Composition root doubles as a **Service-Locator-flavored** state holder (`_DETECTOR`/`_WRITER`/`_PATIENT_STORE`) with thin `get_*` providers; DI is partial, and `app` is imported back into `routers/__init__` to read module state at call time (fine for tests, but a mutable-global code smell). |
| `backend/cyclotorsion/config.py` | ◐ Acceptable | ◐ Acceptable | Clean frozen `Config` dataclass — a rational config-boundary; the `use_bigquery` import-probe and `is_production` heuristic are pragmatic, if implicit. |
| `backend/cyclotorsion/detector.py` | ★ Excellent | ★ Excellent | Textbook **Port/Adapter**: `LandmarkDetector` ABC with `Mock`/`Gemini` adapters and a config factory; retry + breaker isolated behind the port. |
| `backend/cyclotorsion/geometry.py` | ★ Excellent | ★ Excellent | Pure domain Value Object (`Point`) + pure functions; zero I/O, auditable, deterministic — ideal DDD core. |
| `backend/cyclotorsion/sanity.py` | ★ Excellent | ★ Excellent | Pure, side-effect-free rule engine; frozen result object; single source of warning copy. |
| `backend/cyclotorsion/pipeline.py` | ★ Excellent | ★ Excellent | Clean **Domain Service** composing detector → geometry → sanity; `to_client_dict` is a defensible response boundary. Minor: `Any` return type could be a `TypedDict`. |
| `backend/cyclotorsion/storage.py` | ★ Excellent | ◐ Acceptable | Strong `ResultWriter` port with two adapters; `ResultRow`/`CaseRow` are correct de-identified value objects. InMemory writer's `write_row`/`upsert_case` return bare `None` while the ABC declares `Exception | None` (LSP typing drift); `read_rows` etc. have awkward `(rows, error)` tuple APIs. |
| `backend/cyclotorsion/patients.py` | ★ Excellent | ◐ Acceptable | Excellent `PatientStore` port + `InMemory`/`Postgres` adapters; facility scoping and erasure-log are well modeled. **Anemic `PatientRow`/`ErasureLogRow`** (pure data bags — acceptable for a row projection, but identity validation is duplicated in the router rather than in a Value Object). |
| `backend/cyclotorsion/auth.py` | ◐ Acceptable | ◐ Acceptable | Correct Firebase verification + RBAC dependencies; conflates SDK lifecycle, token transport, and FastAPI dependency wiring in one file; `UserContext.role` typed `str` instead of the existing `Role` enum; dead `is_pending_approval` module function. |
| `backend/cyclotorsion/background.py` | ★ Excellent | ◐ Acceptable | Correct bounded background worker for off-hot-path telemetry; **`flush(timeout)` ignores its own `timeout` param** and blocks indefinitely on `join()`. |
| `backend/cyclotorsion/concurrency.py` | ◐ Acceptable | ◐ Acceptable | Correct per-user semaphore guard; `key_from_request` is **duplicated verbatim in `rate_limit.py`** (DRY); `_sems` dict grows unboundedly with no eviction. |
| `backend/cyclotorsion/circuit_breaker.py` | ★ Excellent | ★ Excellent | Clean, thread-safe, clock-injected three-state breaker; properly isolated infra. |
| `backend/cyclotorsion/metrics.py` | ◐ Acceptable | ◐ Acceptable | Well-scoped no-dep Prometheus registry; `make_counter`/`make_histogram` structurally duplicated; `# type: ignore[return-value]` masks a genuine `TypeVar` opportunity. |
| `backend/cyclotorsion/tracing.py` | ◐ Acceptable | ◐ Acceptable | Sound contextvar-based tracing + deferred Cloud Trace export; `_to_attributes` can `KeyError` on unknown keys and import fails unguarded without the GCP SDK; `id()`-derived span ids are non-deterministic. |
| `backend/cyclotorsion/provisioning.py` | ◐ Acceptable | ✖ Critical Debt | **Domain/validation layer raises `HTTPException`** (`_scope_facility`, `_ensure_valid_role`, `_validate_uid`, `_validate_facility_id`); imports private `_ensure_firebase_app`; `_store` singleton not thread-safe. |
| `backend/cyclotorsion/rate_limit.py` | ◐ Acceptable | ◐ Acceptable | Correct in-memory token bucket; duplicates `key_from_request` (DRY); unbounded bucket store. |
| `backend/cyclotorsion/resp_boundary.py` | ★ Excellent | ◐ Acceptable | Strong response-field allow-list + error scrubbing as a cross-cutting boundary; redundant double-scrub in `_build_response`; overly broad internal markers. |
| `backend/cyclotorsion/secrets.py` | ◐ Acceptable | ◐ Acceptable | Clean Secret Manager resolution with lazy SDK import; module-level `_cache` not thread-safe; silent empty-key degradation documented. |
| `backend/cyclotorsion/text.py` | ◐ Acceptable | ★ Excellent | Pure sanitize/PII helpers; `sanitize_text` accepts `None` but signature says `str` (weak typing); aggressive strip of `<`/`>`. |
| `backend/cyclotorsion/logging_config.py` | ★ Excellent | ★ Excellent | Correct contextvar request-id correlation middleware + superset JSON formatter; private-attribute handler tagging is fragile but harmless. |

### Backend — `backend/cyclotorsion/routers/`

| File Path & Name | DDD/Arch Grade | Clean Code/SOLID | Primary Structural Finding |
|---|---|---|---|
| `backend/cyclotorsion/routers/__init__.py` | ◐ Acceptable | ★ Excellent | Useful package docstring rationalizing call-time module state reads; its documented pattern (module import) is **not what the child routers actually do** (they use function imports) — doc drift. |
| `backend/cyclotorsion/routers/health.py` | ★ Excellent | ★ Excellent | Minimal, correct, deliberately-open infra endpoints; no violations. |
| `backend/cyclotorsion/routers/detect.py` | ◐ Acceptable | ◐ Acceptable | **The `/detect` handler is a god-handler**: input validation, patient validation, detection orchestration, PII nudge, analytics enqueue, case upsert, and response shaping all in one ~180-line function (SRP/OCP). The core is properly delegated to `run_detection`, so the leak is orchestration, not math. |
| `backend/cyclotorsion/routers/patients.py` | ✖ Critical Debt | ✖ Critical Debt | **The cascading right-to-erasure transaction (US-7.5) is orchestrated entirely inside the HTTP handler** — a compliance-critical, cross-database, retry-to-completion workflow embedded in the controller. Also `create_patient(body: dict)` weak typing, and all facility/validation/payload logic lives in the router. |
| `backend/cyclotorsion/routers/stats.py` | ◐ Acceptable | ◐ Acceptable | Correct RBAC-before-I/O; manual role check duplicates `require_any_role` (DRY); aggregation math lives in the handler instead of a domain service. |
| `backend/cyclotorsion/routers/admin.py` | ◐ Acceptable | ✖ Critical Debt | `admin_approve(body: dict[str, Any])` — **no Pydantic request model** (canonical FastAPI anti-pattern: no validation, no OpenAPI contract, no IDE safety); imports private `provisioning._*` helpers; mixes HTTP + domain validation in the router. |

### Backend — `backend/tests/` (test suite)

| File | DDD/Arch | SOLID | Finding |
|---|---|---|---|
| `test_api.py`, `test_auth.py`, `test_background.py`, `test_backup.py`, `test_circuit_breaker.py`, `test_concurrency.py`, `test_config.py`, `test_detector.py`, `test_domain.py`, `test_facility.py`, `test_geometry.py`, `test_integration.py`, `test_logging.py`, `test_metrics.py`, `test_patients.py`, `test_pipeline.py`, `test_provisioning.py`, `test_rate_limit.py`, `test_resp_boundary.py`, `test_sanity.py`, `test_secrets.py`, `test_storage.py`, `test_text.py`, `test_tracing.py` | ★ Excellent | ★ Excellent | A comprehensive, well-factored test suite (247 passing, 92% coverage gate). Tests are named by domain and assert behavior, not implementation internals — a sign the hexagon core is genuinely testable. This suite is the safety net that makes the Section 3 refactors *safe* to perform. |

### Frontend — `frontend/js/`

| File | DDD/Arch | SOLID | Finding |
|---|---|---|---|
| `frontend/js/config.js` | ◐ Acceptable | ◐ Acceptable | Config bag reading `window` at module load; carries a **committed, real-looking Firebase API key + project config** (web-config is public by Firebase design, but a committed key/config in source is a hygiene concern to rotate to env-injection). |
| `frontend/js/api.js` | ◐ Acceptable | ✖ Critical Debt | **Severe DRY violation**: seven near-identical `fetch`+error-classification wrappers (`detect`, `fetchStats`, `searchPatients`, `createPatient`, `fetchPatient`, `fetchPatientCases`, `erasePatient`, `fetchErasureLog`) with triplicated try/catch and status branching. Begs for a single `requestJson()` helper. |
| `frontend/js/auth.js` | ◐ Acceptable | ◐ Acceptable | Clean, but hardcodes a remote CDN Firebase SDK version (`10.14.1`) and re-imports `firebase-auth` dynamically on every call; no local pin (supply-chain + version-drift risk). |
| `frontend/js/common.js` | ◐ Acceptable | ★ Excellent | Clean `el()` DOM helper, image-quality heuristic, and PII mirror; client `looks_like_pii` duplicates backend logic (cross-boundary — defensible as defense-in-depth). |
| `frontend/js/session.js` | ★ Excellent | ★ Excellent | Minimal, correct, session-scoped in-memory store. |
| `frontend/js/router.js` | ★ Excellent | ★ Excellent | Clean hash router with auth gating; now also thread a subpath for patient deep links. |
| `frontend/js/pages/analyze.js` | ★ Excellent | ★ Excellent | Good presenter/Porter — delegates math to the backend, holds only capture-slot state; patient-attach wiring is clean. |
| `frontend/js/pages/patients.js` | ◐ Acceptable | ◐ Acceptable | Now a real `<form>` with consent gate (US-7.4) + deep-link load; remains a large page module but reads as presentation-only. |
| `frontend/js/pages/history.js` | ★ Excellent | ★ Excellent | Minimal, session-scoped table view. |
| `frontend/js/pages/insights.js` | ★ Excellent | ★ Excellent | Clean presenter with graceful error/empty states. |
| `frontend/js/pages/result.js` | ◐ Acceptable | ★ Excellent | Clean formatter, but **circular import with `analyze.js`** (`result` ↔ `analyze` import each other). |
| `frontend/js/pages/login.js` | ★ Excellent | ★ Excellent | Clean, accessible `<form>` with label/autocomplete/Enter-submit. |

### Frontend — config/assets

| File | Arch | SOLID | Finding |
|---|---|---|---|
| `frontend/index.html` | ★ Excellent | ★ Excellent | Accessible shell: skip link, semantic nav, per-route views. |
| `frontend/css/styles.css` | ◐ Acceptable | ★ Excellent | Cohesive design system with a11y hardening (focus-visible, hit targets, 16px mobile inputs). |
| `frontend/package.json`, `eslint.config.js`, `README.md` | ★ Excellent | ★ Excellent | Zero-runtime-dependency SPA is a deliberate, well-documented choice; clean ESLint + happy-dom test setup. |
| `frontend/test/*.test.js` (5 files) | ★ Excellent | ★ Excellent | Node `mock()`-based tests incl. patients form/consent/deep-link coverage. |

### Infrastructure — `infra/`

| File | Arch | SOLID | Finding |
|---|---|---|---|
| `infra/terraform/main.tf` | ★ Excellent | ★ Excellent | Strong policy-as-code `check` blocks (residency, ingress, CMEK, single-replica); unused `google-beta` provider is noise. |
| `infra/terraform/variables.tf` | ★ Excellent | ◐ Acceptable | Well-typed/documented variables; unused `shutdown_hook` placeholder is dead code. |
| `infra/terraform/bigquery.tf` | ★ Excellent | ★ Excellent | CMEK-gated, versioned, least-privilege; **missing the Epic-6 `cases` table + `case_ref`/`patient_id` columns** (schema drift vs. the writer that writes them). |
| `infra/terraform/cloudrun.tf` | ◐ Acceptable | ◐ Acceptable | **The `GEMINI_API_KEY` `env` block with `secret_key_ref` sits outside the `containers{}` block** (Cloud Run v2 schema requires it inside `containers`) — likely a plan-time error or silent misconfig; `aiplatform.user` granted unconditionally. |
| `infra/terraform/backup.tf` | ★ Excellent | ★ Excellent | Clean, focused backup job; `objectAdmin` slightly over-scoped vs. `objectCreator`. |
| `infra/terraform/secretmanager.tf` | ★ Excellent | ◐ Acceptable | Secret-manager-gated secrets; automatic replication not region-pinned for India residency. |
| `infra/terraform/monitoring.tf` | ◐ Acceptable | ★ Excellent | Good alert wiring; latency/quota alerts left as comments (operational gap). |
| `infra/terraform/outputs.tf` | ★ Excellent | ★ Excellent | Minimal, useful. |
| `infra/terraform/cloudsql.tf` | ✖ Critical Debt | ✖ Critical Debt | **Dangling reference bug**: `google_compute_network.patients_vpc[0].id` (line 52) references a resource defined nowhere in the repo → `terraform plan` fails whenever `cloud_sql_public_ip=false` (the recommended production path). Also: unused `cloud_sql_enabled` var, `0.0.0.0/0` free public access when public IP is on, `deletion_protection=false` on a PII database. |
| `infra/gcloud/deploy.sh` | ◐ Acceptable | ◐ Acceptable | Parallel IaC path that can drift from Terraform; `|| true` swallows real errors; single-replica guard not applied to `MIN_INSTANCES`. |
| `infra/gcloud/alerts.sh` | ◐ Acceptable | ◐ Acceptable | Inconsistent curl-vs-gcloud API surface; `gcloud alpha` instability; errors swallowed. |
| `infra/gcloud/scheduler.sh` | ◐ Acceptable | ◐ Acceptable | Create-then-update fallback duplicates the entire resource spec (DRY). |
| `infra/README.md` | ★ Excellent | ★ Excellent | Exceptional, honest infra docs — but omits the Cloud SQL private-IP breakage. |

### Scripts & Config

| File | Arch | SOLID | Finding |
|---|---|---|---|
| `scripts/backup_export.py` | ★ Excellent | ★ Excellent | Clean operational export with dry-run; fragile `sys.path` import of app logging. |
| `scripts/evaluate_accuracy.py` | ★ Excellent | ◐ Acceptable | Solid benchmark; dead `* 0.0` no-op term and `sys.path`-based peer import. |
| `scripts/generate_synthetic_data.py` | ★ Excellent | ◐ Acceptable | Well-scoped data generator; `random` unseeded → non-deterministic outputs; `import io` inside function. |
| `scripts/smoke_api.py` | ★ Excellent | ◐ Acceptable | Practical end-to-end smoke; single-element tuple `in (200,)` and unusual `scripts.` import. |
| `pytest.ini` | ★ Excellent | ★ Excellent | 90% coverage gate; test path + pythonpath configured. |
| `ruff.toml` | ★ Excellent | ★ Excellent | Curated lint rules; `py314` target is aggressive for deploy runtimes. |
| `firebase.json` | ◐ Acceptable | ◐ Acceptable | Good CSP/HSTS headers; dev `connect-src` origins + `*.run.app` wildcard need narrowing for production. |
| `docs/PRD_CyclotorsionCheck.md`, `docs/TDD_CyclotorsionCheck.md` | ★ Excellent | ★ Excellent | Enterprise-grade, traceable PRD/TDD; the true spec of record. |

---

# 2. 🔍 Deep-Dive Violations & Blast Radius Analysis

### Violation A — Cascading right-to-erasure orchestrated inside an HTTP router
**Location:** `backend/cyclotorsion/routers/patients.py`, `erase_patient`, lines 250–329 (retry-to-completion loop, lines 274–293).
**Principle violated:** SRP, DIP, Hexagonal layering — a **compliance-critical domain transaction** (cross-database cascade with retry-to-completion, DPDP-mandated) lives as inline orchestration in a transport adapter.
**Blast radius:** (1) **Untestable in isolation** — the transaction is only exercisable through HTTP, so an operator/CLI/scheduled erasure job cannot reuse it and the compliance workflow cannot be unit-tested without FastAPI mocks. (2) **Tight coupling** — the router now depends on *both* `PatientStore` and `ResultWriter` plus the retry policy; any change to erasure semantics (idempotency, resume-on-failure, audit enrichment) is an HTTP-file change causing a redundant deploy of the whole API surface. (3) **No idempotency/resume** — if a crash happens between BigQuery DELETE and Cloud SQL DELETE, the router has no persisted document-of-record to resume from; a later 404 makes the operator believe erasure never happened (false negative in a compliance audit).

### Violation B — Injectable HTTP exceptions raised from domain/validation layer
**Location:** `backend/cyclotorsion/provisioning.py`, `_scope_facility` (line 98), `_ensure_valid_role` (61), `_validate_uid` (77), `_validate_facility_id` (87).
**Principle violated:** Dependency Rule / Hexagonal — domain validation concerns raise a **web-framework exception type** (`fastapi.HTTPException`).
**Blast radius:** (1) The core is no longer web-framework-free; it cannot be ported to a CLI, queue consumer, or test harness without importing FastAPI. (2) Every caller must import `HTTPException` to reason about validation, leaking transport semantics upward/downward. (3) Breaks the "swap the adapter without touching core" guarantee that the rest of the codebase achieves so well.

### Violation C — God-handler `/detect` + fat routers with weak request typing
**Location:** `backend/cyclotorsion/routers/detect.py`, `detect` (lines 245–424); `routers/patients.py` `create_patient(body: dict)`; `routers/admin.py` `admin_approve(body: dict[str, Any])`.
**Principle violated:** SRP, OCP, ISP — controllers accumulate validation+nudging+orchestration; request bodies are untyped dictionaries (no Pydantic models → no OpenAPI contract, no compile-time safety).
**Blast radius:** (1) A single concern (e.g., "raise a PII nudge") couples to the entire detection transactional path, so a small change re-qualifies a large surface. (2) Untyped `body: dict` means silent key-typos reach production instead of being rejected at the boundary; the OpenAPI spec is degraded for front-end/CLI consumers. (3) New intake channels (a second clinic client, an EMR integration) cannot reuse the use case without duplicating the router body.

### Violation D — DRY collapse of the frontend transport
**Location:** `frontend/js/api.js`, all exported functions (lines 41–301).
**Principle violated:** DRY — the same `fetch` + abort/timeout + error-classification boilerplate is written ~8 times.
**Blast radius:** Every endpoint's error handling must be kept in lock-step; a fix to one (e.g., honoring `detail` on all `server` errors, or a retry/backoff) must be manually replicated across all eight. Realistic failure: `createPatient` handles 409 specially, others don't — a subtle divergence that already exists.

### Violation E — Infrastructure: broken patient-store deploy blok (CRITICAL)
**Location:** `infra/terraform/cloudsql.tf` line 52 & `infra/terraform/cloudrun.tf` lines 116–129.
**Principle violated:** Correctness/Operability — the two files that provision the Epic-7 patient store are not deployable as written.
**Blast radius:** Cloud SQL private-IP mode (the README's recommended production path) fails `terraform plan` with `Reference to undeclared resource`. The Gemini secret env block is outside `containers{}`, so the secret-mounted env either fails validation or is silently unset in the running container — meaning **production auth/detection keys may never be injected**. Both block the pilot onboarding of real patient data.

### Violation F — Anemic rows / LSP typing drift in storage
**Location:** `backend/cyclotorsion/storage.py` (`InMemoryWriter.write_row`/`upsert_case` return `None`; ABC declares `Exception | None`), `backend/cyclotorsion/patients.py` (`PatientRow` pure data bag).
**Principle violated:** LSP (covariant return drift), Anemic Domain Model.
**Blast radius:** LSP drift means a backend can silently behave differently; today benign because callers check `is not None` uniformly, but it invites a future adapter that raises. The duplicated MRN-uniqueness/validation logic lives in the router rather than the row/Value Object, so rules can drift across call paths.

---

# 3. 🛠️ Before-and-After Refactoring Blueprint

## Blueprint #1 — Extract the right-to-erasure into a Domain Service (Hexagonal core)

### `// BEFORE:` — transport-embedded compliance workflow (`routers/patients.py`)
```python
@router.delete("/patients/{patient_id}", status_code=200)
def erase_patient(
    patient_id: str,
    user: UserContext = Depends(require_any_role(ROLE_FACILITY_ADMIN)),
    _rate: None = Depends(rate_limit),
    store=Depends(get_patient_store),
    writer=Depends(get_writer),
    cfg=Depends(get_cfg),
) -> dict:
    facility_id = _facility_scope(user, cfg)
    row = store.get_patient(patient_id, facility_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    cases_deleted = results_deleted = 0
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
            raise HTTPException(status_code=503, detail="Erasure could not complete...")

    removed = store.delete_patient(patient_id, facility_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    store.record_erasure(ErasureLogRow(...))
    return {...}
```

### `// AFTER:` — domain service + thin adapter (open for reuse by CLI/scheduler/tests)
```python
# core/application/right_to_erasure.py  (new hexagon core — no framework imports)
class FacilityMismatchError(Exception): ...
class PatientNotFoundError(Exception): ...
class ErasureUnavailableError(Exception): ...

class RightToErasureService:
    """Cross-database, retry-to-completion, DPDP-mandated domain transaction."""
    def __init__(self, patient_store: PatientStore, result_writer: ResultWriter,
                 retry: int = 3, attempts: int = 3) -> None:
        self._patients = patient_store
        self._writer = result_writer
        self._retry, self._attempts = retry, attempts

    def erase(self, patient_id: str, facility_id: str, requested_by: str,
              id_factory=uuid.uuid4, now=datetime.now) -> ErasureSummary:
        if self._patients.get_patient(patient_id, facility_id) is None:
            raise PatientNotFoundError(patient_id)
        result = self._delete_to_completion(patient_id, facility_id)
        removed = self._patients.delete_patient(patient_id, facility_id)
        if removed is None:                      # lost update across stores
            raise PatientNotFoundError(patient_id)
        self._patients.record_erasure(ErasureLogRow(
            erasure_id=str(id_factory()), patient_id=patient_id,
            requested_by=requested_by, facility_id=facility_id,
            erased_at=now(UTC).isoformat(),
            cases_deleted=result.cases, results_deleted=result.results))
        return ErasureSummary(True, result.cases, result.results)

    def _delete_to_completion(self, patient_id, facility_id) -> _Counts:
        for _ in range(self._attempts):
            for _ in range(self._retry):
                cases, results, err = self._writer.delete_cases_for_patient(
                    patient_id, facility_id)
                if err is None:
                    return _Counts(cases, results)
        raise ErasureUnavailableError(patient_id)

# routers/patients.py  -> transport adapter only (framework concerns)
@router.delete("/patients/{patient_id}", status_code=200)
def erase_patient(patient_id: str,
                  user: UserContext = Depends(require_any_role(ROLE_FACILITY_ADMIN)),
                  _rate: None = Depends(rate_limit),
                  cfg=Depends(get_cfg)) -> dict:
    try:
        summary = _erasure_service.erase(patient_id, _facility_scope(user, cfg), user.uid)
    except PatientNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Patient not found") from exc
    except ErasureUnavailableError as exc:
        raise HTTPException(status_code=503,
                            detail="Erasure could not complete; retry.") from exc
    return {"erased_patient_id": patient_id, **summary.model_dump(exclude={"succeeded"})}
```
**Why it's better:** the transaction becomes a framework-free, unit-testable Domain Service; the retry policy is injectable; a CLI/scheduler job and the HTTP adapter share one implementation; the router shrinks to exception-mapping only.

## Blueprint #2 — Tame the `/detect` god-handler with a typed use-case object (CQS)

### `// BEFORE:` — everything in one response-returning handler
```python
@router.post("/detect")
async def detect(request: Request,
                 upright: UploadFile = File(...), rotated: UploadFile = File(...),
                 case_ref: str | None = Form(None), ...,
                 user: UserContext = Depends(current_approved_user), ...) -> dict[str, Any]:
    upright_bytes = _validate_and_read(upright, "upright")
    ...  # validations, patient check, tracer, acquire, run_detection, enqueue, shape response
```

### `// AFTER:` — explicit command model + application service + slim controller
```python
# schemas.py (Pydantic) — typed contract, OpenAPI, boundary validation
class DetectCommand(BaseModel):
    upright: UploadFile
    rotated: UploadFile
    case_ref: str | None = Field(default=None, max_length=80)
    eye_laterality: Literal["OD", "OS"] | None = None
    target_axis_deg: Annotated[float | None, Field(ge=0, lt=180)] = None
    patient_id: UUID | None = None

# application/detect_use_case.py — orchestration, framework-free
class DetectUseCase:
    def __init__(self, detector, writer, patient_store, worker, tracer): ...
    def execute(self, cmd: DetectCommand, ctx: ClinicalContext) -> DetectResult:
        if cmd.patient_id is not None and \
           self._patient_store.get_patient(str(cmd.patient_id), ctx.facility_id) is None:
            raise PatientNotInFacilityError(cmd.patient_id)
        outcome = run_detection(self._detector, cmd.upright.read(), ...)
        self._enqueue_analytics(outcome, cmd, ctx)     # bounded worker stays off hot path
        return DetectResult.from_outcome(outcome, cmd)

# routers/detect.py — thin transport adapter
@router.post("/detect")
async def detect(cmd: Annotated[DetectCommand, Form()],
                 user: UserContext = Depends(current_approved_user),
                 use_case: DetectUseCase = Depends(get_detect_use_case)) -> dict[str, Any]:
    try:
        return (await use_case.execute(cmd, _clinical_context(user))).to_client_dict()
    except PatientNotInFacilityError as exc:
        raise HTTPException(status_code=404,
                            detail="patient_id does not exist in this facility") from exc
```
**Why it's better:** typed request models enforce the contract at the boundary, the retry/concurrency/tracer concerns move into a testable application service, and `locate raw `UploadFile`/`Form` plumbing is confined to the adapter. The handler is now a ~10-line mapping function (OCP: new intake channels reuse `DetectUseCase`).

## Blueprint #3 — Remove framework exceptions from the domain (Port/Adapter boundary)

### `// BEFORE:` — domain raises HTTPException
```python
# provisioning.py
def _scope_facility(user, cfg):
    if cfg.auth_enabled and not user.facility_id:
        raise HTTPException(status_code=403, detail="Authenticated caller has no facility scope")
    return user.facility_id or DEFAULT_DEV_FACILITY
```

### `// AFTER:` — domain raises domain exceptions; adapter maps them
```python
# core/domain/authorization.py
class UnauthorizedError(Exception): ...
class NoFacilityScopeError(UnauthorizedError): ...
class InvalidRoleError(UnauthorizedError): ...
class InvalidFacilityError(UnauthorizedError): ...

# core/application/commander.py (pure)
def resolve_facility_scope(user: Principal, cfg: EnvPolicy) -> str:
    if cfg.auth_enabled and not user.facility_id:
        raise NoFacilityScopeError(user.uid)
    return user.facility_id or DEFAULT_DEV_FACILITY

# routers/patients.py  OR  a shared fastapi dependency (adapter layer)
from cyclotorsion.core.application import resolve_facility_scope

def get_facility_scope(user: UserContext = Depends(current_user),
                       cfg=Depends(get_cfg)) -> str:
    try:
        return resolve_facility_scope(user, cfg)
    except NoFacilityScopeError as exc:
        raise HTTPException(status_code=403,
                            detail="Authenticated caller has no facility scope") from exc
```
**Why it's better:** the core raises only domain exceptions, so it stays importable everywhere (CLI, tests, EMR job) without a web framework; the single `get_facility_scope` FastAPI dependency centralizes the HTTP mapping; `stats.py`, `admin.py`, and `patients.py` all stop re-implementing the mapping.

---

# 4. 🚀 Phased Remediation Roadmap

### Phase 1 — Immediate / Highest-Impact Actions (do first; unblocks everything)
- [ ] **Fix the `cloudsql.tf` dangling reference (CRITICAL).** Define `google_compute_network.patients_vpc` (+ a Serverless VPC connector) gated on `cloud_sql_public_ip == false`, or drop the private-IP branch entirely and force public-IP + a narrow `authorized_networks` allow-list behind a `check`. Add a `check` forbidding `use_cmek`/`enforce_india_residency` combined with `0.0.0.0/0` or `deletion_protection=false`.
- [ ] **Fix the `cloudrun.tf` Gemini secret env block.** Move the `value_source.secret_key_ref` `env` block inside the `containers{}` block (line 116–129) so the key is actually injected; make the `aiplatform.user` IAM conditional on `GEMINI_VERTEX_LOCATION`.
- [ ] **Add the missing BigQuery `cases` table + `case_ref`/`patient_id` columns** to `bigquery.tf` to match the `ResultWriter`/epic-6/7 writer contract — schema drift is a silent production failure waiting to happen.
- [ ] **Blast-proof the refactors with the existing 247-test suite** (green baseline) before touching the routers.

### Phase 2 — Core Hexagonalization (the big architectural win)
- [ ] **Introduce an `application/` (use-case) layer**: `DetectUseCase`, `RightToErasureService`, `PatientProfileService`, `StatsService`. Move orchestration out of `routers/detect.py`, `routers/patients.py`, `routers/stats.py`.
- [ ] **Add Pydantic request/response models** replacing every `body: dict` (esp. `admin_approve`, `create_patient`) — restores OpenAPI contract + boundary validation + IDE safety.
- [ ] **Purge `HTTPException` from `provisioning.py`** (and any domain validator): raise domain exceptions, map to HTTP in a single shared dependency/adapter.
- [ ] **Compose everything from `app.py` as the true composition root** — reserve `get_*` providers for tests, but move the wiring to construct application services once.

### Phase 3 — Code-craft hygiene (mechanical, low risk)
- [ ] **Collapse `api.js` to a single `requestJson(path, init)` helper** — kills the 8× triplication; add one shared error-classification function.
- [ ] **Fix LSP typing drift** in `InMemoryWriter.write_row`/`upsert_case` (return `Exception | None` consistently) and tighten `sanitize_text(str | None)`.
- [ ] **De-duplicate `key_from_request`** between `concurrency.py` and `rate_limit.py`; add eviction for the unbounded `_sems`/`_buckets` maps.
- [ ] **Remove dead code**: `cloud_sql_enabled` var, `variables.tf shutdown_hook`, `main.tf google-beta` provider, `auth.is_pending_approval`, `evaluate_accuracy.py` `* 0.0` term, `resp_boundary` redundant double-scrub.
- [ ] **Break the `result.js` ↔ `analyze.js` circular import** (inject a `onNew` callback or move `renderResult` into the router).

### Phase 4 — Long-Term Architectural Realignment
- [ ] **Give `PatientRow` real Value Objects**: validate `date_of_birth`, `mrn`, `phone`, and the facility-relative MRN invariant *at construction* (invariant-in-the-object) so no caller can create an invalid patient.
- [ ] **Make the erasure workflow reproducible/idempotent**: persist an erasure record-of-intent so a crash mid-cascade can resume (satisfies the strongest reading of DPDP right-to-erasure).
- [ ] **Move shared cross-store transaction semantics into a documented saga/outbox pattern** as the stores scale.
- [ ] **Config hardening**: rotate the committed Firebase web config to env-injected values, externalize the pinned Firebase CDN version, and reconcile the redundant `deploy.sh`/Terraform paths (pick one source of truth).
- [ ] **Narrow production CSP / `*.run.app`** to the exact origins and strip dev `connect-src` entries on deploy.
- [ ] **Implement the stubbed latency/quota alert policies** in `monitoring.tf`.

---

*Report generated from an exhaustive file-by-file audit of the full repository. The highest-value next step is **Phase 1 (fixing the two broken Terraform files)** because the Epic-7 patient store literally cannot deploy correctly today, followed by **Phase 2 (hexagonalizing the routers)** which is where the long-term maintainability win lives.*
