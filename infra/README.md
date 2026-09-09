# Infrastructure as Config

Infrastructure for the Cyclotorsion Check backend. Everything is provisioned in
**`us-central1` by default**, because this runs on a **cloud trial account**
(which may restrict which regions can be used) and the trial processes **only
synthetic, non-patient data** — consistent with PRD Section 5.2's documented
demo-phase exception.

> **India residency note:** the PRD (§5.2) and US-5.2 require all persisted data
> to live in an India-based region (`asia-south1`/`asia-south2`) once **real
> patient data** is involved. The region is a single variable here
> (`var.region` / the `REGION` env var) — override it to an India region and
> re-provision before validation with real patient images. Nothing in the code
> or config hard-codes a region, so this is a parameter change, not a rewrite.

Two equivalent ways to deploy:

- [`terraform/`](terraform/) — the primary, declarative path (recommended).
- [`gcloud/`](gcloud/) — shell-script alternative for teams not using Terraform.

## What gets deployed (`us-central1` unless overridden)

| Component | Purpose |
|---|---|
| Cloud Run service (`cyclotorsion-check-api`) | The FastAPI backend |
| Cloud Run job (`cyclotorsion-backup`) | Runs `scripts/backup_export.py` nightly |
| Cloud Scheduler (`nightly-cyclotorsion-backup`) | Triggers the backup job 02:00 daily (RPO 24h) |
| BigQuery dataset + `results` table | De-identified aggregate results (PRD Epic 4) |
| GCS backup bucket | Nightly JSONL+GZIP exports, versioned, 30-day retention |
| Secret Manager | Firebase service-account JSON + Gemini API key (TDD 5.4) |
| Cloud Monitoring alert policies | TDD 6.4 conditions (source of truth) |

## Trial mode (synthetic data only)

This is a cloud trial account. Use it for the demo/portfolio phase — expect
trial-stage limits (free-tier quota caps are the very reason the rate limiter
and quota alert exist, TDD 4.3/6.4). **Do not process real patient images** on
the trial account: per PRD, region residency (R1) and the validation study
gates must be satisfied first.

## Bringing it to an India region (pre-validation gate)

Once a non-trial project with `asia-south1`/`asia-south2` access is available:

1. `terraform apply` (or the gcloud scripts) with `region=asia-south1` and the
   backup bucket recreated in that region.
2. Point the frontend at the new service, verify traffic.
3. Delete the legacy `us-central1` resources. `asia-south1` is then the single
   source of truth and US-5.2 (no backup outside India) is satisfied.

## Prerequisites

- `terraform >= 1.5`, `gcloud` CLI, and a logged-in Google account.
- A GCS bucket for remote Terraform state, e.g. `cyclotorsion-tfstate`.
- Container images built from the repo and pushed to Artifact Registry/Container
  Registry:

  ```bash
  # API service and backup job share docker/Dockerfile (multi-stage); select
  # each with --target.
  docker build -f docker/Dockerfile --target api    -t gcr.io/$PROJECT_ID/api:$TAG    .
  docker build -f docker/Dockerfile --target backup -t gcr.io/$PROJECT_ID/backup:$TAG .
  docker push gcr.io/$PROJECT_ID/api:$TAG gcr.io/$PROJECT_ID/backup:$TAG
  ```

  The API image listens on `0.0.0.0:8080` (Cloud Run contract); the backup image
  runs `scripts/backup_export.py` and reads `CC_PROJECT_ID` / `CC_BACKUP_TABLE` /
  `CC_BACKUP_BUCKET` from the job environment.

## Secrets

The Firebase service-account JSON and the Gemini API key must exist in Secret
Manager before apply (the `*_secret` variables reference them). The service
reads the Firebase SA as a **mounted file** (`/secrets/firebase/sa.json`) and
the Gemini key from an injected env var (`GEMINI_API_KEY`); the app's resolver
(`backend/cyclotorsion/secrets.py`) never requires secrets in plaintext config.

## Terraform

```bash
cd infra/terraform
terraform init -backend-config="bucket=cyclotorsion-tfstate"
terraform plan \
  -var project_id=cyclotorsion-check \
  -var image=gcr.io/cyclotorsion-check/api:1.0.0 \
  -var backup_image=gcr.io/cyclotorsion-check/backup:1.0.0 \
  -var frontend_origin=https://cyclotorsion-check.web.app \
  -var backup_bucket_name=cyclotorsion-backups \
  -var firebase_storage_secret=projects/cyclotorsion-check/secrets/firebase-service-account/versions/latest \
  -var gemini_api_key_secret=projects/cyclotorsion-check/secrets/gemini-api-key/versions/latest \
  -var notification_email=oncall@example.com
terraform apply
```

## gcloud alternative

```bash
cd infra/gcloud
export PROJECT_ID=cyclotorsion-check
bash deploy.sh            # secret -> SA roles -> Cloud Run service -> BigQuery -> bucket
bash scheduler.sh         # backup job + nightly schedule
bash alerts.sh            # log-based metrics + alert policies + notification channel
```

## Alerting (TDD 6.4)

Two policies are first-class in Terraform (`bigquery_write_failures`,
`gemini_errors`) plus an email notification channel. The latency
(`p95 > 15s`) and Gemini-quota (`> 80%`) alerts reference the app's custom
Prometheus metrics (`gemini_api_latency_seconds`, `gemini_retry_count`) and are
documented in `terraform/monitoring.tf` — wire them once managed Prometheus
collection is enabled.

## Known limits & deliberate deferrals

- **Caching is deliberately deferred.** Cloud Run autoscales and there is no
  read-heavy shared data yet at pilot scale, so a Redis/CDN caching layer would
  add operational cost without measurable benefit. Revisit when pilot traffic
  shows a pattern of repeated `/stats` reads or Gemini costs dominate.
- **Load testing.** `/detect` invokes Gemini (externally rate-limited); a
  meaningful load test needs a real Gemini key budget. The app already protects
  the provider via the rate limiter, the per-user concurrency guard
  (`CC_CONCURRENCY_LIMIT_ENABLED`), and the circuit breaker. Run a soak test
  before/after ramp-up once real credentials are allocated.
- **In-memory rate-limit + concurrency state** is shared per Cloud Run replica
  only (single-instance V1 design). If `min-instances` ever exceeds 1, move both
  to a shared backing store. Note this is also a **distributed-bypass** caveat:
  with `max-instances=10` a determined client can spread requests across
  replicas, multiplying the effective global budget ~10×. At pilot volume the
  per-user cap (2 concurrent `/detect`) plus server-side Gemini quota intent is
  the real protection; revisit with a shared store (Memorystore/Firestore) when
  `min-instances > 1`.

## Security notes

- **`CC_APP_ENV=production` is required in all prod deploys.** The auth layer
  fails CLOSED (401) on `/detect`, `/stats`, `/admin/*` whenever auth is disabled
  in a production environment — a config typo can no longer silently expose the
  service. The API is deployed with `--no-allow-unauthenticated` by default;
  public `allUsers` ingress is strictly opt-in (`PROD_INGRESS=true` /
  `var.public_ingress`) and recommended only behind an IAP-backed gateway.
- `run:invoker` is public only on the Cloud Run ingress; `/detect` and `/stats`
  are protected by the app's Firebase auth layer (`CC_AUTH_ENABLED=true`).
- **Admin tier is facility-scoped:** a `facility_admin` may only list/approve
  accounts at their own facility and is refused if their own account lacks a
  `facility_id` claim; only `program_officer` is cross-facility.
- **Raw exception text is never returned to callers.** Persistence failures
  surface as a generic `logging_warning` + a stable `logging_warning_code`; the
  full exception is logged server-side only.
- **Image decoding is bounded** by `MAX_IMAGE_PIXELS` (16 MP) to block
  decompression-bomb DoS; oversized images are rejected with 413.
- **Firebase ID tokens are verified with `check_revoked=True`** so de-provisioned
  sessions end promptly.
- `force_destroy = false` on the backup bucket: accidental data loss is blocked
  by Terraform.
- Secrets never appear in Cloud Run env config as plaintext values.
- **CMEK + India residency** are hardening toggles (`use_cmek`,
  `enforce_india_residency` / `ENFORCE_INDIA_REGION`). Enable both before
  onboarding any real patient data: data must live in `asia-south1/2` and be
  encrypted with a customer-managed KMS key.
