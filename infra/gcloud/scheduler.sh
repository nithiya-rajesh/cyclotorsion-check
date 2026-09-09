#!/usr/bin/env bash
#
# gcloud alternative to infra/terraform scheduler.
# Creates the Cloud Run backup job + nightly Cloud Scheduler that runs
# scripts/backup_export.py (RPO 24h), plus the monitoring alert on failure.
# Region defaults to us-central1 (cloud TRIAL account); override for India.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"
BACKUP_IMAGE="${BACKUP_IMAGE:?set BACKUP_IMAGE (image that runs backup_export.py)}"
BACKUP_TABLE="${BACKUP_TABLE:?set BACKUP_TABLE (dataset.table)}"
BACKUP_BUCKET="${BACKUP_BUCKET:?set BACKUP_BUCKET}"
JOB="cyclotorsion-backup"
SCHEDULER="nightly-cyclotorsion-backup"

gcloud config set project "$PROJECT_ID"

echo "==> Backup service account + roles"
gcloud iam service-accounts create cyclotorsion-backup \
  --project="$PROJECT_ID" --display-name="Cyclotorsion backup" || true
BACKUP_SA="cyclotorsion-backup@$PROJECT_ID.iam.gserviceaccount.com"
for role in roles/bigquery.dataViewer roles/bigquery.jobUser \
            roles/storage.objectAdmin roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$BACKUP_SA" --role="$role" >/dev/null || true
done

echo "==> Cloud Run job"
gcloud run jobs create "$JOB" --image="$BACKUP_IMAGE" --region="$REGION" \
  --service-account="$BACKUP_SA" --tasks=1 --max-retries=2 --task-timeout=300s \
  --set-env-vars="CC_PROJECT_ID=$PROJECT_ID,CC_BACKUP_TABLE=$BACKUP_TABLE,CC_BACKUP_BUCKET=$BACKUP_BUCKET" \
  || gcloud run jobs update "$JOB" --image="$BACKUP_IMAGE" --region="$REGION" \
     --service-account="$BACKUP_SA" --tasks=1 --max-retries=2 --task-timeout=300s \
     --set-env-vars="CC_PROJECT_ID=$PROJECT_ID,CC_BACKUP_TABLE=$BACKUP_TABLE,CC_BACKUP_BUCKET=$BACKUP_BUCKET"

echo "==> Cloud Scheduler (02:00 Asia/Kolkata daily)"
gcloud scheduler jobs create http "$SCHEDULER" --schedule="0 2 * * *" \
  --time-zone="Asia/Kolkata" --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/$JOB:run" \
  --http-method=POST --oidc-service-account-email="$BACKUP_SA" --oidc-token-audience="https://run.googleapis.com/" \
  --location="$REGION" --description="Nightly BigQuery->GCS backup" \
  || gcloud scheduler jobs update http "$SCHEDULER" --schedule="0 2 * * *" \
     --time-zone="Asia/Kolkata" --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/$JOB:run" \
     --http-method=POST --oidc-service-account-email="$BACKUP_SA" --oidc-token-audience="https://run.googleapis.com/" \
     --location="$REGION"
