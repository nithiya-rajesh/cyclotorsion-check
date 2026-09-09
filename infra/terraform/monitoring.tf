# --- Alerting (TDD Section 6.4) ---
# Policies target the app's own structured JSON logs (Cloud Logging -> log-based
# metrics are guaranteed to exist once the service runs) plus Cloud Run / Cloud
# Scheduler standard metrics. Custom per-request metrics exposed at /metrics are
# also scrapeable via GMP if managed Prometheus collection is enabled; the
# latency/quota alerts below reference those as documented MQL placeholders.

# Notification channel (email).
resource "google_monitoring_notification_channel" "email" {
  display_name = "Cyclotorsion on-call"
  type         = "email"
  labels = {
    email_address = var.notification_email
  }
}

# --- Log-based metrics ---

# BigQuery analytics write failures (`detect.log_write_failed` events).
resource "google_logging_metric" "bigquery_write_failures" {
  name        = "cyclotorsion/bigquery_write_failures"
  description = "Count of non-blocking BigQuery analytics write failures."
  filter      = "resource.type=\"cloud_run_revision\" AND jsonPayload.event=\"detect.log_write_failed\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

# Gemini fallback/error events (breaker fallback message surfaced to users).
resource "google_logging_metric" "gemini_errors" {
  name        = "cyclotorsion/gemini_errors"
  description = "Sustained AI-provider unavailability events."
  filter      = "resource.type=\"cloud_run_revision\" AND (jsonPayload.message=\"AI provider temporarily unavailable\" OR jsonPayload.event=\"detect.log_write_failed\")"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

# --- Alert policies ---

# TDD 6.4: bigquery_write_failure_rate sustained > 0 for 30+ minutes.
resource "google_monitoring_alert_policy" "bigquery_write_failures" {
  display_name = "BigQuery write failures sustained"
  combiner     = "OR"

  conditions {
    display_name = "BQ write failure rate > 0 over 30m"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.bigquery_write_failures.name}\" AND resource.type=\"cloud_run_revision\""
      duration        = "1800s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
  alert_strategy {
    auto_close = "10800s"
  }
}

# TDD 6.4: Gemini quota / unavailability — alert on any recent error burst.
resource "google_monitoring_alert_policy" "gemini_errors" {
  display_name = "Gemini provider errors"
  combiner     = "OR"

  conditions {
    display_name = "Gemini error events > 0 over 5m"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.gemini_errors.name}\" AND resource.type=\"cloud_run_revision\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
  alert_strategy {
    auto_close = "3600s"
  }
}

# Latency alert (TDD 6.4: p95 latency > 15s) and Gemini-quota alert (>80%) are
# left as documented MQL policies here. They reference the custom Prometheus
# metrics exported by /metrics (gemini_api_latency_seconds,
# gemini_retry_count) once managed Prometheus collection (GMP worker) is
# enabled on the cluster/service; uncomment and adjust the metric.type to your
# GMP export when that is deployed:
#
#   gcloud monitoring policies create ... \
#     --monitoring-filter='metric.type="prometheus.googleapis.com/gemini_api_latency_seconds/histogram" ...'
