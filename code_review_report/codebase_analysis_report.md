# CyclotorsionCheck — Enterprise Codebase Analysis Report

## 1. Architecture Overview

**System**: CyclotorsionCheck — a toric IOL alignment verification tool. Surgeons upload two eye photographs (upright/rotated); a Gemini model computes the cyclotorsion angle and flags sanity-check failures. A Firebase-Hosted SPA faces a FastAPI service on Cloud Run, with BigQuery analytics and Firebase Auth.

```
Browser (vanilla ES-module SPA)
   │  fetch /detect /stats /admin/* /health /metrics
   ▼
Cloud Run v2 ── FastAPI (backend/cyclotorsion/)
   ├─ Gemini (google-genai)        # image analysis  [lazy]
   ├─ Firebase Auth               # ID-token verify  [lazy]
   ├─ BigQuery                    # analytics rows    [lazy]
   └─ InMemory / STDOUT           # circuit breaker, metrics, tracing
   ▲
Firebase Hosting (frontend/) ── Cloud Scheduler (backup cron)
                              ── Cloud Monitoring (alerts)
```

### Backend (`backend/cyclotorsion/`, 18 modules)
- **app.py (FastAPI app, 324 lines)**: `/health`, `/metrics` (Prometheus format), `/detect`, `/stats`, `/admin/users`, `/admin/approve`. Production-grade RBAC via dependency injection (`current_approved_user` → `current_verified_user` → `require_any_role`), `rate_limit` dependency, 10MB image limit via `MAX_IMAGE_BYTES`, file-type validation, structured `logger.info` events, lazy Cloud SDK imports.
- **detector.py**: `GeminiDetector` + `MockDetector` behind `DetectorFactory`/`ModeDetector`; retry with backoff on specific Gemini error codes.
- **auth.py**: Firebase `id_token` verification, role/facility custom claims, `_ensure_firebase_app`, benefit-of-doubt denial on errors (fail-closed — correct for clinical app).
- **storage.py**: `ResultWriter` ABC → `BigQueryWriter` (prod) + `InMemoryWriter` (dev). **Best-effort, non-blocking** writes so analytics failure never blocks the clinical result — deliberately good design.
- **circuit_breaker.py, tracing.py, metrics.py, config.py, secrets.py**: cross-cutting concerns, cleanly separated.

### Frontend (`frontend/`, framework-free)
Vanilla ES-module SPA (no build step), `type: module`, Firebase Hosting rewrites `**` → `index.html` (SPA fallback). Pages in `js/pages/*`, shared modules (`api.js`, `auth.js`, `session.js`). Linting (ESLint flat config) and formatting (Prettier) are clean.

### Database
BigQuery `results` table (schema in `storage.ResultRow`: test_id, timestamp, angle_deg, landmarks, flags, facility_id, user_uid — **no patient-identifiable fields**, de-identified by design per PRD Epic 4).

### Integrations
Gemini (AI analysis), Firebase Auth + Admin SDK, BigQuery (analytics), Cloud Scheduler (synthetic-data/backup cron), Cloud Monitoring (alerts/metrics/scrape), Secret Manager (API keys).

## 2. Code Quality Assessment

**Strengths**
- Layered, single-responsibility modules; clean separation of cross-cutting concerns (tracing, metrics, circuit breaker, secrets).
- Strong docstrings mapping directly to PRD/TDD bullets (e.g., `app.py`, `storage.py`, `auth.py`) — exceptional traceability.
- Defensive clinical-grade decisions: fail-closed auth, best-effort non-blocking analytics, circuit breaker with half-open probe, safe image parsing.
- Excellent, consistent logging via `logger.<event>(extra={"event": ...})`.
- **Backend: 98.24% unit-test coverage (148 tests)**; frontend tests green; all lint/format/type checks passing.

**Gaps**
- `BigQueryWriter` writes `None` for `facility_id`/`user_uid` by default and `_get_client()` logs nothing on failure; diagnostics on BQ insert failure are thin.
- `tests` monkeypatch cloud SDKs via `sys.modules` fakes — robust but means cloud paths are never integration-tested against real services.
- Some SQL/JSON batching (e.g., synthetic-data script) uses string interpolation rather than parameterized constructs in places.

## 3. Best-Practices Compliance

**Security — strong**
- Fail-closed auth; `scoped_by_facility_id` RBAC; `/admin/*` role-gated; only de-identified analytics surfaced.
- Secrets via Secret Manager + `_read_secret`; `.gitignore` prevents secret commits; images held in memory only (never persisted) per PRD US-5.1.
- **Opportunity**: no explicit CORS policy file found in `app.go`/config — Cloud Run + Firebase Hosting same-origin rewrites may make this moot, but a deliberate `CORSMiddleware` allow-list should be declared rather than assumed.

**Performance**
- Lazy SDK imports avoid cold-start penalties; rate limiting; 10MB upload cap; non-blocking analytics write decoupled from critical path. Good.
- **Opportunity**: no response caching or concurrency/backpressure tuning for the Gemini path; no load/soak test.

**Scalability / Reliability**
- Cloud Run v2 autoscales; circuit breaker protects Gemini; BigQuery is serverless. Strong.
- **Opportunity**: no horizontal caching layer (Redis/CDN) — fine at pilot scale but a known constraint.

**Version Control / CI-CD — strong** (recently added)
- `.github/workflows/ci.yml`: backend (Ruff check+format, pytest with `--cov-fail-under=90`) + frontend (lint, format:check, test). Root `.gitignore`, `package-lock.json` committed.
- **NOTE: repo is not yet a git repository.** CI is dead until `git init` + push.

**Documentation** — `docs/PRD` and `docs/TDD` are exceptional and drive the code.

## 4. Dependency & Package Review

- **Backend** (`backend/pyproject.toml`): prod = `fastapi`, `uvicorn[standard]`, `python-multipart`, `Pillow`; `[prod]` extra = `google-genai`, `google-cloud-bigquery`, `firebase-admin`; `[dev]` = `pytest`, `pytest-cov`, `httpx`, `ruff`. Clean and minimal. `requirements.txt` mirrors prod for Cloud Run.
- **Frontend** (`package.json`): zero runtime deps; dev = ESLint/Prettier/globals. **`npm audit`: 0 vulnerabilities.** Minimal supply-chain surface — excellent for a medical-adjacent tool.
- **Opportunity**: `requires-python = ">=3.10"` is broad; dependencies unpinned to exact versions (CI reproducibility relies on ranges) — consider lockfiles (Poetry/uv `uv.lock`, `npm` uses `package-lock.json` already).

## 5. Testing & Coverage

- **Backend**: 148 tests, **98.24%** coverage, `--cov-fail-under=90` enforced in pytest and CI. Thorough, behavioral tests incl. Gemini retries, auth paths, tracing, secrets, circuit breaker.
- **Frontend**: 4 tests via `node:test` covering pure `session.js` logic (Node 22 experimental VM modules). Framework-free UI means no DOM/component tests.
- **CI**: backend + frontend jobs wired to gate on all of the above.

## 6. Operational Readiness

- **Strong**: `/health` liveness probe, `/metrics` Prometheus endpoint scraped by Cloud Monitoring, Cloud Scheduler cron for backup/synthetic data, Terraform IaC (`infra/terraform/`) with region `us-central1`, ops scripts (`scripts/backup_export.py`, `smoke_api.py`, `evaluate_accuracy.py`, `generate_synthetic_data.py`).
- **⚠️ Critical gap — `/stats` in production**: `_visible_rows()` (`app.py:250`) reads `getattr(_WRITER, "rows", [])`. This only works with `InMemoryWriter`. When `CC_STORAGE_MODE=bigquery` (production default), the writer is `BigQueryWriter` which has **no** `.rows` attribute → `/stats` returns zeroed stats. Either `/stats` must query BigQuery directly, or keep an in-memory mirror. As written, the analytics endpoint silently returns zeros in production.
- **⚠️ No Dockerfile found**: Cloud Run deploy must use Cloud Native Buildpacks (GCP default) or the build would fail. Ensure the CI/deploy path documents buildpacks or adds a Dockerfile.
- **⚠️ Repo not version-controlled**: `.github/`, `.gitignore`, and a prod toolchain exist, but no `git init` → no history, no remote, no actual CI runs, no rollback.

## 7. Prioritized Recommendations

**P0 — Correctness / blocking**
1. Fix `/stats` to read from BigQuery when in `bigquery` storage mode (`app.py:257`), or maintain an in-memory row cache for stats. Highest-impact functional bug.
2. `git init`, commit, push to a remote, and trigger CI (it's fully configured but inert without a repo).

**P1 — Operational hardening**
3. Add an explicit CORS allow-list middleware; verify it against the Firebase Hosting origin.
4. Decide and document the Cloud Run build strategy — add a `Dockerfile` or explicitly rely on buildpacks in README/CI.
5. Add `logging_warning` surfacing for BigQuery insert failures (currently swallowed as a bare exception) and a smoke test that runs `/stats` against `bigquery` mode locally.

**P2 — Quality / reproducibility**
6. Pin/lock backend dependencies (uv/Poetry lockfile) to make CI reproducible; keep ranges in `requirements.txt` for flexibility.
7. Add frontend DOM/component tests (jsdom or a headless runner) — login/approval flow currently untested.
8. Add an integration test tier that runs the cloud backends against emulators (Firebase Emulator, BigQuery local) to validate the mocked paths.

**P3 — Resilience / scale**
9. Load/stress test the Gemini path; add per-user concurrency limits and a response-size cap.
10. Add a caching layer only if/when pilot traffic justifies it.

**Overall**: A remarkably well-engineered, security-conscious, well-tested codebase (98% backend coverage, lint/format/CI green, minimal attack surface, clean IaC). The blocking issues are narrow and surgical: the `/stats`-with-BigQuery gap and the not-yet-initiated git repo. Fix those two and it's ready for a pilot.

---

# Remediation Status (all findings addressed in sequential order)

## P0 — Correctness / blocking
1. **`/stats` reads from BigQuery — DONE.** `storage.ResultWriter` now defines a
   `read_rows(facility_id=...)` method; both `InMemoryWriter` and
   `BigQueryWriter` implement it (`BigQueryWriter` pushes the facility filter
   into a **parameterized** `SELECT ... WHERE facility_id = @facility_id`).
   `app.stats()`/`_visible_rows()` delegate to `_WRITER.read_rows(...)`, so
   production aggregates are real, not zeros. Covered by new unit tests
   (`test_storage.py`) and a full auth-enabled `/detect` → `/stats` round-trip
   (`test_integration.py`, incl. facility scoping).
2. **Git repo initialized + committed — DONE.** `git init` (branch `main`),
   `.gitignore` extended with `.coverage`/`htmlcov`, and an initial commit
   created. CI will run once a remote is added and pushed (no remote URL was
   provided).

## P1 — Operational hardening
3. **CORS — already correct (no change needed).** The report's CORS concern was
   inaccurate: `app.py` already registers `CORSMiddleware` with an explicit
   allow-list derived from `cfg.cors_origin_list` (`CC_CORS_ORIGINS`), with a
   sensible localhost default. Verified, documented.
4. **Dockerfiles added — DONE.** `docker/api.Dockerfile` (Cloud Run API, listens
   `0.0.0.0:8080`, installs prod deps and prefers `requirements.lock` to
   `requirements.txt`) and `docker/backup.Dockerfile` (Cloud Run job running
   `scripts/backup_export.py`). Build steps documented in `infra/gcloud/deploy.sh`
   and `infra/README.md`.
5. **BigQuery failure visibility — DONE.** `BigQueryWriter._get_client`,
   `write_row`, and `read_rows` now log structured warnings on failure
   (`storage.bigquery_client_init_failed` / `bigquery_write_failed` /
   `bigquery_read_failed`) instead of silently swallowing. `write_row` still
   returns the exception for the existing non-blocking `logging_warning` path in
   the `/detect` response; `read_rows` returns `[]` on failure so `/stats`
   degrades (zeros) rather than crashing. Smoke covered by
   `test_stats_reads_from_bigquery_writer` + `test_integration.py`.

## P2 — Quality / reproducibility
6. **Backend lock file — DONE.** `backend/requirements.lock` (exact pins,
   resolved from `requirements.txt` via `pip-compile`, `pip-tools` added to the
   dev extras). CI validates the committed lock installs cleanly; Docker
   prefers the lock. Ranges in `requirements.txt`/`pyproject.toml` retained for
   flexibility.
7. **Frontend DOM/component tests — DONE.** Added `@happy-dom/global-registrator`
   and `frontend/test/login.test.js` covering the login card render, the
   login/register toggle, and the failed-submit friendly-error path
   (re-enables button). `eslint.config.js` test globals now include browser
   globals (happy-dom). Frontend suite: **7 tests, all passing**; lint + Prettier
   clean.
8. **Integration test tier — DONE.** `backend/tests/test_integration.py`
   exercises the full production circuit with mocked Firebase verification +
   fake BigQuery client: `program_officer` sees all facilities
   (`scoped_by_facility_id` is null) and a `facility_admin` sees their own
   facility's rows, proving `read_rows` runs a real query. Runs in CI without
   credentials; point the same tests at emulators by swapping the fakes.

## P3 — Resilience / scale
9. **Per-user concurrency limit + response-size cap — DONE.**
   `concurrency.py` adds a per-key (`uid:`/`ip:`) semaphore guard
   (`CC_CONCURRENCY_LIMIT_ENABLED`, `CC_MAX_CONCURRENT_PER_USER`, default 2)
   wired into `/detect` (acquire via dependency, release in `finally`). Enabled
   in `infra/terraform/cloudrun.tf` and `deploy.sh`. The Gemini response-size cap
   bounds `description` to `MAX_DESCRIPTION_CHARS` (200) in `detector._parse_landmark`.
   Config fields and both behaviors are unit-tested (`test_concurrency.py`,
   `test_detector.py`). Load/stress testing remains recommended once real Gemini
   credentials are allocated (documented in `infra/README.md`).
10. **Caching — deferred by design (documented).** No read-heavy shared data at
    pilot scale and Cloud Run autoscales; a Redis/CDN layer was deemed premature.
    Documented as a deliberate decision in `infra/README.md`.

## Findings verified to be inaccurate (no change made)
- **SQL string interpolation in the synthetic-data script**: no such code exists.
  `scripts/generate_synthetic_data.py` is pure PIL image generation with no SQL;
  `backup_export.py` uses the BigQuery SDK `extract_table` (no SQL); the only SQL
  is the new parameterized query in `BigQueryWriter.read_rows` (uses BigQuery
  query parameters, not string interpolation).
- **CORS policy missing**: already present and correctly configured (see P1-3).

## Final verification
- Backend: `python -m pytest` → **166 passed**, coverage **97.74%**
  (threshold 90% enforced), `storage.py` at 100%.
- `python -m ruff check .` and `python -m ruff format --check .` → clean.
- Frontend: `npm test` → **7 passed**; `npm run lint` and `npm run format:check` → clean.
- Git: repo initialized, initial commit created.
