# --- Nightly BigQuery -> GCS backup (TDD Section 4.4) ---
# A Cloud Run *job* runs scripts/backup_export.py; Cloud Scheduler invokes it
# once a day (RPO 24h). Job failure surfaces in Cloud Monitoring for alerting.

resource "google_service_account" "backup" {
  project    = var.project_id
  account_id = "cyclotorsion-backup"
}

resource "google_project_iam_member" "backup_roles" {
  for_each = toset([
    "roles/bigquery.dataViewer",
    "roles/bigquery.jobUser",
    "roles/storage.objectAdmin",
    "roles/logging.logWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.backup.email}"
}

resource "google_cloud_run_v2_job" "backup" {
  name     = "cyclotorsion-backup"
  location = var.region
  project  = var.project_id

  template {
    template {
      service_account = google_service_account.backup.email
      timeout         = "300s"
      containers {
        image = var.backup_image
        env {
          name  = "CC_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "CC_BACKUP_TABLE"
          value = var.bigquery_table
        }
        env {
          name  = "CC_BACKUP_BUCKET"
          value = var.backup_bucket_name
        }
      }
    }
  }

  depends_on = [google_project_service.services]
}

resource "google_cloud_scheduler_job" "nightly_backup" {
  name        = "nightly-cyclotorsion-backup"
  region      = var.region
  project     = var.project_id
  schedule    = "0 2 * * *" # 02:00 daily, India local
  time_zone   = "Asia/Kolkata"
  description = "Nightly BigQuery results -> GCS backup (TDD 4.4, RPO 24h)"

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.backup.name}:run"
    oidc_token {
      service_account_email = google_service_account.backup.email
      audience              = "https://run.googleapis.com/"
    }
  }

  depends_on = [google_cloud_run_v2_job.backup]
}
