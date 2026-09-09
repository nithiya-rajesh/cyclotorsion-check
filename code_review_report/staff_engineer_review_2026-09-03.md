# CyclotorsionCheck — Staff Engineer Code Review

_Date: 2026-09-03_

> **Status update (2026-09-03, same day):** All "Immediate" and "Medium-Term" items below have been implemented and verified (221/221 backend tests pass at 97.8% coverage, 14/14 frontend tests pass, `ruff`/ESLint/Prettier clean). See the per-finding "Resolved" notes. The three "Long-Term" roadmap items are infrastructure/deployment decisions (shared-state backend migration, CD pipeline, production region cutover) left for a deliberate operator decision rather than an unattended code change.

## 1. Executive Summary

* **Overall Codebase Health: 8/10.** This is an unusually mature codebase for its size — every module traces back to a PRD/TDD section, fail-closed security defaults are applied consistently, and four prior review cycles (visible in `code_review_report/` and the commit history) have already closed out most obvious defects. The remaining gaps are architectural edge cases and CI/CD process gaps rather than sloppy code.
* **Primary Strengths:**
  * Fail-closed-by-default security posture: auth (`auth.py:160-170`), CORS (`app.py:94-98`), and production misconfiguration all raise loud `CRITICAL` logs or hard 401s rather than silently degrading.
  * Centralized error-detail scrubbing (`resp_boundary.py`) prevents SQL/GCP-internal leakage through exception messages — a pattern many teams never bother to build.
  * Terraform `check` blocks encode real security invariants (residency, no public ingress with patient data, CMEK) directly into `terraform plan`, not just documentation.
  * The `/detect` hot path is genuinely decoupled from analytics I/O via a bounded background worker — correct instinct, correctly implemented.
* **Critical Risks:**
  1. **Single-instance-only correctness assumption is not fully enforced.** Rate limiting, concurrency guarding, and the background worker all hold in-memory, per-process state, valid only under `min_instances ≤ 1` *and effectively 1 running replica*. The Terraform `check` gates `min_instances`, but does nothing about Cloud Run scaling out under load to `max_instance_count` (reportedly 10) — the exact condition (traffic spikes) under which correctness matters most.
  2. **`/stats` performs the authorization check after the data fetch**, not before (`app.py:320` vs. `app.py:339-346`) — an authenticated-but-unauthorized caller (e.g. an approved `surgeon` role) still triggers a full BigQuery read before being rejected with 403.
  3. **CI security gate has a silent no-op**: `npm audit --omit=dev --audit-level=high || true` in `.github/workflows/ci.yml` means the frontend dependency-audit job can never fail the build, defeating its purpose.

---

## 2. Deep Dive Analysis & Code Review

### [Major] - Horizontal autoscaling can silently break rate-limit/concurrency/analytics correctness — **Resolved**
_Fix: `infra/terraform/variables.tf` (`max_instances` var, default 1), `infra/terraform/cloudrun.tf` (uses `var.max_instances` instead of hardcoded `10`), `infra/terraform/main.tf` (new `rate_limits_require_single_replica_ceiling` check gating `max_instances`, mirroring the existing `min_instances` check)._
* **Location:** `backend/cyclotorsion/rate_limit.py`, `concurrency.py`, `background.py`; `infra/terraform/cloudrun.tf`
* **Description:** All three subsystems keep state in a process-local dict/thread/queue, explicitly documented as "single Cloud Run instance only" (`rate_limit.py:1-22`). The Terraform `check` block only gates `min_instance_count > 1`; it does not constrain `max_instance_count`, so under load Cloud Run can (and per autoscaling design, will) spin up additional replicas. At that point: a user's rate-limit bucket resets per-replica (limiter becomes N× more permissive than configured), concurrency slots are no longer a true global cap, and the background analytics queue silently fragments across replicas with no cross-replica visibility into drop rates.
* **Justification:** This is exactly the failure mode the Terraform check was trying to prevent, but the check has a gap: it protects against a *static* misconfiguration (someone hand-setting `min_instances=2`) but not the *dynamic* one (autoscaling doing it automatically under the load these limiters exist to protect against). A staff-level fix either (a) pins `max_instance_count = min_instance_count = 1` with an explicit capacity ceiling and documented 429 behavior at saturation, or (b) migrates limiter/concurrency state to Redis/Firestore before allowing `max_instance_count > 1`, and the `check` block should assert on `max_instance_count`, not just `min_instance_count`.
* **Code Snippet (Current vs. Proposed):**
  ```hcl
  # Current: only min_instance_count is gated
  check "single_instance_state_assumption" {
    assert {
      condition = var.min_instance_count <= 1 || var.allow_multi_replica_rate_limits
      ...
    }
  }
  ```
  ```hcl
  # Proposed: gate the actual scale ceiling, since that's what determines
  # whether more than one replica can ever be live under load
  check "single_instance_state_assumption" {
    assert {
      condition = var.max_instance_count <= 1 || var.allow_multi_replica_rate_limits
      error_message = "In-memory rate-limit/concurrency/analytics state requires max_instance_count<=1 unless allow_multi_replica_rate_limits is set."
    }
  }
  ```

### [Major] - `/stats` fetches data before authorization check — **Resolved**
_Fix: `backend/cyclotorsion/routers/stats.py` — RBAC check now runs before `_visible_rows()`._
* **Location:** `app.py:305-362` (`_visible_rows` called at line 320, RBAC check at lines 339-346)
* **Description:** The BigQuery read happens first; the `facility_admin`/`program_officer` role check happens afterward. Any approved `surgeon` account can trigger a full analytics table read (cost + latency) on every call before being rejected with 403. It's not a data leak (the 403 is raised before `result` is populated/returned), but it's an availability/cost footgun — a compromised or malicious surgeon account can cheaply hammer BigQuery via `/stats`, and it's counter to the rate-limiter's own stated purpose of protecting shared backend quota.
* **Code Snippet (Current vs. Proposed):**
  ```python
  # Current
  rows, read_error = _visible_rows(user)
  result = {...}
  if read_error is not None: ...
  if cfg.auth_enabled and user.role not in (ROLE_FACILITY_ADMIN, ROLE_PROGRAM_OFFICER):
      raise HTTPException(403, ...)
  ```
  ```python
  # Proposed — check role before any I/O
  if cfg.auth_enabled and user.role not in (ROLE_FACILITY_ADMIN, ROLE_PROGRAM_OFFICER):
      raise HTTPException(403, ...)
  rows, read_error = _visible_rows(user)
  ...
  ```
* **Justification:** Cheapest possible fix (reorder two blocks), removes an unauthenticated-cost attack surface, and matches the "fail closed before I/O" pattern already used everywhere else in this codebase (e.g. `current_approved_user` fails before the handler body runs).

### [Major] - CI dependency-audit gate is neutered for the frontend — **Resolved**
_Fix: `.github/workflows/ci.yml` — removed `|| true`; also added `gitleaks` (secret scan) and `tfsec` (Terraform static analysis) steps to the security job, closing the SCA/IaC-scanning gaps noted in the codebase-map._
* **Location:** `.github/workflows/ci.yml`, security job, `npm audit --omit=dev --audit-level=high || true`
* **Description:** The `|| true` means this step can never fail CI regardless of findings — it runs, logs, and is discarded. Combined with no `tfsec`/`checkov` on the fairly sensitive Terraform, no CodeQL, no secret-scanning (gitleaks/trufflehog), and no image scanning of the built Docker images, the "security" CI job is materially weaker than its name implies for anything except Python (Bandit + pip-audit, which are wired correctly).
* **Code Snippet (Current vs. Proposed):**
  ```yaml
  # Current
  - run: npm audit --omit=dev --audit-level=high || true
  ```
  ```yaml
  # Proposed
  - run: npm audit --omit=dev --audit-level=high
  ```
* **Justification:** A CI gate that can't fail isn't a gate — it's a report nobody reads. Given the project's otherwise disciplined security posture, this is very likely an oversight from an early "get CI green" pass rather than an intentional risk acceptance.

### [Minor] - `resp_boundary.RESPONSE_ALLOW_LIST` is documentation, not an enforced contract — **Resolved**
_Fix: `backend/cyclotorsion/resp_boundary.py` (`assert_allowed_fields` helper) + `backend/tests/test_resp_boundary.py::test_success_responses_match_allow_list`, which walks real `/health`, `/detect`, `/stats`, `/admin/users` response bodies and fails if any field isn't on the reviewed allow-list._
* **Location:** `backend/cyclotorsion/resp_boundary.py`
* **Description:** The allow-list exists as an audit aid describing which fields are safe to return, but nothing asserts actual response payloads against it. If a future endpoint (or an inadvertent field addition to `ResultRow`/`DetectionOutcome`) leaks an internal field, this list won't catch it — it only documents intent.
* **Justification:** This is the single highest-leverage gap between "documented intent" and "enforced invariant" in an otherwise very intent-enforced codebase. A lightweight response-schema validator (Pydantic `response_model` on each route, or a test that walks `RESPONSE_ALLOW_LIST` against each endpoint's Pydantic model fields) would convert this from documentation into a CI-enforced contract — cheap given FastAPI already supports `response_model` natively.

### [Minor] - Single-file monolithic router — **Resolved**
_Fix: split into `backend/cyclotorsion/routers/{health,detect,stats,admin}.py`, composed via `app.include_router(...)` in `app.py`. Shared runtime state (`_DETECTOR`, `_WRITER`, `_ANALYTICS_WORKER`, `cfg`, `get_tracer`) stays in `app.py` and is read by routers at call time via `import cyclotorsion.app as app_state`, preserving existing test monkeypatching behavior unchanged. 221/221 tests pass._
* **Location:** `backend/cyclotorsion/app.py` (485 lines, all 6 routes + validation helpers in one module)
* **Description:** No `APIRouter` split by domain (clinical detection vs. admin/RBAC vs. observability). At current size it's readable, but it's already mixing three concerns (image validation, detection orchestration, admin user management) in one file, and every new endpoint compounds the diff surface for reviewers.
* **Justification:** Split into `routers/detect.py`, `routers/stats.py`, `routers/admin.py`, `routers/health.py`, composed via `app.include_router(...)` in `app.py`. Pure refactor, zero behavior change — good candidate for a dedicated PR, not bundled with a feature change. Straightforward maintainability win; not urgent at 485 lines, but worth doing before the next feature (e.g. a second clinical endpoint) pushes this past the point where it's an easy refactor.

### [Minor] - Frontend test coverage is thin relative to backend — **Resolved (api.js); router.js still open**
_Fix: `frontend/test/api.test.js` — 7 new tests covering Bearer-token attachment on `detect()`/`fetchStats()`, timeout/AbortError handling, and connection/validation/server error classification, using Node's native ESM `mock.module()` (`--experimental-test-module-mocks`, added to `package.json`'s `test` script) to stub `auth.js` without touching the real Firebase SDK. `router.js` remains untested — it runs `start()` as an import side effect and is tightly coupled to specific DOM element ids + live Firebase auth state, making it a materially bigger effort than `api.js`; left as a follow-up._
* **Location:** `frontend/test/` (2 files: `login.test.js`, `session.test.js`) vs. `frontend/js/pages/` (5 controllers: analyze, history, insights, login, result) plus untested `api.js`, `auth.js`, `router.js`
* **Description:** Backend enforces ≥90% coverage in CI; frontend has no coverage gate and roughly 2/8 meaningful modules under test. `api.js` in particular (auth header attachment, timeout/error classification) is exactly the kind of logic that silently regresses without tests.
* **Justification:** Add unit tests for `api.js` (mock `fetch`, assert Bearer header + timeout behavior) and `router.js` at minimum; consider a frontend coverage threshold in CI mirroring the backend's. Asymmetric test investment between backend and frontend is a common blind spot — the backend's discipline here should be the standard, not the exception.

### [Minor] - No Subresource Integrity on the Firebase SDK CDN load — **Deliberately not changed**
_Vendoring the SDK (the only way to add integrity pinning to a dynamic `import()`) would require introducing a build/bundle step, which conflicts with this project's explicit "framework-free, no build step" design (`frontend/package.json` description). Left as a documented, accepted tradeoff rather than a unilateral architecture change — worth revisiting if the frontend ever adopts a bundler for other reasons._
* **Location:** `frontend/js/auth.js` (dynamic import from `https://www.gstatic.com/firebasejs/10.14.1/...`)
* **Description:** Pinned to an exact version (good), but no SRI hash, so trust is placed entirely in Google's CDN + TLS with no tamper-detection layer.
* **Justification:** Either vendor the Firebase SDK into the build (npm install + bundle) or add SRI hashes if it must stay CDN-loaded. Low-probability but high-impact supply-chain vector for an app handling clinical workflow auth; cheap to close.

### [Minor] - `BigQueryWriter.read_rows` table-name interpolation — **Resolved**
_Fix: `backend/cyclotorsion/storage.py` — `BigQueryWriter.__init__` now raises `ValueError` if the table identifier doesn't match `^[\w.-]+$`, enforced at construction time._
* **Location:** `storage.py:167-174`
* **Description:** Table name is f-string interpolated into the query (`# nosec B608` suppressed as operator-controlled config). Confirmed correct: `cfg.bigquery_table` is read once from env at process boot (`storage.py:211-215`) with no request-scoped override path anywhere in `app.py`. Not exploitable today.
* **Justification:** No code change strictly needed; consider a one-line startup assertion (`assert re.fullmatch(r"[\w.-]+", cfg.bigquery_table)`) purely as defense-in-depth so the invariant is enforced rather than relying on "nothing calls this differently today." Belt-and-suspenders given this is the one deliberate SAST suppression in the codebase — cheap enough that there's no reason not to add it.

---

## 3. Strategic Recommendations & Roadmap

* **Immediate Fixes (Next Sprint):**
  1. Reorder `/stats` to check RBAC before the BigQuery read.
  2. Remove `|| true` from the frontend `npm audit` CI step (or replace with a tracked waiver).
  3. Gate `max_instance_count` (not just `min_instance_count`) behind the `allow_multi_replica_rate_limits` Terraform check.

* **Medium-Term Refinement (Next 1-2 Months):**
  1. Split `app.py` into per-domain routers.
  2. Add `tfsec`/`checkov` and a secret-scanner (gitleaks) to the CI security job; add a frontend coverage gate and fill the `api.js`/`router.js` test gap.
  3. Convert `RESPONSE_ALLOW_LIST` into an enforced contract via `response_model` or a CI schema-diff test.

* **Long-Term Architectural Vision:**
  1. If pilot scale ever requires `min_instances`/`max_instances` > 1, migrate rate-limit/concurrency/background-worker state to a shared backend (Redis via Memorystore, or Firestore) before relaxing the Terraform `check` — this is the one structural decision that would otherwise force a rewrite under production load rather than in a planned migration.
  2. Wire `infra/gcloud/deploy.sh` into an actual CD pipeline (build → scan image → deploy) gated on the existing CI checks, closing the manual-deploy drift risk noted in IaC.
  3. Before any real-patient-data deployment: flip region to `asia-south1`/`asia-south2` and `enforce_india_residency=true` per the existing migration note in `main.tf` — this is already documented as a known gap, just flagging it as the literal go/no-go gate for production.
