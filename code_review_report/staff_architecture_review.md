# CyclotorsionCheck — Production-Grade Architecture & Security Code Review

*Scope: full-stack codebase (FastAPI backend, vanilla-JS SPA, Terraform IaC, GitHub Actions CI). Reviewed against the five vectors: architecture, quality, bugs/security, performance, testing/CI-CD.*

---

## 1. Executive Summary

* **Overall Codebase Health: 7.5 / 10.** This is a genuinely well-architected V1: deep design docs, clean separation of AI/deterministic logic, dependency-injection-friendly factories, lazy cloud imports, and a 90%-coverage enforced test suite with the AppSec findings from the prior audit already remediated. It is held back from 8+ by several production-readiness gaps: duplicated per-request Firebase verification, a concurrency-slot leak on dependency-abort paths, a CORS default that quietly allows credentials from developer origins in all environments, and zero CD/container tests in CI.

* **Primary Strengths:**
  - **Facade/Adapter pattern is excellent.** `detector.py` isolates all Gemini interaction (prompt, retry+jitter, circuit breaker) behind `LandmarkDetector`; `pipeline.py` cleanly orchestrates detect → geometry → sanity. Swapping providers or the AI back-end is a one-file change.
  - **Fail-closed discipline.** Auth fails closed on bad/missing tokens, in production-with-auth-disabled, and for pending-approval accounts; admin tiers are facility-scoped; raw exception text never reaches callers.
  - **Reviewable, deterministic clinical core.** `geometry.py` and `sanity.py` are pure functions with auditable math and documented simplifications (pupil-center assumption), not model-estimated angles.
  - **Right-sized dependencies & lazy cloud integration.** The app imports and runs fully without any GCP/Firebase credential; cloud SDKs load on first use. Excellent for testability.
  - **Disciplined testing.** `pytest.ini` enforces `--cov-fail-under=90`; CI runs lint, format, coverage, SAST (bandit/pip-audit/npm audit), and validates the production lock.

* **Critical Risks (top issues requiring immediate attention before live patient onboarding):**
  1. **Concurrency-slot leak on dependency-abort paths** (`/detect`): a Firebase/rate-limit 429/401 raised *after* `concurrency_limit` acquires a semaphore permanently consumes a per-user slot until process restart — a silent self-DoS escalation path.
  2. **Duplicate Firebase ID-token verification per request** (`/detect` verifies the token **twice**); at pilot volume this doubles identity-provider load/cost and latency, and is evidence the auth dependency graph isn't sharing state correctly.
  3. **Default CORS allows credentialed requests from `localhost:3000/8080` in production.** `config.cors_origin_list` returns dev origins when `CC_CORS_ORIGINS` is unset — an operator forgetting to set the var ships an allowlist that includes developer origins with `allow_credentials=True`.

---

## 2. Deep Dive Analysis & Code Review

### Vector 3 · Bugs / Security (Highest concentration of issues)

---

### [Critical] - Concurrency semaphore leak when a later dependency aborts `/detect`

* **Location:** `backend/cyclotorsion/app.py:173-253` (`detect`), `concurrency.py:72-107`, `rate_limit.py:111-130`, `auth.py:221-240`
* **Description:** `concurrency_limit` acquires a per-user semaphore slot as a FastAPI dependency. If a *subsequent* dependency raises (e.g. `rate_limit` returns 429 because the user is rate-limited, or a duplicate-token check fails), FastAPI aborts the request **before the endpoint body runs**, so the `finally: release_concurrency(request)` at `app.py:253` **never executes**. The slot is silently consumed for the rest of the process lifetime. Empirically confirmed: after an abort the semaphore remains fully consumed (`request.state.concurrency_key` is still set, `acquire()` returns False). A malicious user can trigger 429 (rate limit) repeatedly to ratchet themselves to permanent 429, or a burst that other users share the replica with degrades availability.
* **Code Snippet:**

```python
# app.py — slot acquired as a dependency, released only in body's finally
async def detect(..., _rate=Depends(rate_limit), _conc=Depends(concurrency_limit)):
    try:
        ...
    finally:
        release_concurrency(request)   # never runs if a dependency 429/401'd

# order of resolution in the signature matters
```

```python
# Proposed: acquire INSIDE the handler (after validation), guard the whole body,
# or wrap dependencies so a post-acquire failure always releases.
async def detect(..., _conc=Depends(concurrency_limit)):
    # acquire a token within the request body, not as a short-circuit dep
    acquired = concurrency_limit.acquire_session(request)   # non-raising
    try:
        ...
    finally:
        release_concurrency(request)
```
* **Justification:** From a Staff Engineer stance this is a resource-accounting invariant, not a cosmetic one. Any "acquired somewhere, released elsewhere" pattern that can be bypassed by an early abort is a leak. Prefer sandboxing acquisition/release inside a single `with concurrency_slot(request):` context that FastAPI cannot short-circuit, so the holder is always the releaser. Add a regression test that fires a 429 via `rate_limit`/token failure *after* a concurrency acquisition and asserts availability returns to full.

---

### [Major] - Firebase ID token verified twice per `/detect` request

* **Location:** `app.py:178` (`Depends(current_approved_user)`), `auth.py:150-173` (`current_user`), `auth.py:221-240` (`current_approved_user`), `rate_limit.py:111`, `concurrency.py:72`
* **Description:** FastAPI caches sub-dependencies by callable identity + args, so the `Depends(current_user)` inside `rate_limit` and `concurrency_limit` share **one** cached `current_user`. But `current_approved_user` calls `current_user(request)` **directly** (not via `Depends`), which bypasses the cache — producing a **second** `_verify_with_firebase` (a network/id-keyed call to Firebase) per request. Net: **two token verifications per `/detect`**. Same pattern affects `/stats`, `/admin/*` via `require_any_role`.
* **Code Snippet:**

```python
# auth.py — direct call bypasses FastAPI's dependency cache
def current_approved_user(request: Request) -> UserContext:
    user = current_user(request)   # direct call → NOT cached with the Depends(user) below
    ...
```
```python
# Proposed: express the compound gate *as a dependency of* current_user so it is cached.
def current_approved_user(user: UserContext = Depends(current_user)) -> UserContext:
    if cfg.auth_enabled and is_pending_approval(user):
        raise HTTPException(status_code=403, detail="Account pending approval")
    return user
```
* **Justification:** Doubling identity verification doubles per-request latency on the clinical path and the Firebase billing/load footprint. Making `current_approved_user` depend on `Depends(current_user)` both fixes the duplication and makes the dependency graph explicit and cacheable. This is a cheap, high-leverage change.

---

### [Major] - Default CORS allowlist silently includes developer origins in production

* **Location:** `config.py:172-180` (`cors_origin_list`), `app.py:88-94`
* **Description:** When `CC_CORS_ORIGINS` is unset, `cors_origin_list` returns `["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8080"]` — **even in production**. Combined with `allow_credentials=True`, any page running on `localhost:3000` (e.g. a malicious local app, or developer tooling) is a credentialed-permitted origin for the API. Operators who forget to set `CC_CORS_ORIGINS` inherit an implicit dev-origin allowlist rather than failing closed. There is also no signal that this is a misconfiguration (unlike the auth fail-closed guard).
* **Code Snippet:**

```python
# config.py
@property
def cors_origin_list(self) -> list[str]:
    if self.cors_origins:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
    # fallback used in production too:
    return ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8080"]
```
```python
# Proposed: in production, empty CC_CORS_ORIGINS => no origins allowed (fail closed)
@property
def cors_origin_list(self) -> list[str]:
    if cfg.is_production and not self.cors_origins:
        logger.critical("config.cors_empty_in_production", extra={"event": "config.cors_empty_in_production"})
        return []
    return [o.strip() for o in self.cors_origins.split(",") if o.strip()] or \
           ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8080"]
```
* **Justification:** Credentialed CORS is a perimeter control; its default should be the safe default. Mirror the already-built `is_production` fail-closed philosophy (used in auth) onto CORS, and log when the fallback list is active so the exposure is auditable.

---

### [Major] - Cluster impersonation / cached detections: in-memory rate, concurrency, and analytics state is per-instance

* **Location:** `rate_limit.py:92-109`, `concurrency.py:59-69`, `storage.py:67-80` (`InMemoryWriter`), `README:125-135`
* **Description:** Rate-limit, concurrency, and the in-memory `/stats` store are all process-local. `cloudrun.tf` runs `--max-instances=10`. With >1 replica, (a) rate/concurrency budgets are effectively unenforced cluster-wide (a client spreads across replicas ≈10× budget), and (b) `/stats` returns *different* aggregates depending on which replica answers — analytics inconsistency. The README documents the *intent* but the deployment makes `min-instances` configurable with no guard, so this is a live invariant, not just a note.
* **Code Snippet:**

```python
# concurrency.py — dict of semaphores, process-local
self._sems: dict[str, threading.Semaphore] = ...
```
```python
# Proposed: if min-instances > 1, back these stores (Memorystore/Firestore)
# and gate: assert !use_real_gemini or min_instances <= 1 before allowing the
# rate/concurrency config to be ignored.
```
* **Justification:** A distributed-bypass on rate limiting + a split-brain `/stats` are both production correctness issues at precisely the scale the IaC is heading toward. Either keep `min-instances=1` (and enforce it in Terraform `check`), or move the three stores to a shared backing store. This should be a hard `check` in Terraform, not a doc caveat.

---

### [Major] - `safeDetail` and error paths can echo backend internals (limited)

* **Location:** `frontend/js/api.js:65-77, 109-116`; `app.py` exception paths
* **Description:** `safeDetail` renders `body.detail` from the server verbatim into the UI. The FastAPI validation `detail` (e.g. 422 field errors) and 403/403 messages are generally safe, but any future handler that accidentally includes internal text in `detail` (or a future dev-added endpoint) will leak it to any authenticated client. The prior MED-3 fix sanitized `/detect`'s *write* path but the *validation/parse* errors and the 413/415/400 `detail`s still carry interpolated values (see `app.py:140-153`). These are acceptable (they're the consumer's own input), but there's no central "detail must be allow-listed" pattern enforced at the framework boundary.
* **Justification:** Introduce a single FastAPI exception handler / response-schema policy that whitelists which fields reach the client, and unit-test that no `detail` or non-`logging_warning` field may contain `projects/`, `SELECT`, or `.json` fragments. Centralize rather than patching per-endpoint.

---

### Vector 3 · continued: correctness edge cases

### [Major] - `/detect` response `warning` may leak internal landmark description

* **Location:** `pipeline.py:31-43`, `app.py:219, 233`
* **Status: FIXED** — `text.sanitize_text` sanitizes descriptions + warnings at
  ingest (`_parse_landmark`) and at the response boundary (`to_client_dict`);
  central `resp_boundary.RESPONSE_ALLOW_LIST` documents the client-visible field
  set. SPA already renders via `textContent` only.
* **Description:** `to_client_dict` puts `self.upright_landmark.description` / `rotated_landmark.description` into the response. For Gemini, the description is the free-text model string (capped at 200 chars) — a model-controlled free text field returned to the clinician verbatim. It is not HTML-escaped server-side (depends entirely on the SPA using `el()`/`textContent`). If the SPA ever renders it via innerHTML (it currently throws on `html:` — good), this is a stored/reflected XSS vector. Even without XSS, an untrusted model string is displayed directly.
* **Code Snippet:**

```python
# pipeline.py
"upright_landmark": self.upright_landmark.description,
```
```python
# Proposed: treat descriptions as untrusted: escape at render, and validate/
# redact on ingest (regex allow-list of generic anatomical tokens, strip control
# characters) before returning them to clients or persisting to BigQuery.
```
* **Justification:** Defense-in-depth: the model output is third-party-untrusted data. Cap ✅ exists (200 chars), but validation + explicit output encoding at the API boundary belongs in the service, not only the SPA.

---

### [Major] - `BigQueryWriter.read_rows` returns `[]` on ANY failure (including transient), silently hiding broken analytics

* **Location:** `storage.py:130-187`
* **Status: FIXED** — `read_rows` now returns a typed `(rows, error)`; `/stats`
  surfaces `analytics_warning="analytics read failed"` (logged server-side) when
  the read degrades, distinguishing "no data" from "analytics broken".
* **Description:** The read path swallows *all* exceptions and returns `[]`. For `/stats`, a fully-wedged BigQuery (permissions revoked, dataset deleted) renders as an empty (yet successful-looking) report instead of surfacing "analytics unavailable". Combined with the metrics there is no per-`/stats` alert. This is a correctness + observability gap: aggregates are *clinically trusted* outputs, and empty-when-broken is the wrong failure mode.
* **Proposed:** return a typed result `(rows, error)` and let `/stats` include `analytics_warning=true` (and log/alert) when reads degrade, instead of silently returning zero rows.

---

### Vector 4 · Performance

### [Major] - Cloud Trace export + analytics write run on the scheduled `end()`/`finally` (critical-path latency)

* **Location:** `app.py:251-253` + `tracing.py:143-150`; `storage.py:113-128`
* **Status: FIXED** — new `background.py` bounded worker; Cloud Trace flush and
  the analytics `write_row` are both enqueued off the hot path (non-blocking,
  drop-on-full). `logging_warning` response fields removed; failures tracked via
  `bigquery_write_failure_rate` + logs.
* **Description:** `tracer.end(...)` calls `self.exporter.flush(buf)` which performs a **synchronous, network `batch_write_spans`** call inside the request's `finally`, before the response is fully returned. The GCP client is lazily constructed but the flush is in-process and blocking. A slow Trace API adds wall-clock latency to every `/detect` response. Analytics `write_row` is also synchronous (though non-blocking). Neither is offloaded to a worker/background queue.
* **Proposed:** defer tracing telemetry off the hot path — collect spans into a bounded in-memory queue flushed by a background thread on a cadence, and bound the queue length. Same pattern applies to analytics events; use `asyncio` background tasks or a batched writer rather than inline calls.
* **Justification:** The clinical result should only wait on the AI provider and BQ write at most; telemetry and observability plumbing must never add tail latency.

---

### Vector 5 · Testing & CI/CD Readiness

### [Major] - CI has no Docker build, container scan, or CD/deploy job; tests don't run in the production container

* **Location:** `.github/workflows/ci.yml` (jobs: backend / frontend / security only)
* **Description:** There is no job that builds `backend/api.Dockerfile`, runs the test suite *inside* the built image, scans the image (GCR/Trivy), or a CD job to deploy to Cloud Run. The prior supply-chain recommendation (SBOM, pinning by digest) is unimplemented. Tests validate source but not the artifact that actually ships.
* **Proposed:** add a `build-and-scan` job that (1) builds the image, (2) runs `pytest` in it, (3) runs a container scanner + `pip-audit` against the locked deps already present, (4) optionally pushes to Artifact Registry with digest pin + SBOM attestation. Add a separate `deploy` job gated on main+manual approval.

---

### [Minor] - `geometry.py` cyclotorsion sign/doc mismatch is untested; floating-point wraparound untested

* **Location:** `geometry.py:54-72`
* **Description:** The docstring says positive = "clockwise as displayed" under a y-down convention, but the math computes `atan2(dy, dx)` differences with no test asserting the *sign direction*. For a clinical tool the sign convention is loaded: an inverting error changes a "clockwise" instruction (relevant to toric axis) into counter-clockwise. There is no test for the `≅180°` wraparound seam either (the module's own most subtle code path).
* **Proposed:** add unit tests that pin down the sign convention for concrete upright/rotated landmark pairs and the 179°→-179° wrap, and document the clinical meaning of positive vs negative explicitly with a worked example.

---

### [Minor] - `metrics.py` counters/histograms are not increment-safe under concurrency

* **Location:** `metrics.py:38-39, 70-76`
* **Description:** `Counter.inc` and `Histogram.observe` perform unlocked `+=` on shared floats. The registry locks only for *creation* (`make_counter`/`make_histogram`), not for *increments*. Under concurrent `/detect` requests (explicitly supported), two threads can both read `_sum` then write, losing an update. The error is silent and baked into dashboards.
* **Proposed:** use `threading.Lock` (or `itertools`/`decimal`-free atomic via a small locked setter) inside `inc`/`observe`, or move to `prometheus_client`. Regression-test with a threaded increment burst.

---

### [Minor] - `_image_dimensions` returns `(512, 512)` on undecodable images, but `detect` continues with the detector

* **Location:** `app.py:375-404`
* **Status: FIXED** — undecodable/corrupt images now fail fast with **400**
  ("Image is corrupt or could not be decoded") + a distinct `detect.image_decode_failed`
  log event; decompression bombs still 413.
* **Description:** If the image can't be parsed for dimensions (corrupt but MIME-claimed PNG), `_image_dimensions` silently returns `(512, 512)` and `run_detection` proceeds to decode it via Gemini. This is a recoverable-but-noise source: `detect.start` logs width/height that differ from reality, and the detector re-decodes a file already known to be problematic. Better to fail the request with 400 (corrupt image) than to run a paid Gemini call on it, or at least log a distinct "undecodable" event.

---

### [Minor] - `checkImageQuality` leaks the `ImageBitmap` on early-return paths

* **Location:** `frontend/js/common.js:41-58`
* **Status: FIXED** — `bitmap.close()` moved to a `try/finally`, releasing the
  ImageBitmap on every path including early returns and exceptions.
* **Description:** `bitmap.close()` is only invoked on the full-analysis success path. Early returns (`w < 40 || h < 40`) skip the close. Browsers GC the handle eventually, but under repeated uploads (the exact usage pattern here) this is an unmanaged resource that can temporarily pin memory. Close it in a `finally`.
* **Proposed:** restructure with `try { ... } finally { bitmap.close(); }`.

---

## 3. Strategic Recommendations & Roadmap

### Immediate Fixes (Next Sprint)
1. **Fix the concurrency-slot leak** — wrap acquisition+release in a single `with concurrency_slot(request)` so no dependency-abort can leak a semaphore; add a regression test.
2. **Make `current_approved_user` depend on `Depends(current_user)`** to eliminate the duplicate per-request Firebase verification on `/detect`, `/stats`, `/admin/*`.
3. **Fail closed on CORS in production** when `CC_CORS_ORIGINS` is unset, and log when the dev-origin fallback is active.
4. **Add targeted tests:** geometry sign + wraparound; metric increment safety; concurrency slot release on 429/401.

### Medium-Term Refinement (Next 1-2 Months)
5. **Move rate/concurrency/analytics state to a shared backing store** OR hard-enforce `min-instances=1` via a Terraform `check`; stop documenting the multi-replica bypass as acceptable. — **DONE (enforce path)**: Terraform `check "rate_limits_require_single_engine"` + `min_instances` var. Shared-store migration still an option for horizontal scale (rec. #11).
6. **Offload telemetry + analytics writes off the request hot path** (bounded background queue / batched exporter) so Trace/BQ latency never adds to clinical-response tail latency. — **DONE**: `background.py` bounded worker; trace flush + analytics write both enqueued.
7. **Treat Gemini landmark descriptions as untrusted:** validate/redact on ingest, escape at render, and add a central "response field allow-list" FastAPI boundary so internal text cannot reach clients. — **DONE**: `text.sanitize_text` (ingest + response boundary) + `resp_boundary.RESPONSE_ALLOW_LIST`.
8. **Add a central error-boundary policy** (single exception handler + a test that asserts no `detail`/response string contains `projects/`, `SELECT`, `.json`, or resource names). — **DONE**: `resp_boundary.register_exception_handlers` + leak-assertion tests.

### Long-Term Architectural Vision (Future-proofing & scale)
9. **Reproducible artifact pipeline:** pin images by digest, emit SBOM + sign with KMS, run the test suite *inside* the built container, add GCR/Trivy scan, and wire a gated CD job to Cloud Run — closing the origin→deploy trust chain.
10. **Introduce a proper observability substrate** (OpenTelemetry SDK + OTLP to the existing managed Prometheus/Cloud Trace) replacing the home-grown span registry, once the single-instance constraint is lifted.
11. **Add horizontal-scale correctness:** Firestore/Memorystore-backed rate + concurrency + analytics; a real shared analytics store for cluster-consistent `/stats`; evaluate a message queue for the detect event stream so writes are truly fire-and-forget.
12. **Clinical-input telemetry:** instrument the count/sign of emitted cyclotorsion decisions against downstream toric-axis recommendations, and add data-validity checks so the "clinically trusted" `/stats` aggregates can never silently read as empty.
13. **Support future AI-provider / on-prem inference:** the `LandmarkDetector` facade already permits this — keep prompt-versioning, model-metrics, and a fallback chain (Gemini → mock) as first-class concerns so the swap stays a config change.

---

*Appendix — files most relevant to the critical findings:*
- `backend/cyclotorsion/app.py` (`detect`, `_classify_storage_error`, `_image_dimensions`)
- `backend/cyclotorsion/auth.py` (`current_user` / `current_approved_user` / `verify_id_token`)
- `backend/cyclotorsion/concurrency.py` / `rate_limit.py`
- `backend/cyclotorsion/config.py` (`cors_origin_list`, `is_production`)
- `backend/cyclotorsion/storage.py`, `backend/cyclotorsion/metrics.py`, `backend/cyclotorsion/tracing.py`
- `frontend/js/api.js`, `frontend/js/common.js`, `frontend/js/pages/*`
- `.github/workflows/ci.yml`, `infra/terraform/cloudrun.tf`

---

# Remediation Status — Immediate Fixes (all four implemented)

1. **Concurrency-slot leak (Critical) — FIXED.**
   Acquisition moved out of the `concurrency_limit` FastAPI dependency into a
   new `acquire_concurrency(request, user)` called **inside** the `/detect`
   handler's `try` block, so a later dependency abort (rate-limit 429 / auth
   401) can never leave an acquired slot unreleased — the matching `finally`
   always releases. `concurrency.py` now exposes `acquire_concurrency`; the old
   dependency is removed. Regression: `test_detect_rate_429_does_not_leak_concurrency_slot`
   (enables rate-limit burst 1 + concurrency 1, fires a 429, then asserts a
   follow-up run completes 200 instead of concurrency-429'ing).

2. **Duplicate Firebase verify (Major) — FIXED.**
   `current_approved_user` now takes `user: UserContext = Depends(current_user)`
   instead of calling `current_user(request)` directly, so FastAPI caches the
   verification and `/detect`, `/stats`, `/admin/*` verify the ID token **once**
   per request instead of twice. Headless callers in `test_facility.py` updated
   to pass a `UserContext` directly.

3. **CORS fail-closed in production (Major) — FIXED.**
   `cors_origin_list` now returns an **empty** allowlist when `is_production`
   and `CC_CORS_ORIGINS` is unset (no dev-origin fallback), and `app.py` logs a
   CRITICAL `app.production_cors_empty` at import so the misconfiguration is
   visible. Dev behavior unchanged. Tests: `test_cors_origin_list_production_empty_is_fail_closed`,
   `test_cors_origin_list_prod_alias_also_fail_closed`.

4. **Regression tests (Minor items) — ADDED.**
   - `geometry.py`: pinned the clock/counter-clockwise **sign convention** and
     the **-180 seam wrap** (`test_cyclotorsion_cw_rotation_is_positive`,
     `test_cyclotorsion_ccw_rotation_is_negative`,
     `test_cyclotorsion_sign_is_antisymmetric`,
     `test_cyclotorsion_wrap_negative_side_reports_small_angle`).
   - `metrics.py`: `Counter.inc` / `Histogram.observe` are now **lock-guarded**
     so concurrent `/detect` writes cannot lose updates
     (`test_counter_increment_safe_under_concurrency`,
     `test_histogram_observe_safe_under_concurrency` — 8 threads × N increments
     with a start barrier assert exact totals).
   - Concurrency guard pairing committed as `test_acquire_release_pairing_recovers_full_capacity`.

## Remediation Status — Batch 2 (remaining Major + Minor findings)

5. **Move rate/concurrency/analytics state to shared store OR enforce
   `min-instances=1` via Terraform `check` (Major) — DONE (enforce path).**
   Added an explicit `min_instances` variable (default 1) wired into the Cloud
   Run `scaling` block, plus a new Terraform `check
   "rate_limits_require_single_engine"` that fails the plan whenever
   `min_instances>1` unless the operator sets `allow_multi_replica_rate_limits=true`.
   This hard-stops the silent multi-replica bypass of the in-memory rate limiter
   / concurrency guard (and in-memory `/stats` split) unless the operator has
   deliberately acknowledged moving to a shared store. `infra/terraform/variables.tf`,
   `cloudrun.tf`, `main.tf`.

6. **Offload telemetry + analytics writes off the request hot path (Major) —
   DONE.** New `background.py` provides a bounded (`maxsize=512`) FIFO worker
   drained by a single daemon thread; `submit()` is non-blocking and drops+logs
   when full (telemetry backpressure can never stall or balloon the handler).
   - **Cloud Trace:** `Tracer.end()` now enqueues the exporter flush on the
     background worker instead of calling the synchronous `batch_write_spans`
     inline in the request `finally`. `Tracer` gained `flush_background()` for
     deterministic shutdown/tests.
   - **Analytics (BigQuery) write:** `/detect` enqueues `write_row` on the
     worker; the clinical result no longer waits on the insert. The failure is
     recorded via `bigquery_write_failure_rate` + server-side log (never echoed
     to a client — the per-request `logging_warning`/`logging_warning_code`
     response fields were removed as part of this change). `flush_analytics()`
     drains the worker for tests / graceful shutdown.
   Tests updated to drain workers (`-o addopts=""` friendly).

7. **Treat Gemini landmark descriptions as untrusted (Major) — DONE.**
   New `text.sanitize_text` neutralizes model-produced free text at **two**
   layers: (a) **ingest** — `_parse_landmark` sanitizes the Gemini description
   before it becomes a `Landmark`; (b) **response boundary** — `to_client_dict`
   sanitizes both landmark descriptions and the `warning`. It strips control
   characters, collapses whitespace, drops `<`/`>` markup delimiters, and caps
   length. The SPA already renders solely via `textContent` (`el()` throws on
   `html:`), so render-escape is satisfied; the backend sanitize is defense-in-depth.

8. **Central error-boundary policy (Major) — DONE.** New `resp_boundary.py`
   installs FastAPI exception handlers (`HTTPException`,
   `StarletteHTTPException`, `RequestValidationError`) that scrub every
   `detail` of internal fragments (`projects/`, `SELECT`, paths, tracebacks…)
   and replace-leak with a generic message (fail closed). A documented
   `RESPONSE_ALLOW_LIST` names the canonical client-visible fields. Regression
   tests assert no `projects/`/`SELECT` leak through raised errors or 422s.

**Minor findings**
- **`_image_dimensions` corrupt fallback (Minor) — DONE.** An undecodable/corrupt
  image now raises **400** ("Image is corrupt or could not be decoded") with a
  distinct `detect.image_decode_failed` log event instead of silently returning
  `(512,512)` and paying a Gemini call on a known-bad file. Decompression bombs
  still 413.
- **`checkImageQuality` ImageBitmap leak (Minor) — DONE.** Bitmap is now closed in
  a `try/finally` so every path (early returns + exceptions) releases it.

## Verification
- Backend: **218 passed**, coverage **97.79%** (threshold 90%).
- `ruff check .` and `ruff format --check .` clean (backend + repo root).
- Frontend: **7 tests pass**, ESLint + Prettier clean.

*Remaining roadmap items (not in this sprint, see Strategic Recommendations):
reproducible artifact pipeline — CI Docker build/test-in-container/scan + gated
CD job to Cloud Run (recommendation #9); proper OTel observability substrate
(#10); horizontal-scale correctness with a shared store + message queue (#11);
clinical-input telemetry (#12); AI-provider fallback/prompt versioning (#13).
`terraform validate` itself still needs a local Terraform install to run live.*