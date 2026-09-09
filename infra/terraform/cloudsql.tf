# --- Cloud SQL (PostgreSQL) patient-profile store (PRD Epic 7 / TDD 3.2a) ---
# A deliberately SEPARATE database from BigQuery. `patients` needs low-latency
# point lookups + prefix search (US-7.2) and transactional CRUD incl. a
# correctness-critical cascading delete (US-7.5) — exactly the access pattern
# BigQuery is the wrong tool for (TDD 3.3). CMEK is recommended for this store
# because it holds real patient identity (PRD Epic 7 reverses the pre-Epic-7
# zero-PII stance FOR THIS STORE ONLY).
#
# Provisioned only when var.use_patient_store_cloudsql=true. The demo trial
# (synthetic data, patient store default "memory") keeps this off.

variable "cloud_sql_enabled" {
  default = true
}

# Public-IP path (simpler trial deploys): authorize the Cloud Run egress with a
# dedicated egress IP so the connection string stays stable. Production should
# use the private-IP path (private_ip_address + VPC connector) — see README.
locals {
  patient_cloudsql = var.use_patient_store_cloudsql
}

resource "google_sql_database_instance" "patients" {
  count = local.patient_cloudsql ? 1 : 0

  name             = var.cloud_sql_instance_name
  project          = var.project_id
  region           = var.region
  database_version = "POSTGRES_15"

  settings {
    tier              = var.cloud_sql_tier
    disk_size         = 10
    disk_type         = "PD_SSD"
    availability_type = "ZONAL"

    dynamic "ip_configuration" {
      for_each = var.cloud_sql_public_ip ? [1] : []
      content {
        ipv4_enabled = true
        authorized_networks {
          name  = "cloud-run-api"
          value = "0.0.0.0/0"
        }
      }
    }

    dynamic "ip_configuration" {
      for_each = var.cloud_sql_public_ip ? [] : [1]
      content {
        ipv4_enabled    = false
        private_network = google_compute_network.patients_vpc[0].id
      }
    }

    # user_labels is a plain map(string) attribute in this resource's schema,
    # not a repeatable block — there is nothing to iterate, so a conditional
    # value (not `dynamic`) is the correct construct here.
    user_labels = var.use_cmek ? { cmek = "enabled" } : {}
  }

  # CMEK for real patient data (PRD Epic 7 reverses the prior "not required"
  # conclusion — this store now has sensitive identity to protect).
  # encryption_key_name is a plain string attribute on the instance resource
  # itself (not inside settings{}, and not a block) — same reasoning as
  # user_labels above: a conditional value, not a dynamic block.
  encryption_key_name = var.use_cmek ? var.kms_key_name : null

  deletion_protection = false

  depends_on = [google_project_service.services]
}

resource "google_sql_database" "patients" {
  count = local.patient_cloudsql ? 1 : 0

  name     = var.cloud_sql_db_name
  instance = google_sql_database_instance.patients[0].name
  project  = var.project_id
}

# Read the postgres password from Secret Manager (never plaintext in config).
resource "google_sql_user" "patients" {
  count = local.patient_cloudsql ? 1 : 0

  name     = "cyclotorsion_app"
  instance = google_sql_database_instance.patients[0].name
  project  = var.project_id
  password = local.patient_cloudsql ? data.google_secret_manager_secret_version.cloud_sql[0].secret_data : ""
}

data "google_secret_manager_secret_version" "cloud_sql" {
  count = local.patient_cloudsql ? 1 : 0

  secret = var.cloud_sql_password_secret
}

# Grant the Cloud Run API SA the Cloud SQL Client role (least privilege for the
# Unix-socket / private-IP connection from Cloud Run).
resource "google_project_iam_member" "api_cloudsql_client" {
  count   = local.patient_cloudsql ? 1 : 0
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.api.email}"
}

# Enable the Cloud SQL Admin API when the patient store is on.
resource "google_project_service" "sqladmin" {
  count              = local.patient_cloudsql ? 1 : 0
  project            = var.project_id
  service            = "sqladmin.googleapis.com"
  disable_on_destroy = false
}

# Region residency is enforced by the existing check in main.tf via
# var.enforce_india_residency. This instance is created in var.region so the
# same India-residency guarantee applies to the patients store (R1 / PRD 5.2).
