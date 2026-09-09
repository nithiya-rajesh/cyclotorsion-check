# CyclotorsionCheck — AppSec Deep-Dive & Threat Model

_Date: 2026-09-03_

> **Status update (2026-09-03, same day):** All High and Medium findings are implemented and verified — 221/221 backend tests pass (97.8% coverage), 14/14 frontend tests pass, `ruff` clean, `bash -n` clean on `deploy.sh`, and `ci.yml`/`firebase.json` both parse and validate against their expected schemas. The two Low items were also addressed (base-image digest pin, `--set-env-vars` delimiter fix); the SRI item remains a deliberately accepted tradeoff (see its entry below). One caveat: the Dockerfile/`.dockerignore` changes could not be verified with a live `docker build` — Docker Desktop's daemon isn't running in this environment — so that pair is verified by careful reading against Docker's own documented ignore-pattern semantics, not by an actual build.

## 1. Executive Security Summary

* **Overall Security Posture: Low-Medium Risk** (Grade: **B+**). The application layer is unusually disciplined for its size — parameterized BigQuery access, fail-closed auth/CORS, centralized error scrubbing, output-encoding-only DOM rendering, and no home-rolled crypto (all identity/token verification is delegated to Firebase Admin SDK, avoiding JWT algorithm-confusion classes entirely). No injection, broken-auth, or sensitive-data-exposure vulnerability was found reachable by an external attacker. The residual risk is concentrated in the **container/CI supply chain** and a **deploy-script/IaC consistency gap**, not application logic.
* **Key Threat Vectors:**
  1. **Supply chain via the container build** — no `.dockerignore` and root-user containers mean a compromised/careless local build environment (stray `.env`, cached credentials) could ship secrets into a production image, and a compromised process inside the container runs as `root`.
  2. **Deploy-path inconsistency** — `infra/gcloud/deploy.sh` (the manual alternative to Terraform) hardcodes `--max-instances=10`, silently bypassing the single-replica invariant that Terraform's `check` block now enforces for the in-memory rate limiter/concurrency guard/analytics queue — an operator using the script instead of Terraform re-opens a class of bug already closed in IaC.
  3. **CI token/action over-privilege** — the GitHub Actions workflow has no explicit least-privilege `permissions:` block and pulls third-party actions by mutable tag rather than pinned SHA.
* **Top 3 Immediate Priorities:**
  1. Add `USER` (non-root) to both Dockerfiles and a `.dockerignore` that excludes everything except what's actually needed in the image.
  2. Bring `infra/gcloud/deploy.sh`'s `--max-instances` in line with the Terraform-enforced single-replica invariant (or delete the script in favor of Terraform-only deploys).
  3. Add `permissions: contents: read` (least privilege) to `.github/workflows/ci.yml` and pin third-party Actions to a commit SHA.

---

## 2. Security Deep Dive & Code Review

### [High] - CWE-250: Execution with Unnecessary Privileges (containers run as root) — **Resolved**
_Fix: `docker/api.Dockerfile`, `docker/backup.Dockerfile` — both now create a non-root `app` user, `chown /app`, and switch via `USER app` before `CMD`/`ENTRYPOINT`. Verified by inspection (Docker Desktop's daemon isn't available in this environment to run a live build) — the `groupadd`/`useradd`/`chown`/`USER` sequence is the standard Debian-slim pattern._
* **Location:** `docker/api.Dockerfile`, `docker/backup.Dockerfile` (no `USER` directive)
* **Exploit Scenario:** Both images end with `CMD`/`ENTRYPOINT` running as the default `root` user inside the container. If an attacker achieves code execution through *any* future dependency vulnerability (e.g. a Pillow/`google-genai` CVE processing an untrusted image), they inherit root inside the container rather than a restricted service account — widening the blast radius for container-escape or privilege-escalation chains, and violating the CIS Docker Benchmark / Cloud Run security baseline.
* **Vulnerable Code Snippet:**
  ```dockerfile
  FROM python:3.13-slim
  ...
  RUN pip install --upgrade pip && ...
  EXPOSE 8080
  CMD ["uvicorn", "cyclotorsion.app:app", "--host", "0.0.0.0", "--port", "8080"]
  ```
* **Remediated Code Snippet:**
  ```dockerfile
  FROM python:3.13-slim
  ...
  RUN pip install --upgrade pip && ...
  RUN groupadd --system app && useradd --system --gid app --no-create-home app \
      && chown -R app:app /app
  USER app
  EXPOSE 8080
  CMD ["uvicorn", "cyclotorsion.app:app", "--host", "0.0.0.0", "--port", "8080"]
  ```
* **Remediation Guidance:** Add a non-root `app` user in both Dockerfiles, `chown` `/app` before switching, and verify the app still binds/reads correctly (`docker run --rm <image> id` should show the `app` UID, not `0`). Cloud Run itself already runs containers under a constrained sandbox (gVisor), so this is defense-in-depth, not the only control — but it's a one-line fix with no downside.

### [High] - CWE-538: File and Directory Information Exposure via missing `.dockerignore` — **Resolved**
_Fix: added `.dockerignore` at the repo root using Docker's own documented `*` + `!backend/` `!scripts/` re-include pattern, plus explicit excludes for `.env*`, `*.json` (no legitimate `.json` files exist under `backend/`/`scripts/` today — verified by search), caches, and credential-shaped filenames. Not verified with a live `docker build` (Docker Desktop unavailable here) — verified by inspection against Docker's documented ignore semantics instead._
* **Location:** `docker/api.Dockerfile:20` and `docker/backup.Dockerfile:17` (`COPY backend/ backend/`); no `.dockerignore` exists anywhere in the repo
* **Exploit Scenario:** Docker `COPY` respects only `.dockerignore`, never `.gitignore`. A developer building locally (`docker build -f docker/api.Dockerfile .`) with a stray `backend/.env`, a downloaded Firebase service-account JSON, `__pycache__/`, `.pytest_cache/`, or local test artifacts under `backend/` would have all of it baked into the image layer — and image layers are effectively permanent (extractable even after a later layer "deletes" the file). If that image is ever pushed to a registry with broader read access than intended, those secrets leak.
* **Vulnerable Code Snippet:**
  ```dockerfile
  COPY backend/ backend/
  ```
* **Remediated Code Snippet:**
  ```
  # .dockerignore (repo root)
  **/__pycache__/
  **/*.pyc
  .git/
  .env
  .env.*
  *.json
  !backend/pyproject.toml
  .pytest_cache/
  .ruff_cache/
  htmlcov/
  .coverage*
  node_modules/
  frontend/
  code_review_report/
  ```
* **Remediation Guidance:** Add the `.dockerignore` above at the repo root (Docker resolves it relative to the build context). Then run `docker build ... && docker run --rm <image> find /app -name "*.json" -o -name ".env*"` to confirm nothing unexpected made it in. Treat this as blocking for any image that will be pushed to a shared/public registry.

### [High] - CWE-16 (Configuration): Manual deploy script bypasses the Terraform single-replica safety invariant — **Resolved**
_Fix: `infra/gcloud/deploy.sh` — added the same `MAX_INSTANCES`/`ALLOW_MULTI_REPLICA_RATE_LIMITS` guard as the Terraform `check`, defaulting `MAX_INSTANCES` to `1` and refusing to deploy above that without explicit acknowledgment. `--max-instances=10` replaced with `--max-instances="$MAX_INSTANCES"`. Verified with `bash -n` (syntax) — no live `gcloud` deploy was run (would require real GCP credentials/project)._
* **Location:** `infra/gcloud/deploy.sh:95` (`--max-instances=10`)
* **Exploit Scenario:** This isn't attacker-exploitable directly, but it's a **security-control bypass via an alternate deploy path**. The codebase's rate limiter, concurrency guard, and background analytics queue are documented in-code as correct only under `max_instance_count <= 1` (see `rate_limit.py`), and the Terraform `check` block now enforces that. `deploy.sh` — explicitly documented as "gcloud alternative to infra/terraform" — hardcodes `--max-instances=10` with no equivalent guard, so an operator who deploys via this script (rather than Terraform) silently re-opens the autoscaling correctness/DoS-protection gap the Terraform fix just closed, with no warning at deploy time.
* **Vulnerable Code Snippet:**
  ```bash
  gcloud run deploy "$SERVICE" --image="$IMAGE" --region="$REGION" \
    --service-account="$API_SA" "$INGRESS_FLAG" \
    --cpu=1 --memory=512Mi --concurrency=80 --timeout=55s --max-instances=10 \
    ...
  ```
* **Remediated Code Snippet:**
  ```bash
  MAX_INSTANCES="${MAX_INSTANCES:-1}"
  ALLOW_MULTI_REPLICA_RATE_LIMITS="${ALLOW_MULTI_REPLICA_RATE_LIMITS:-false}"
  if [[ "$MAX_INSTANCES" -gt 1 && "$ALLOW_MULTI_REPLICA_RATE_LIMITS" != "true" ]]; then
    echo "ERROR: MAX_INSTANCES>1 requires ALLOW_MULTI_REPLICA_RATE_LIMITS=true (the" \
         "rate limiter/concurrency guard/analytics queue are in-memory, per-replica)." >&2
    exit 1
  fi
  gcloud run deploy "$SERVICE" --image="$IMAGE" --region="$REGION" \
    --service-account="$API_SA" "$INGRESS_FLAG" \
    --cpu=1 --memory=512Mi --concurrency=80 --timeout=55s --max-instances="$MAX_INSTANCES" \
    ...
  ```
* **Remediation Guidance:** Mirror the Terraform `check` in the script (shown above), or — simpler and lower-maintenance — delete `deploy.sh`/`alerts.sh`/`scheduler.sh` from the deploy path entirely and make Terraform the single source of truth, keeping the gcloud scripts only as documented reference commands. Two independently-maintained deploy mechanisms for the same security invariant will drift again.

### [Medium] - CWE-1104 / Supply Chain: CI tests run against floating dependency ranges, not the pinned lock — **Resolved**
_Fix: `.github/workflows/ci.yml` — the lock install now runs first (as its own named step), then `pip install -e "backend[dev]"` only adds dev-only tooling on top. Verified locally: after this exact install order, all 58 packages listed in `requirements.lock` matched their installed versions exactly (`importlib.metadata` check), and the full test suite (221 tests) still passes._
* **Location:** `.github/workflows/ci.yml`, `backend` job (`pip install -e "backend[dev]"` at line 21, before the lock-install-only step)
* **Exploit Scenario:** The `backend` CI job installs from `backend/requirements.txt`'s `>=` ranges and runs the full test suite against *whatever resolves at that moment* — a newer (possibly vulnerable or behavior-changed) transitive dependency could slip in between CI runs. `requirements.lock` (the actual production pin, used by the Docker image) is only checked for *installability*, never exercised by the test suite. This means "tests green" does not guarantee "the exact thing being deployed was tested."
* **Vulnerable Code Snippet:**
  ```yaml
  - name: Install backend (base + dev)
    run: pip install -e "backend[dev]"
  - name: Validate production dependency lock installs
    run: pip install -r backend/requirements.lock
  - name: Run tests (coverage >= 90% enforced)
    run: python -m pytest
  ```
* **Remediated Code Snippet:**
  ```yaml
  - name: Install pinned production deps + dev tools
    run: |
      pip install -r backend/requirements.lock
      pip install -e "backend[dev]" --no-deps
  - name: Run tests against the pinned lock (coverage >= 90% enforced)
    run: python -m pytest
  ```
* **Remediation Guidance:** Install the pinned lock first, then the package itself with `--no-deps` so dev tools (pytest, ruff, bandit) layer on top without re-resolving runtime deps against the floating ranges. Re-run CI once to confirm nothing in `[dev]` extras needs a runtime package outside the lock. This makes "CI passed" and "this exact dependency set is what ships" the same claim.

### [Medium] - CWE-269: Improper Privilege Management — CI workflow lacks least-privilege token scope — **Resolved**
_Fix: `.github/workflows/ci.yml` — added `permissions: contents: read` at the workflow level; confirmed no job pushes, comments, or publishes. Verified: `yaml.safe_load` parses the file and reports `permissions: {'contents': 'read'}`._
* **Location:** `.github/workflows/ci.yml` (no top-level or job-level `permissions:` block)
* **Exploit Scenario:** Without an explicit `permissions:` block, the `GITHUB_TOKEN` used by every step in every job defaults to the repository/org setting, which for many orgs is still broad (`contents: write`, etc.) rather than the minimum this workflow needs (`contents: read` — it never pushes, comments, or creates releases). A compromised third-party Action (see next finding) running with an over-scoped token can do more damage than necessary — e.g. push to the repo or modify workflow files.
* **Vulnerable Code Snippet:**
  ```yaml
  name: CI

  on:
    push:
      branches: [main]
    pull_request:

  jobs:
    backend:
  ```
* **Remediated Code Snippet:**
  ```yaml
  name: CI

  on:
    push:
      branches: [main]
    pull_request:

  permissions:
    contents: read

  jobs:
    backend:
  ```
* **Remediation Guidance:** Add `permissions: contents: read` at the workflow level (verified sufficient — no job currently writes to the repo, comments on PRs, or publishes packages). If a future job needs to comment on PRs or upload artifacts, grant that permission at the *job* level only, not workflow-wide.

### [Medium] - CWE-829: Inclusion of Functionality from Untrusted Control Sphere — third-party Actions pinned by mutable tag — **Resolved**
_Fix: `.github/workflows/ci.yml` — every `uses:` (`actions/checkout`, `actions/setup-python`, `actions/setup-node`, `gitleaks/gitleaks-action`, `aquasecurity/tfsec-action`) now pins a full 40-char commit SHA (resolved live from each repo's tag ref via the GitHub API), with the human-readable tag kept as a trailing comment._
* **Location:** `.github/workflows/ci.yml` (`gitleaks/gitleaks-action@v2`, `aquasecurity/tfsec-action@v1.0.3` — added in the prior remediation pass)
* **Exploit Scenario:** A tag like `@v2` can be force-moved by the action's maintainer (or, in a supply-chain compromise, by an attacker who gains write access to that repo) to point at different, potentially malicious code without any change on this side. `@v1.0.3` is a more specific tag but is still mutable in Git (tags can be re-pushed unless the upstream repo protects them).
* **Remediated Code Snippet:**
  ```yaml
  - name: gitleaks (secret scan)
    uses: gitleaks/gitleaks-action@ff98106e4c7b2bc287b24eaf42907196329070c8 # v2.3.9
  - name: tfsec (Terraform static analysis)
    uses: aquasecurity/tfsec-action@6c9ea6921c26e1b1d5da3c9e3cca39ceff5ae74a # v1.0.3
  ```
* **Remediation Guidance:** Pin every third-party Action to a full commit SHA (with the human-readable tag as a trailing comment for maintainability), and consider Dependabot's `github-actions` ecosystem to keep those pins current automatically. Apply this to `actions/checkout`, `actions/setup-python`, and `actions/setup-node` too for full coverage, even though first-party GitHub actions are lower risk.

### [Medium] - CWE-1021: Missing Anti-Clickjacking / Security Headers on the SPA — **Resolved**
_Fix: `firebase.json` — added a `headers` block (`X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Strict-Transport-Security`, and a `Content-Security-Policy` with no `'unsafe-inline'` — verified no inline `<script>`/`<style>` or JS-set `.style`/`style=` usage exists anywhere in `frontend/js` or `index.html`, so a strict `script-src 'self' https://www.gstatic.com; style-src 'self'` is safe). `connect-src` covers Cloud Run's `*.run.app` domain plus local dev; `frontend/README.md` now documents narrowing it to the exact API origin per environment. Verified: `json.load` parses the file cleanly._
* **Location:** `firebase.json` (Hosting config has no `headers` block)
* **Exploit Scenario:** Firebase Hosting serves the SPA with no `X-Frame-Options`/`frame-ancestors`, `X-Content-Type-Options`, `Referrer-Policy`, or `Strict-Transport-Security` headers configured. An attacker could iframe the login or analyze page on a malicious site and attempt clickjacking (tricking a signed-in clinician into an unintended click), or a MIME-sniffing edge case could let a served asset be reinterpreted as executable content in a legacy browser.
* **Remediated Code Snippet:**
  ```json
  {
    "hosting": {
      "public": "frontend",
      "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
      "headers": [
        {
          "source": "**",
          "headers": [
            { "key": "X-Frame-Options", "value": "DENY" },
            { "key": "X-Content-Type-Options", "value": "nosniff" },
            { "key": "Referrer-Policy", "value": "strict-origin-when-cross-origin" },
            { "key": "Strict-Transport-Security", "value": "max-age=31536000; includeSubDomains" },
            {
              "key": "Content-Security-Policy",
              "value": "default-src 'self'; script-src 'self' https://www.gstatic.com; connect-src 'self' https://*.googleapis.com https://*.firebaseio.com <API_BASE>; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
            }
          ]
        }
      ],
      "rewrites": [{ "source": "**", "destination": "/index.html" }]
    }
  }
  ```
* **Remediation Guidance:** Add the `headers` block, substituting `<API_BASE>` with the actual Cloud Run origin(s) used per environment. Test with `curl -I` against the deployed Hosting URL to confirm headers are present, and validate the CSP doesn't block the dynamic `import()` calls to `www.gstatic.com` in `auth.js` (adjust `script-src`/`connect-src` if Firebase Auth's own network calls need broader allowances).

### [Low] - CWE-1104: Docker base image not pinned to a digest — **Resolved**
_Fix: both Dockerfiles now pin `FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285` (the manifest-list digest for the `3.13-slim` tag, resolved live via the Docker Hub registry API), with a comment on how to refresh it deliberately._
* **Location:** `docker/api.Dockerfile:10`, `docker/backup.Dockerfile:9` (`FROM python:3.13-slim`)
* **Exploit Scenario:** A mutable tag (`3.13-slim`) can point to a different image over time as upstream pushes patch updates — generally desirable for security patches, but it also means builds aren't reproducible and a compromised upstream base-image push (rare, but has happened in the ecosystem) would be pulled in silently on the next build.
* **Remediation Guidance:** Pin to a digest (`FROM python:3.13-slim@sha256:<digest>`) and update it deliberately via Dependabot/Renovate's Docker ecosystem support, which opens a PR when a new patched digest is available — giving you an auditable, reviewed update instead of silent drift in either direction.

### [Low] - CWE-88-adjacent: Unvalidated variable interpolation into `gcloud --set-env-vars` — **Resolved**
_Fix: `infra/gcloud/deploy.sh` — switched the `--set-env-vars` delimiter from the default `,` to `^:^...:` custom delimiter syntax, so a comma inside `FRONTEND_ORIGIN` (a legitimately comma-separated value per `CC_CORS_ORIGINS`) can no longer be misparsed as a new key-value pair._
* **Location:** `infra/gcloud/deploy.sh:96`
* **Exploit Scenario:** `CC_CORS_ORIGINS=$FRONTEND_ORIGIN` is interpolated directly into a comma-separated `--set-env-vars` list. `gcloud`'s env-var flag treats commas as pair separators; if `FRONTEND_ORIGIN` is ever set to a comma-separated multi-origin value (which the app's own `cors_origin_list` config supports and documents as valid), the `gcloud run deploy` command would misparse it — at best failing loudly, at worst (depending on gcloud's parsing edge cases) setting an unintended env var. This is operator-input, not remote-attacker-input, so severity is low, but it's a latent correctness/security footgun in a script whose entire job is setting security-relevant config (`CC_AUTH_ENABLED`, `CC_CORS_ORIGINS`).
* **Remediation Guidance:** Use `gcloud run deploy --update-env-vars` with `^:^` custom delimiter syntax (`--set-env-vars="^:^CC_CORS_ORIGINS=$FRONTEND_ORIGIN"`) or write env vars via a `--env-vars-file` YAML instead of inline comma-joining, so a comma in any value can't be misinterpreted as a new key-value pair.

### [Low / Accepted] - CWE-829: Firebase JS SDK loaded from CDN without Subresource Integrity
* **Location:** `frontend/js/auth.js` (dynamic `import()` from `https://www.gstatic.com/firebasejs/10.14.1/...`)
* **Status:** Previously identified and **deliberately not remediated** — see `staff_engineer_review_2026-09-03.md`. Dynamic `import()` has no SRI mechanism; fixing this requires vendoring the SDK, which would introduce a build step into a project that explicitly avoids one. Listed here for completeness of this security pass, not as a new action item.

---

## 3. DevSecOps & Security Hardening Roadmap

* **Immediate Guardrails (block exploits/drift today):**
  1. Non-root `USER` + `.dockerignore` in both Dockerfiles (High findings above) — ship before the next image build.
  2. Fix or retire `deploy.sh`'s `--max-instances` bypass of the Terraform single-replica invariant.
  3. Add `permissions: contents: read` to `ci.yml`.

* **SAST/DAST Tooling Integration (tailored to this stack):**
  * **Already present** (this and prior reviews): Bandit (Python SAST), `pip-audit` (Python SCA), ESLint/Prettier (JS lint/format), `npm audit` (JS SCA, now fail-closed), `gitleaks` (secret scanning), `tfsec` (Terraform SAST).
  * **Recommended additions:**
    - **Trivy** or **Grype** for container-image scanning of the built `api`/`backup` images (CVEs in the base image + installed packages) — run in CI right after `docker build`, before push.
    - **CodeQL** (GitHub-native, free for public/eligible private repos) for deeper Python/JS dataflow analysis than Bandit/ESLint alone provide — particularly useful for catching taint-flow issues across the router split.
    - **DAST**: a lightweight OWASP ZAP baseline scan against a deployed staging Cloud Run URL (auth disabled in a throwaway env) would validate the security headers, CORS behavior, and error-boundary scrubbing end-to-end rather than only at the unit-test level.
    - **Dependabot** (or Renovate) for `pip`, `npm`, `docker`, and `github-actions` ecosystems — closes the "SHA-pin the Actions" and "digest-pin the base image" items above with auditable, reviewed PRs instead of manual upkeep.

* **Long-Term Defense-in-Depth (architectural):**
  1. **Workload Identity Federation** instead of long-lived service-account keys anywhere a CI/CD pipeline eventually needs to deploy to GCP (there's no CD pipeline yet per the prior architecture review — when one is built, start it keyless).
  2. **Cloud Armor / GCLB in front of Cloud Run** for production, giving WAF rules, rate limiting at the network edge (complementing, not replacing, the app-level limiter), and DDoS protection — currently the service is Cloud-Run-direct.
  3. **Consolidate the two deploy paths** (Terraform vs. `infra/gcloud/*.sh`) into one authoritative mechanism — the security-relevant drift found in this review (High-3) is a structural risk that will keep recurring as long as both exist and only one is kept current.
  4. **Formalize a secret-rotation policy** for the Gemini API key and Firebase service-account credential now that both live in Secret Manager — rotation is technically easy (new version + IAM already governs access) but isn't documented as a runbook anywhere in `docs/`.
