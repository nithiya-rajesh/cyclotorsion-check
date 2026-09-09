# CyclotorsionCheck — Production Security Audit & Threat Model

**Scope:** Full backend (`backend/cyclotorsion/`), frontend SPA (`frontend/`), IaC
(`infra/terraform`, `infra/gcloud`), CI (`ci.yml`), manifests, and Dockerfiles.
**Date:** 2026-09-03 · **Reviewer role:** Staff AppSec / DevSecOps Architect.

---

## 1. Executive Security Summary

* **Overall Security Posture: Medium Risk — otherwise exemplary engineering, but several**fail-open** or scoping defects could expose the clinical decision-support endpoint if not corrected.**
  The codebase is unusually disciplined: parameterized BigQuery queries, no SQL string
  interpolation, no `innerHTML`/`eval` on any data path (XSS-robust by construction), no
  hardcoded secrets (verified via repo-wide grep), secrets via Secret Manager + IAM, correct
  CORS (exact origins, not `*`), fail-closed Firebase token verification, per-user rate &
  concurrency limiting, and defense-in-depth layering. The material issues are **authorization
  scoping**, **a fail-open security default**, and **error/info disclosure**, not injection or
  secrets mismanagement.

* **Key Threat Vectors (most likely exploitation avenues):**
  1. **Unprotected-exposure via misconfiguration / fail-open default** — `CC_AUTH_ENABLED`
     defaults to `False`, and every endpoint (incl. `/admin/*`, `/detect`, `/stats`) runs
     **open** in that mode; Cloud Run is deployed `--allow-unauthenticated`. A config typo or
     a fresh deploy without auth wired removes all access control.
  2. **Broken access control / excessive data exposure in the admin tier** — a `facility_admin`
     can enumerate **all** Firebase accounts (`/admin/users`) and, lacking a `facility_id`
     claim themselves, approve users at **any** facility.
  3. **Resource-exhaustion / cost-bleed** — the global rate & concurrency limits are
     **in-memory and per-instance**; with `max_instance_count=10` an attacker trivially
     multiplies the effective budget 10×, and `/detect` drives paid Gemini quota.
  4. **Information disclosure via error echo** — the raw BigQuery exception string is returned
     verbatim in the `/detect` response `logging_warning` field.
  5. **Decompression-bomb DoS** — untrusted Pillow-parsed images with no pixel-count cap.

* **Top 3 Immediate Priorities (hotfix order):**
  1. **Harden the fail-open default + deployments.** Make `auth_enabled` fail-closed (or gate
     `/detect`/`/stats`/`/admin` behind an explicit allow-list when disabled) and require
     non-unauthenticated Cloud Run ingress in prod. *(HIGH)*
  2. **Fix admin-tier authorization scoping.** Scope `/admin/users` to the approver's
     facility and forbid a claim-less `facility_admin` from approving cross-facility.
     *(HIGH)*
  3. **Stop echoing raw exception strings** in API responses and harden image decoding
     against decompression bombs. *(MEDIUM→HIGH)*

---

## 2. Security Deep Dive & Code Review

### HIGH

#### [HIGH] - CWE-306 / CWE-287 - Missing Authentication When Auth Is Disabled (Fail-Open Default)
* **Location:** `backend/cyclotorsion/config.py:70` (`auth_enabled: bool = False`),
  `auth.py:152` (`if not cfg.auth_enabled: return UserContext(uid=None)`),
  `auth.py:167-173` & `188-199` (roles permit all in open mode),
  `infra/terraform/cloudrun.tf:148-153` (public invoker), `infra/gcloud/deploy.sh:71` (`--allow-unauthenticated`).
* **Exploit Scenario:** The default `CC_AUTH_ENABLED=false` causes `current_approved_user` to
  return an *unauthenticated* context, `require_any_role`/`require_role` to permit **every**
  caller, and `current_user` to never verify Firebase. Because Cloud Run is
  `--allow-unauthenticated`, **anyone on the internet** reaching the service gets `405/403`-
  free access to `/detect` (a paid, decision-support LLM call) and `/stats`. A single typo
  (`CC_AUTH_ENABLED= flase` → parsed as `False`), a misfiled env var, or a sandbox deploy with
  default Terraform input silently removes **all** access control while still looking deployed.
  Business impact: full public access to the clinical-decision endpoint, unauthenticated paid
  Gemini spend, and exposure of aggregate stats.
* **Vulnerable Code Snippet:**
  ```python
  # auth.py
  def current_user(request: Request) -> UserContext:
      if not cfg.auth_enabled:            # fail-open default
          return UserContext(uid=None)     # treated as "permitted"
      token = _extract_token(request)
      return _verify_with_firebase(token)
  ```
  ```hcl
  # cloudrun.tf — public invoker relying solely on the app-level switch
  resource "google_cloud_run_v2_service_iam_member" "public" {
    role   = "roles/run.invoker"
    member = "allUsers"        # no IAP/identity-gate; app flag is the only guard
  }
  ```
* **Remediated Code Snippet:**
  ```python
  def current_user(request: Request) -> UserContext:
      if not cfg.auth_enabled:
          # Fail closed in any non-test context: reject, don't open the door.
          if not cfg.auth_allow_open_dev and cfg.env != "test":
              raise HTTPException(status_code=401, detail="Authentication is disabled")
          return UserContext(uid=None)
      token = _extract_token(request)
      return _verify_with_firebase(token)
  ```
  ```hcl
  # cloudrun.tf — never expose unauthenticated in prod; drive via a bool var
  variable "public_ingress" { type = bool; default = false }
  resource "google_cloud_run_v2_service_iam_member" "public" {
    count  = var.public_ingress ? 1 : 0   # default off
    role   = "roles/run.invoker"
    member = "allUsers"
  }
  ```
  ```bash
  # deploy.sh — require auth explicitly and refuse unauthenticated by default
  : "${CC_AUTH_ENABLED:=true}"   # deployment must be explicit
  gcloud run deploy ... $( [[ "$CC_AUTH_ENABLED" == "true" ]] && echo --allow-unauthenticated )
  ```
* **Remediation Guidance:**
  1. Invert the default: `auth_enabled` should be a *required* variable with no permissive
     default for production, or the open path must only be reachable when an explicit
     `CC_ENV=test/dev` is set (never silently on prod).
  2. Add a startup assertion: if `not cfg.auth_enabled` and not running under tests, log a
     CRITICAL and refuse to serve protected routes (or fail fast at import).
  3. In Terraform, make the public-invoker binding opt-in (`public_ingress = false` default)
     and prefer IAP / GCLB-backed ingress in prod instead of `allUsers`.
  4. In CI, add a test that asserts every protected route *rejects* an anonymous caller in
     the auth-enabled config (regression guard for the fail-closed contract).

#### [HIGH] - CWE-200 / CWE-639 - Broken Access Control & Excessive Data Exposure in Admin Tier
* **Location:** `backend/cyclotorsion/provisioning.py:142-159` (`list_users` of
  `FirebaseApprovalStore` iterates **all** accounts), `app.py:269-283` (`/admin/users` has no
  facility scoping), `provisioning.py:75-82` (`_scope_facility` returns
  `user.facility_id or facility_id` — a claim-less admin can pick any facility),
  `app.py:286-317` (`/admin/approve`).
* **Exploit Scenario:** A low-privileged `facility_admin` at facility A can:
  * Call `GET /admin/users` and enumerate **every** Firebase account across all facilities —
    leaking emails, UIDs, roles, and facility ids (CWE-639 / excessive data exposure).
  * Post `/admin/approve` with `facility_id = "facility-B"`. Because `_scope_facility` does
    `user.facility_id or facility_id`, and an admin who was provisioned without a
    `facility_id` claim has `user.facility_id is None`, the attacker-supplied
    `facility_B` is used verbatim — granting approval **outside** their own facility.
    This is a privilege/scope escalation enabling lateral administration across facilities.
* **Vulnerable Code Snippet:**
  ```python
  def _scope_facility(user, facility_id):
      if cfg.auth_enabled and user.role == ROLE_FACILITY_ADMIN:
          if user.facility_id and facility_id != user.facility_id:
              raise HTTPException(403, "Facility admins may only approve their own facility")
          return user.facility_id or facility_id   # <-- trust the caller if admin has no claim
      return facility_id
  ```
  ```python
  @app.get("/admin/users")
  def admin_list_users(user=Depends(require_any_role(ROLE_FACILITY_ADMIN, ROLE_PROGRAM_OFFICER)), ...):
      store = get_approval_store()
      return [record.to_dict() for record in store.list_users()]   # no facility filter
  ```
* **Remediated Code Snippet:**
  ```python
  def _scope_facility(user, facility_id):
      if cfg.auth_enabled and user.role == ROLE_FACILITY_ADMIN:
          if not user.facility_id:
              raise HTTPException(403, "Your account has no facility; contact a program officer")
          if facility_id != user.facility_id:
              raise HTTPException(403, "Facility admins may only approve their own facility")
          return user.facility_id      # never trust caller's facility for facility_admin
      return facility_id
  ```
  ```python
  @app.get("/admin/users")
  def admin_list_users(user=Depends(require_any_role(ROLE_FACILITY_ADMIN, ROLE_PROGRAM_OFFICER)), ...):
      store = get_approval_store()
      if cfg.auth_enabled and user.role == ROLE_FACILITY_ADMIN:
          records = [r for r in store.list_users() if r.facility_id == user.facility_id]
      else:
          records = store.list_users()          # program_officer sees all (by design)
      return [record.to_dict() for record in records]
  ```
* **Remediation Guidance:**
  1. Make `_scope_facility` reject a `facility_admin` that carries no `facility_id` claim
     (fail closed), and always use the verified claim as the authoritative value — never the
     caller-supplied `facility_id`.
  2. Filter `/admin/users` by `user.facility_id` for `facility_admin` so they only see their
     own facility's accounts.
  3. Add unit tests: a `facility_admin` with no claim receives 403 on approve; a
     `facility_admin` sees only own-facility users; a `program_officer` retains cross-facility.
  4. Validate `uid` format (Firebase UIDs are base64url, ≤128 chars) and `facility_id` against
     a known allow-list/prefix before writing custom claims.

#### [MEDIUM] - CWE-209 - Sensitive Information Disclosure: Raw Exception Echoed in API Response
* **Location:** `backend/cyclotorsion/app.py:200` (`response["logging_warning"] = f"analytics write failed: {write_error}"`),
  `app.py:196-199`, `backend/cyclotorsion/storage.py:117,173` (raw `str(exc)` logged),
  `backend/cyclotorsion/secrets.py:57` (raw `str(exc)` logged).
* **Exploit Scenario:** A transient BigQuery failure causes `BigQueryWriter.write_row` to return
  the exception; `app.py` embeds `str(write_error)` into the **response body** a caller can
  read. Google/BigQuery exceptions include the SQL fragment (`SELECT test_id, ... FROM ...`),
  resource names (`projects/.../results`), and sometimes full caller paths. This leaks internal
  schema, project/resource identifiers, and implementation details to any authenticated caller
  (and, in fail-open mode, to the public). Logged `str(exc)` also risks ending up in
  Cloud Logging with the API-key/secret deeper in the chain on a resolve failure.
* **Vulnerable Code Snippet:**
  ```python
  if write_error is not None:
      logger.warning("detect.log_write_failed", extra={... "error": str(write_error)})
      response["logging_warning"] = f"analytics write failed: {write_error}"  # raw, to client
  ```
* **Remediated Code Snippet:**
  ```python
  if write_error is not None:
      # Log the full detail server-side only.
      logger.warning("detect.log_write_failed", extra={... "error": type(write_error).__name__},
                     exc_info=True)
      # Return a generic, safe message with no internals.
      response["logging_warning"] = "analytics write failed"
      response["logging_warning_code"] = _classify_error(write_error)  # e.g. "storage_unavailable"
  ```
  In `secrets.py`/`storage.py`, log `type(exc).__name__` + a sanitized reason, never the full
  `str(exc)` when it can embed credentials/resource paths:
  ```python
  except Exception as exc:
      logger.warning("storage.bigquery_read_failed",
                     extra={"event": "storage.bigquery_read_failed", "table": self._table,
                            "error_type": type(exc).__name__}, exc_info=True)
      return []
  ```
* **Remediation Guidance:**
  1. Remove raw exception strings from any response payload; replace with a stable, generic
     classification (e.g. `"storage_unavailable"`, `"remote_timeout"`).
  2. Log the full original exception to Cloud Logging with `exc_info=True` so the server trail
     retains diagnostics, but never bind the raw text to a client-visible field.
  3. If an exception message is ever needed again, cap length and sanitize (strip
     `projects/`, `gcloud`, credential-bearing content) before exposure.

#### [MEDIUM] - CWE-400 - Decompression-Bomb / Large-Pixel DoS in Image Parsing
* **Location:** `backend/cyclotorsion/app.py:320-330` (`_image_dimensions` uses `PIL.Image.open`
  with no pixel cap), `detector.py:78-92` (`MockLandmarkDetector` `.convert("L")`/`.resize`),
  `detector.py:219-222` (`_image_part` passes bytes to Gemini).
* **Exploit Scenario:** The 10 MB byte cap (`MAX_IMAGE_BYTES`) bounds the *encoded* size but not
  the *decoded* pixel count. A tiny (e.g. 5 KB) PNG that declares 20000×20000 px (or a
  multi-frame image) is parsed by PIL's full decoder in `_image_dimensions` and
  `MockLandmarkDetector` (`.convert("L")` materializes it), consuming hundreds of MB of memory
  per request — exhausting the 512 MiB Cloud Run replica (remote DoS) and, in production, the
  bytes are also forwarded to Gemini. This is a classic decompression bomb.
* **Vulnerable Code Snippet:**
  ```python
  def _image_dimensions(image_bytes):
      with Image.open(io.BytesIO(image_bytes)) as img:
          return img.size         # parses header only, but callers later fully decode
  ```
  ```python
  # detector.py (mock)
  img = Image.open(io.BytesIO(image_bytes)).convert("L")   # full decode, unbounded pixels
  img = img.resize((max(1, width // 2), max(1, height // 2)))
  ```
* **Remediated Code Snippet:**
  ```python
  from PIL import Image
  Image.MAX_IMAGE_PIXELS = 40_000_000   # fail fast with DecompressionBombError

  MAX_PIXELS = 16_000_000               # ~4096x4096, enough for clinical photos

  def _validate_image(image_bytes: bytes) -> tuple[int, int]:
      try:
          with Image.open(io.BytesIO(image_bytes)) as img:
              w, h = img.size
              if w * h > MAX_PIXELS:
                  raise HTTPException(413, "Image dimensions exceed the safe limit")
              img.verify()              # force full decode + integrity check safely
              return w, h
      except Image.DecompressionBombError:
          raise HTTPException(413, "Image appears to be a decompression bomb")
      except Exception:
          raise HTTPException(400, "Could not decode image")
  ```
* **Remediation Guidance:**
  1. Set `Image.MAX_IMAGE_PIXELS` and a module-level `MAX_PIXELS` cap; check `w*h` before any
     `.convert`, `.resize`, or forwarding to Gemini; call `img.verify()` on a copied stream.
  2. Reject multi-frame/oversized images with 413 early, before the payload is sent upstream.
  3. Unit-test with a crafted high-pixel low-byte image to assert 4xx and bounded memory.

---

### LOW

#### [LOW] - CWE-672 - Token Revocation Not Checked on Firebase Verification
* **Location:** `backend/cyclotorsion/auth.py:126` (`fb_auth.verify_id_token(token)`).
* **Exploit Scenario:** A signed-out/suspended/approved-then-declined user's token stays valid
  until its natural expiry (~1 hour by default) because `check_revoked` is not set. De-
  provisioned clinicians keep access for up to an hour after revocation.
* **Vulnerable Code Snippet:** `decoded = fb_auth.verify_id_token(token)`
* **Remediated Code Snippet:** `decoded = fb_auth.verify_id_token(token, check_revoked=True)`
  (Wrap the new `RevokedIdTokenError` in the existing 401 handler; enabled by default once
  deployment refreshes the token.)
* **Remediation Guidance:** Set `check_revoked=True`, handle `firebase_admin.auth.RevokedIdTokenError`
  → 401 with `WWW-Authenticate`, and document the ~1hr residual window already covered by the
  401-on-bad-token path.

#### [LOW] - CWE-404 / In-Memory Limits are Per-Instance — Distributed Bypass
* **Location:** `rate_limit.py:95-108`, `concurrency.py:59-69` (process-local `_limiter`/`_guard`),
  `cloudrun.tf:35` (`max_instance_count = 10`), `cloudrun.tf:32` (`timeout = "55s"` unconditional).
* **Exploit Scenario:** Rate and concurrency budgets live in each replica's memory and are never
  shared. With up to 10 instances, an attacker rotating behind a proxy (IP-only keying in open
  mode) or spreading requests across replicas multiplies the effective limit ~10×, draining
  Gemini quota and inflating cost. Per-request 55s timeout also permits long-lived head-of-line
  blocking against a shared upstream.
* **Vulnerable Code Snippet:** `self._buckets: dict = store or {}` (per-process dict, no sharding).
* **Remediated Code Snippet (design):** back the limiter/guard with a shared store when
  `min_instances/u>1`:
  ```python
  # Use Cloud Memorystore / Firestore for the token bucket when multi-instance.
  def get_limiter():
      store = _distributed_store() if cfg.rate_limit_shared else None
      return MemRateLimiter(per_minute=cfg.rate_limit_per_minute, burst=cfg.rate_limit_burst, store=store)
  ```
* **Remediation Guidance:**
  1. When `max_instance_count > 1` (it is), move rate limiting to a shared store and key by
     `uid` (auth) rather than IP (which is trivially rotated behind proxies).
  2. Lower/derive `timeout` from expected worst-case Gemini latency rather than a fixed 55s;
     keep the circuit breaker's cooldown consistent.
  3. Document the current single-instance assumption in Terraform output so a future scale-out
     flips on a shared limiter (already partially documented).

#### [LOW] - CWE-732 / Missing Key-Management & Bucket IAM Hardening for PHI
* **Location:** `bigquery.tf:12-33` (BQ default encryption), `backup.tf` / bucket (default
  GCS-managed AES-256), `deploy.sh:66-67` (bucket created, objectAdmin on backup SA).
* **Exploit Scenario:** Data is currently synthetic, but the PRD explicitly plans real patient
  data later. With default provider-managed keys and no explicit read-role minimisation on the
  backup bucket / BQ dataset, a future incident (stolen SA key) has no added CMEK revocation
  layer, and there's no documented read rebinding.
* **Remediated Code Snippet (HCL):**
  ```hcl
  resource "google_storage_bucket" "backups" {
    ...
    encryption {
      default_kms_key_name = google_kms_crypto_key.backup_ckek.id   # CMEK
    }
  }
  # Bind only the backup SA, and a read audit role — never allUsers/allAuthenticatedUsers.
  ```
* **Remediation Guidance:**
  1. Before accepting real PHI, switch BQ + GCS to CMEK with a rotation policy and store the
     KMS key in the same IAM-governed project.
  2. Add an explicit read IAM binding to the backup bucket for the backup SA only; ensure no
     public `allUsers`/`allAuthenticatedUsers` role exists (currently none).
  3. Relocate to an India region (`asia-south1/2`) before real patient data, as already
     documented (`variables.tf`, `main.tf`), and enforce that residency in `plan` validation.

#### [LOW] - CWE-79 (Absent-on-review) - XSS surface is currently contained; keep the invariant
* **Location:** `frontend/js/common.js:13-21` (`el(...)` defaulting to `textContent`;
  `"html":` key is the *only* `innerHTML` sink), all pages render server data via `text:`.
* **Exploit Scenario:** None today — the only `innerHTML` assignments are `view.innerHTML = ""`
  (clearing) and the unused `html:` key in `el()`. All server-derived strings (Angles,
  `upright/rotated_landmark`, `sanity_flags`, `warning`, stats numbers) flow through
  `node.textContent`, which is inert to HTML/attribute injection even if an LLM returns a
  hostile description (already capped at 200 chars).
* **Remediated Code Snippet (ensure the rail stays closed):**
  ```js
  // common.js — harden the helper so `html:` can never be passed accidentally.
  export function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") throw new Error("el() forbids HTML injection; use textContent"); // kill the sink
      else node.setAttribute(k, v);
    }
    ...
  }
  ```
* **Remediation Guidance:** Remove the unused `html:` branch entirely (or throw) so future
  renderers can't regress into `innerHTML`. Re-run ESLint with `eslint-plugin-unsafe`
  (react/next not used; use `no-unsanitized`) to flag any future `innerHTML`. Note this is a
  **defense-in-depth hardening** and the app is not currently vulnerable.

---

## 4. Security Vectors — Confirmed Good / No-Defect Summary

The following vectors were **verified as clean** during this audit and should be treated as
already-satisfied requirements (no action required):

* **SQL injection (CWE-89):** The only SQL is `BigQueryWriter.read_rows`
  (`storage.py:151-156`). The user-controlled `facility_id` is passed via a
  `bigquery.ScalarQueryParameter` named parameter, never string-concatenated. `use_legacy_sql=False`
  is set. The interpolated table name comes from config (`CC_BIGQUERY_TABLE`), not user input.
  **Not injectable.**
* **Command injection (CWE-78):** No `os.system`/`subprocess`/`shell=True` on any input path.
* **SSRF (CWE-918):** No user-supplied URLs. The only outbound calls are the fixed Gemini SDK,
  BigQuery, and Secret Manager — all SDK-defined. 
* **XSS (CWE-79):** Confirmed contained (see LOW above).
* **Insecure deserialization (CWE-502):** No `pickle`/`yaml.load`/`eval` on untrusted data.
  `json.loads` on the LLM response is safe, well-scoped, and type-shape-checked
  (`detector.py:225-242`).
* **Hardcoded secrets:** Repo-wide grep found only test literals (`"k"`, `"sekret"`,
  `"env-key"`); no real API keys, SA keys, or tokens are committed. Firebase web config uses
  placeholders (`config.js:33-38`). 
* **Strong authentication (enabled path):** OWASP-recommended server-side Firebase ID-token
  verification via Admin SDK, `Bearer` parse with proper header handling, 401 fail-closed with
  `WWW-Authenticate`, roles read from verified custom claims (`auth.py:116-155`).
* **Authorization layering:** RBAC dependency helpers (`require_any_role`/`require_role`/
  `current_approved_user`) gate `/stats` and `/admin/*`, with per-facility scoping for the
  program path (fixes pending on the admin tier, HIGH above).
* **Transport security:** Cloud Run serves TLS by default; Firebase Hosting → Cloud Run is
  HTTPS. No cleartext transport for production traffic.
* **Privacy in analytics:** `ResultRow` deliberately excludes patient identifiers; stores only
  `test_id` (UUID), numeric angle, landmark description, flags, `user_uid` (clinician), and
  `facility_id`. `/stats` returns aggregates only.
* **Dependency hygiene:** Backend ranges in `requirements.txt`/`pyproject.toml` + a pinned
  `requirements.lock` (validated in CI). Frontend deps are dev-only (lint/test); the runtime
  SDK is a **pinned** Firebase 10.14.1 module from Google's CDN with a fixed version (good
  history), not a mutable `latest`. No known-vulnerable pinned runtime dependencies surfaced by
  inspection; recommended: add `pip-audit`/`npm audit` to CI.
* **Logging:** Structured JSON with per-request `request_id`; events are whitelisted
  (`extra={"event": ...}`); no raw creds logged in the normal path. Residual `str(exc)` logging
  addressed in the MEDIUM above.

---

## 3. DevSecOps & Security Hardening Roadmap

### Immediate Guardrails (block today's exploits)
* **Fix the three HIGH/MEDIUM above first**: (1) fail-closed auth default + opt-in public
  ingress; (2) admin-tier facility scoping & `_scope_facility` fail-closed; (3) strip raw
  exception text from responses + cap decoded image pixels.
* Add `check_revoked=True` to Firebase verification.
* Replace the `el()` `html:` sink with a throw.
* Turn on rate/concurrency limiting in *every* deploy template (they're already wired in IaC —
  keep them; don't ship local-defaults to prod).
* Gate Terraform `plan` on `region ∈ {asia-south1, asia-south2}` and
  `public_ingress = false` whenever real patient data is configured.

### SAST/DAST Tooling Integration
* **Python (backend/CI):** `pip-audit` (CVE scan of `requirements.lock`),
  `bandit` (secrets/weak-crypto patterns — catches key-in-`insert_rows_json`-adjacent code),
  `semgrep` (rule sets: `p/python`, `p/owasp-top-ten`) for custom rules like
  "raw `str(exc)` in response" and "innerHTML sink". Add `safety` as a fallback.
* **Frontend:** `eslint-plugin-unsafe` + `eslint-plugin-security` (no `innerHTML` regressions);
  `npm audit --omit=dev` in CI.
* **IaC:** `tfsec` / `checkov` (`cloudrun.tf`, `bigquery.tf`, `backup.tf`) to enforce
  non-public ingress, CMEK-on-PHI, and bucket IAM (no `allUsers`).
* **Runtime/DAST:** Cloud Security Scanner (CSR) or OWASP ZAP against the deployed service on
  every prod release: check `/detect`,`/stats`,`/admin/*` unauthenticated responses and
  error-body leakage; verify 401/403/429 on anonymous/insufficient roles.
* **CI wiring:** run `pip-audit`, `bandit`, `semgrep`, `tfsec`, `eslint` unsafe, and the DAST
  smoke in the existing `ci.yml` (or a parallel `security.yml`) so regressions block `main`.

### Long-Term Defense-in-Depth (architectural shifts)
* **Centralized decision / identity gateway:** Put Cloud Run behind an IAP-protected load
  balancer (or a GCLB with IAP) so a config mishap can never expose `/detect` publicly;
  keep `allUsers` out of prod entirely.
* **Shared, distributed limiting + quotas:** Move rate/concurrency budgets (and later, tracing
  correlation) to a shared store (Memorystore) or enforce budget at the API gateway/GCLB layer,
  keyed by verified `uid`, so multi-instance scale can't be abused.
* **Asymmetry in the trust boundary:** Treat record writes as a separate, IAM-scoped path
  (writer SA) and reads as an even-more-scoped path, so a compromise of `/detect` does not
  grant `/stats`/BigQuery read.
* **Detach secrets from the code path:** Fully trust Secret Manager mounts (already partially
  done) and remove fallback-to-`GEMINI_API_KEY` env in `secrets.py` for prod so a leaked env
  isn't a valid credential source.
* **Encryption & residency:** Standardize CMEK (BQ dataset, GCS bucket, KMS key) and assert
  India-region residency in Terraform `precondition` blocks before real PHI onboarding.
* **Observability without duplication:** Because logs already bind a `request_id`, add
  centralized error tracking and a redaction filter so `str(exc)` never persists credentials;
  enforce log retention/access via IAM.
* **Supply-chain posture:** Pin the entire runtime image (digest) via reproducible builds and
  `docker scan`/`gcr scan`; enable SBOM generation and artifact signing (KMS) for the images
  the CI builds so the immutable property holds end to end.

---

*Appendix: Top-3 immediate priorities map to the following artifacts you can open directly:
  `backend/cyclotorsion/config.py`, `auth.py`, `provisioning.py`, `app.py`,
  `infra/terraform/cloudrun.tf`, `backend/cyclotorsion/rate_limit.py`, `concurrency.py`,
  `backend/cyclotorsion/storage.py`.*

---

# Remediation Status (all findings fixed in sequence)

## HIGH
1. **Fail-open default closed (HIGH-1).** Added `Config.app_env` (`CC_APP_ENV`, default
   `development`); in a **production** env (`CC_APP_ENV=production`) `auth.current_user` now
   **fails closed with 401** whenever `auth_enabled=False`, a CRITICAL is logged at start-up
   (`app.production_without_auth`), and all protected routes inherit it via the shared
   `current_user` dependency. IaC now sets `CC_APP_ENV=production` (Terraform + deploy.sh),
   Terraform's `allUsers` invoker is **opt-in** (`var.public_ingress`, default `false`), and
   `deploy.sh` uses `--no-allow-unauthenticated` by default. Tests:
   `test_current_user_production_disabled_auth_fails_closed`, `test_detect_fails_closed_in_production_*`,
   `test_stats_fails_closed_in_production_*`, `test_app_env_*`.
2. **Admin-tier scoping closed (HIGH-2).** `_scope_facility` now **refuses a `facility_admin`
   whose own account lacks a `facility_id` claim** and never trusts a caller-supplied
   `facility_id` for a facility admin. `/admin/users` is filtered to the caller's own facility
   for `facility_admin` (program_officer retains cross-facility). Added `uid`/`facility_id`
   input validation (whitespace/IP characteristics + DNS-label regex). Tests:
   `test_facility_admin_without_claim_cannot_approve_anywhere`, `test_admin_users_facility_admin_scoped_*`,
   `test_scope_facility_*`, `test_validate_uid_*`, `test_validate_facility_id_*`.

## MEDIUM
3. **Raw exception echo removed (MEDIUM-3).** `/detect` now returns a generic
   `logging_warning = "analytics write failed"` + a stable `logging_warning_code`
   (`storage_timeout` / `storage_rate_limited` / `storage_unavailable`); the full exception is
   logged server-side with `exc_info=True`. `storage.py` and `secrets.py` log
   `error_type` instead of raw `str(exc)`. Test:
   `test_detect_write_failure_returns_generic_warning` (asserts no `projects/`/`SELECT` leak).
4. **Decompression-bomb DoS closed (MEDIUM-4).** `_image_dimensions` enforces
   `MAX_IMAGE_PIXELS=16_000_000` (rejecting 413 on oversized / `DecompressionBombError`),
   `verify()`s the image, and sets `PIL.Image.MAX_IMAGE_PIXELS` globally; the mock detector
   also guards pixel count. Test: `test_detect_rejects_decompression_bomb_dimensions`.

## LOW
5. **Firebase token revocation (LOW-5).** `verify_id_token(token, check_revoked=True)`.
   Tests: `test_verify_with_firebase_uses_check_revoked`, `test_verify_with_firebase_revoked_token_fails_closed`.
6. **`el()` HTML sink removed (LOW-6).** `common.js` now throws if `html:` is passed, closing
   the only `innerHTML` sink for future-proofing (no current XSS).
7. **CMEK + residency + bucket IAM (LOW-7).** Terraform `check` blocks assert India-region
   residency (`enforce_india_residency`) and forbid `allUsers` ingress for patient data;
   `use_cmek` encrypts the BQ dataset + backup bucket with a KMS key; an explicit objectAdmin
   binding on the backup bucket scopes it to the backup SA. `deploy.sh` adds
   `ENFORCE_INDIA_REGION` enforcement.
8. **In-memory limit caveat documented (LOW-8).** `infra/README.md` clarifies the
   per-replica rate/concurrency state, the `max-instances` distributed-bypass implication, and
   the shared-store requirement before `min-instances > 1`. Pixel cap backstopped in detector.

## DevSecOps (CI)
- New `security` job in `.github/workflows/ci.yml`: **Bandit** SAST (`backend/cyclotorsion`),
  **pip-audit** (against `requirements.lock`), and **npm audit** (frontend omit dev).
- Added `bandit>=1.7` + `pip-audit>=2.7` to `backend` dev extras; `[tool.bandit]` config with
  documented skips; `# nosec B608` on the config-derived (parameterized) BigQuery query.

## Final verification
- Backend: **190 passed**, coverage **97.25%** (threshold 90%).
- `python -m ruff check .` and `python -m ruff format --check .` clean.
- Frontend: **7 tests pass**, ESLint + Prettier clean.