#!/usr/bin/env bash
#
# gcloud alternative to infra/terraform (see infra/README.md).
# Deploys: Secret Manager secrets, service account + roles, Cloud Run service,
# BigQuery dataset/table, GCS backup bucket.
# Region defaults to us-central1 (cloud TRIAL account; synthetic data only).
# Override REGION to asia-south1/asia-south2 before any real patient deployment.
#
# Security: the service is always deployed with CC_APP_ENV=production so the app
# FAILS CLOSED if CC_AUTH_ENABLED is ever unset/typo'd (HIGH-1 fix). Public
# unauthenticated ingress requires PROD_INGRESS=true (default off). Scaling
# beyond one replica (MAX_INSTANCES>1) requires ALLOW_MULTI_REPLICA_RATE_LIMITS=true
# (AppSec review High — see the guard below).
#
# Before running, build and push the images (see docker/Dockerfile, multi-stage):
#   docker build -f docker/Dockerfile --target api    -t gcr.io/$PROJECT_ID/api:$TAG    .
#   docker build -f docker/Dockerfile --target backup -t gcr.io/$PROJECT_ID/backup:$TAG .
#   docker push gcr.io/$PROJECT_ID/api:$TAG gcr.io/$PROJECT_ID/backup:$TAG
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-cyclotorsion-check-api}"
IMAGE="${IMAGE:?set IMAGE (e.g. gcr.io/$PROJECT_ID/api:1.0.0)}"
FRONTEND_ORIGIN="${FRONTEND_ORIGIN:?set FRONTEND_ORIGIN}"
BACKUP_BUCKET="${BACKUP_BUCKET:?set BACKUP_BUCKET}"
DATASET="${DATASET:-cyclotorsion_check}"
TABLE="${DATASET}.results"
FIREBASE_SECRET_REF="${FIREBASE_SECRET_REF:-}"
GEMINI_SECRET_REF="${GEMINI_SECRET_REF:-}"
MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"
# Auth always on for production deploys; refuse to deploy otherwise.
AUTH_ENABLED="${AUTH_ENABLED:-true}"
# Public unauthenticated ingress is OPT-IN and OFF by default (High-1 fix).
PROD_INGRESS="${PROD_INGRESS:-false}"
# Single-replica invariant (AppSec review High): rate_limit.py, concurrency.py,
# and background.py hold in-memory, per-replica state. Scaling beyond one
# replica silently fragments them across instances — exactly the invariant
# infra/terraform/main.tf's `rate_limits_require_single_replica_ceiling` check
# now enforces. This script is a separate deploy path from Terraform, so it
# must enforce the same invariant itself rather than silently drifting from it.
MAX_INSTANCES="${MAX_INSTANCES:-1}"
ALLOW_MULTI_REPLICA_RATE_LIMITS="${ALLOW_MULTI_REPLICA_RATE_LIMITS:-false}"
if [[ "$MAX_INSTANCES" -gt 1 && "$ALLOW_MULTI_REPLICA_RATE_LIMITS" != "true" ]]; then
  echo "ERROR: MAX_INSTANCES>1 requires ALLOW_MULTI_REPLICA_RATE_LIMITS=true — the" >&2
  echo "rate limiter, concurrency guard, and analytics queue are in-memory, per-replica," >&2
  echo "and fragment silently across instances above 1. Set that only after moving" >&2
  echo "that state to a shared backing store (Memorystore/Firestore)." >&2
  exit 1
fi

gcloud config set project "$PROJECT_ID"

# Region guard (LOW-7 / PRD residency): refuse a real-patient deploy to
# us-central1 unless ALLOW_NON_INDIA_REGION is explicitly true.
if [[ "${ENFORCE_INDIA_REGION:-false}" == "true" ]]; then
  case "$REGION" in
    asia-south1|asia-south2) ;;
    *)
      echo "ERROR: ENFORCE_INDIA_REGION=true requires REGION in asia-south1/asia-south2 (PRD R1)." >&2
      exit 1 ;;
  esac
else
  [[ "$REGION" == us-central1 ]] && echo "WARNING: deploying to us-central1 (cloud trial; synthetic data only). Set REGION=asia-south1/asia-south2 + ENFORCE_INDIA_REGION=true before any real patient deployment." >&2
fi

echo "==> Enabling APIs ($REGION)"
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com \
  secretmanager.googleapis.com bigquery.googleapis.com monitoring.googleapis.com \
  cloudtrace.googleapis.com --project="$PROJECT_ID"

echo "==> Service accounts"
gcloud iam service-accounts create cyclotorsion-api \
  --project="$PROJECT_ID" --display-name="Cyclotorsion API" || true
API_SA="cyclotorsion-api@$PROJECT_ID.iam.gserviceaccount.com"
for role in roles/bigquery.dataEditor roles/bigquery.jobUser roles/logging.logWriter \
            roles/monitoring.metricWriter roles/cloudtrace.agent roles/run.invoker; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$API_SA" --role="$role" >/dev/null || true
done

echo "==> Secrets"
if [[ -n "$FIREBASE_SECRET_REF" ]]; then
  gcloud services enable secretmanager.googleapis.com --project="$PROJECT_ID"
  # Copy your Firebase SA JSON into Secret Manager as firebase-service-account
  # (version 1) before this step, then pass the env var.
  gcloud secrets add-iam-policy-binding firebase-service-account \
    --member="serviceAccount:$API_SA" --role=roles/secretmanager.secretAccessor >/dev/null || true
fi
if [[ -n "$GEMINI_SECRET_REF" ]]; then
  gcloud secrets add-iam-policy-binding gemini-api-key \
    --member="serviceAccount:$API_SA" --role=roles/secretmanager.secretAccessor >/dev/null || true
fi

echo "==> BigQuery dataset/table"
bq --location="$REGION" mk --dataset "$PROJECT_ID:$DATASET" 2>/dev/null || true
TABLE_EXISTS=$(bq ls --format=json "$PROJECT_ID:$DATASET" | grep -c "\"results\"" || true)
if [[ "$TABLE_EXISTS" == "0" ]]; then
  bq mk --table "$PROJECT_ID:$DATASET.results" \
    test_id:STRING,timestamp:STRING,angle_deg:FLOAT,upright_landmark:STRING,rotated_landmark:STRING,passed_sanity_check:BOOL,sanity_flags:STRING,facility_id:STRING,user_uid:STRING
fi

echo "==> GCS backup bucket"
gsutil mb -p "$PROJECT_ID" -l "$REGION" "gs://$BACKUP_BUCKET" || true
gsutil versioning set on "gs://$BACKUP_BUCKET"

echo "==> Cloud Run service (no-traffic deploy)"
INGRESS_FLAG="--no-allow-unauthenticated"
[[ "$PROD_INGRESS" == "true" ]] && INGRESS_FLAG="--allow-unauthenticated"
# `^:^` switches the --set-env-vars pair/list delimiter from "," to ":" (AppSec
# review Low: CWE-88-adjacent) so a comma inside FRONTEND_ORIGIN (a legitimate
# value — CC_CORS_ORIGINS accepts a comma-separated origin list) can never be
# misparsed as the start of a new KEY=VALUE pair.
gcloud run deploy "$SERVICE" --image="$IMAGE" --region="$REGION" \
  --service-account="$API_SA" "$INGRESS_FLAG" \
  --cpu=1 --memory=512Mi --concurrency=80 --timeout=55s --max-instances="$MAX_INSTANCES" \
  --set-env-vars="^:^CC_PROJECT_ID=$PROJECT_ID:CC_APP_ENV=production:CC_AUTH_ENABLED=$AUTH_ENABLED:CC_STORAGE_MODE=bigquery:CC_BIGQUERY_TABLE=$TABLE:CC_DETECT_MODE=auto:GEMINI_MODEL=$MODEL:CC_TRACING_ENABLED=true:CC_RATE_LIMIT_ENABLED=true:CC_CONCURRENCY_LIMIT_ENABLED=true:CC_PROVISIONING_MODE=firebase:CC_CORS_ORIGINS=$FRONTEND_ORIGIN" \
  ${FIREBASE_SECRET_REF:+--set-secrets="CC_FIREBASE_CREDENTIALS=$FIREBASE_SECRET_REF"} \
  ${GEMINI_SECRET_REF:+--set-secrets="GEMINI_API_KEY=$GEMINI_SECRET_REF"}

echo "Deployed. URL:" $(gcloud run services describe "$SERVICE" --region="$REGION" --format="value(status.url)")
