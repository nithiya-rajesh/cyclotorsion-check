# --- Cloud Run API service ---
# /health + /metrics remain open for uptime/scraping; /detect and /stats are
# protected by the app layer (Firebase) once CC_AUTH_ENABLED=true. The service
# is deployed with CC_APP_ENV=production so the app fails closed if auth is ever
# accidentally disabled (HIGH-1 fix). Public invocation is strictly opt-in via
# var.public_ingress (default false).

resource "google_service_account" "api" {
  project    = var.project_id
  account_id = "cyclotorsion-api"
}

resource "google_project_iam_member" "api_roles" {
  for_each = toset([
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/run.invoker",
    "roles/aiplatform.user", # only needed if GEMINI_VERTEX_LOCATION is used
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_cloud_run_v2_service" "api" {
  name     = var.service_name
  location = var.region
  project  = var.project_id

  template {
    service_account = google_service_account.api.email
    timeout         = "55s"
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }
    containers {
      image = var.image
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
      env {
        name  = "CC_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "CC_APP_ENV"
        value = "production"
      }
      env {
        name  = "CC_AUTH_ENABLED"
        value = "true"
      }
      env {
        name  = "CC_STORAGE_MODE"
        value = "bigquery"
      }
      env {
        name  = "CC_BIGQUERY_TABLE"
        value = var.bigquery_table
      }
      env {
        name  = "CC_DETECT_MODE"
        value = "auto"
      }
      env {
        name  = "GEMINI_MODEL"
        value = var.gemini_model
      }
      env {
        name  = "CC_TRACING_ENABLED"
        value = "true"
      }
      env {
        name  = "CC_RATE_LIMIT_ENABLED"
        value = "true"
      }
      env {
        name  = "CC_CONCURRENCY_LIMIT_ENABLED"
        value = "true"
      }
      env {
        name  = "CC_PROVISIONING_MODE"
        value = "firebase"
      }
      env {
        name  = "CC_CORS_ORIGINS"
        value = var.frontend_origin
      }
      dynamic "env" {
        for_each = var.firebase_storage_secret != "" ? [1] : []
        content {
          # The app reads a FILE PATH from CC_FIREBASE_CREDENTIALS; mount the
          # secret as a volume at that path (TDD 5.4: file mount, not plaintext).
          name  = "CC_FIREBASE_CREDENTIALS"
          value = "/secrets/firebase/sa.json"
        }
      }
      dynamic "volume_mounts" {
        for_each = var.firebase_storage_secret != "" ? [1] : []
        content {
          name       = "firebase-secret"
          mount_path = "/secrets/firebase"
        }
      }
      dynamic "env" {
        for_each = var.gemini_api_key_secret != "" ? [1] : []
        content {
          # Mounted as GEMINI_API_KEY so it never sits in plaintext config.
          # backend/cyclotorsion/secrets.py reads it from env at runtime.
          name = "GEMINI_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.gemini[0].secret_id
              version = "latest"
            }
          }
        }
      }
    }
    dynamic "volumes" {
      for_each = var.firebase_storage_secret != "" ? [1] : []
      content {
        name = "firebase-secret"
        secret {
          secret       = google_secret_manager_secret.firebase[0].secret_id
          default_mode = "0400"
          items {
            path = "sa.json"
            mode = "0400"
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [google_project_service.services]
}

# Public invocation is OPT-IN (default false). Prefer IAP / GCLB-backed ingress
# in production; the app-layer auth is defense-in-depth, not the only gate.
resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.public_ingress ? 1 : 0
  project  = var.project_id
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
