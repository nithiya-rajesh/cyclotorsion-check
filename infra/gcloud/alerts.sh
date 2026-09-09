#!/usr/bin/env bash
#
# gcloud alternative to infra/terraform monitoring.tf.
# Creates log-based metrics + alert policies for TDD Section 6.4 conditions and
# an email notification channel.
#
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
: "${NOTIFICATION_EMAIL:?set NOTIFICATION_EMAIL (on-call email)}"
REGION="${REGION:-us-central1}"

gcloud config set project "$PROJECT_ID"

echo "==> Notification channel"
curl -sS -X POST \
  "https://monitoring.googleapis.com/v3/projects/$PROJECT_ID/notificationChannels" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -d "{
    \"type\": \"email\",
    \"displayName\": \"Cyclotorsion on-call\",
    \"labels\": { \"email_address\": \"$NOTIFICATION_EMAIL\" }
  }" >/dev/null || true

echo "==> Log-based metric: bigquery_write_failures"
gcloud logging metrics create cyclotorsion/bigquery_write_failures \
  --description="BigQuery analytics write failures" \
  --log-filter='resource.type="cloud_run_revision" AND jsonPayload.event="detect.log_write_failed"' \
  || true

echo "==> Log-based metric: gemini_errors"
gcloud logging metrics create cyclotorsion/gemini_errors \
  --description="Gemini provider unavailability events" \
  --log-filter='resource.type="cloud_run_revision" AND (jsonPayload.message="AI provider temporarily unavailable" OR jsonPayload.event="detect.log_write_failed")' \
  || true

echo "==> Alert policy: BigQuery write failures sustained > 0 over 30m"
gcloud alpha monitoring policies create \
  --display-name="BigQuery write failures sustained" \
  --notification-channels="$(gcloud monitoring channels list --filter='displayName="Cyclotorsion on-call"' --format='value(name)' | head -n1)" \
  --conditions-from-filter="metric.type=logging.googleapis.com/user/cyclotorsion/bigquery_write_failures AND resource.type=cloud_run_revision, duration=1800s, comparison=GT, threshold=0, aggregations=ALIGN_RATE" \
  || echo "create policy manually: see infra/terraform/monitoring.tf for the canonical form"

echo "==> Alert policy: Gemini errors burst"
gcloud alpha monitoring policies create \
  --display-name="Gemini provider errors" \
  --notification-channels="$(gcloud monitoring channels list --filter='displayName="Cyclotorsion on-call"' --format='value(name)' | head -n1)" \
  --conditions-from-filter="metric.type=logging.googleapis.com/user/cyclotorsion/gemini_errors AND resource.type=cloud_run_revision, duration=300s, comparison=GT, threshold=0, aggregations=ALIGN_RATE" \
  || echo "create policy manually: see infra/terraform/monitoring.tf"
