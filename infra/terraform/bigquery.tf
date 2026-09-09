# --- BigQuery results table (PRD Epic 4) ---
# Mirrors TDD Section 3.2 schema. Region = var.region (us-central1 for the trial;
# override to an India region for any real-patient deployment per PRD US-5.2).

resource "google_bigquery_dataset" "results" {
  project     = var.project_id
  dataset_id  = var.bigquery_dataset
  location    = var.region
  description = "De-identified aggregate cyclotorsion results (zero patient fields)."
  # No default_table_expiration_ms: 0 isn't a valid value (GCP requires >= 1
  # hour in ms if set at all) and the intent here is "never auto-expire", so
  # the argument is simply omitted rather than set to an invalid "0".
  dynamic "default_encryption_configuration" {
    for_each = var.use_cmek ? [1] : []
    content {
      kms_key_name = var.kms_key_name
    }
  }
}

resource "google_bigquery_table" "results" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.results.dataset_id
  table_id   = "results"

  schema = <<-EOT
  [
    {"name":"test_id","type":"STRING","mode":"REQUIRED"},
    {"name":"timestamp","type":"STRING","mode":"REQUIRED"},
    {"name":"angle_deg","type":"FLOAT","mode":"REQUIRED"},
    {"name":"upright_landmark","type":"STRING","mode":"REQUIRED"},
    {"name":"rotated_landmark","type":"STRING","mode":"REQUIRED"},
    {"name":"passed_sanity_check","type":"BOOL","mode":"REQUIRED"},
    {"name":"sanity_flags","type":"STRING","mode":"REQUIRED"},
    {"name":"facility_id","type":"STRING","mode":"NULLABLE"},
    {"name":"user_uid","type":"STRING","mode":"NULLABLE"}
  ]
  EOT

  clustering = ["facility_id"]
  depends_on = [google_project_service.services]
}

# --- Cloud Storage backup bucket (TDD Section 4.4 / PRD residency) ---
# Nightly tables exported here. Trial runs in us-central1; relocate to an India
# region (with the BQ dataset) before any real patient data per PRD US-5.2.
# Lifecycle prunes old snapshots beyond retention.
# CMEK is applied here whenever var.use_cmek=true (the dynamic "encryption"
# block below); this bucket is Google-managed-key by default only in the
# synthetic-data trial phase (PRD Section 5.5 / TDD 5.3), where CMEK is
# recommended, not required. tfsec can't evaluate that conditional statically
# and flags it as google-storage-bucket-encryption-customer-key — excluded
# via --exclude in .github/workflows/ci.yml's tfsec step (an inline
# #tfsec:ignore: comment here did not suppress it against the pinned tfsec
# version; the CLI flag does, confirmed locally).
resource "google_storage_bucket" "backups" {
  project                     = var.project_id
  name                        = var.backup_bucket_name
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true
  versioning {
    enabled = true
  }
  dynamic "encryption" {
    for_each = var.use_cmek ? [1] : []
    content {
      default_kms_key_name = var.kms_key_name
    }
  }
  lifecycle_rule {
    condition {
      age = var.backup_retention_days
    }
    action {
      type = "Delete"
    }
  }
}

# Least-privilege: explicitly grant ONLY the backup service account object access
# (it already has objectAdmin via backup.tf). No allUsers/allAuthenticatedUsers
# reader exists; this binding documents that the bucket is private-by-default and
# prevents accidental public exposure. (LOW-7)
resource "google_storage_bucket_iam_member" "backups_backup_sa_admin" {
  bucket = google_storage_bucket.backups.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.backup.email}"
}
