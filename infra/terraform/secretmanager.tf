# --- Secret Manager (TDD Section 5.4) ---
# Stores the Firebase service-account JSON and the Gemini HTTP API key instead
# of keeping them as plaintext env vars. Reads are IAM-governed (audit trail).

resource "google_secret_manager_secret" "firebase" {
  count     = var.firebase_storage_secret != "" ? 1 : 0
  project   = var.project_id
  secret_id = "firebase-service-account"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "gemini" {
  count     = var.gemini_api_key_secret != "" ? 1 : 0
  project   = var.project_id
  secret_id = "gemini-api-key"
  replication {
    auto {}
  }
}

# Grants the API's service account read access to both secrets so it can
# resolve them at runtime / via the mounted env var.
resource "google_secret_manager_secret_iam_member" "firebase_accessor" {
  count     = var.firebase_storage_secret != "" ? 1 : 0
  project   = var.project_id
  secret_id = google_secret_manager_secret.firebase[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  # google_service_account.api.email directly, rather than reading it back
  # through google_cloud_run_v2_service.api.template[0].service_account
  # (template is a list-of-objects block — .template.service_account without
  # the [0] index is invalid — and this is the same value regardless: the
  # Cloud Run resource itself sets template.service_account to this email).
  member = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "gemini_accessor" {
  count     = var.gemini_api_key_secret != "" ? 1 : 0
  project   = var.project_id
  secret_id = google_secret_manager_secret.gemini[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  # google_service_account.api.email directly, rather than reading it back
  # through google_cloud_run_v2_service.api.template[0].service_account
  # (template is a list-of-objects block — .template.service_account without
  # the [0] index is invalid — and this is the same value regardless: the
  # Cloud Run resource itself sets template.service_account to this email).
  member = "serviceAccount:${google_service_account.api.email}"
}
