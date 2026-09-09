terraform {
  # >= 1.9 for cross-variable references inside a variable's own `validation`
  # block (used in variables.tf) — that feature isn't available in 1.5-1.8.
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0, < 7.0"
    }
  }

  # Remote state on GCS. Initialize per-project:
  #   terraform init -backend-config="bucket=cyclotorsion-tfstate"
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# --- Region (R1 / PRD Section 5.2) ---
# Everything is provisioned in var.region. The default is us-central1 because
# this runs on a cloud TRIAL account (which may restrict other regions) and only
# synthetic, non-patient data is ever processed — consistent with the PRD
# Section 5.2 demo-phase exception. Before any real patient data is used, set
# var.region to an India region (asia-south1/asia-south2) and relocate BigQuery
# + the backup bucket there to satisfy PRD US-5.2 / R1 residency. See
# infra/README.md.
resource "google_project_service" "services" {
  for_each = toset([
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
    "secretmanager.googleapis.com",
    "bigquery.googleapis.com",
    "monitoring.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
    "cloudtrace.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --- Security invariants ---
# These were originally 5 top-level `check` blocks here. Moved to `validation`
# blocks on the relevant variables in variables.tf: tfsec (this project's CI
# static analyzer) cannot parse the `check` block type at all — confirmed via
# `terraform validate` (accepts it) vs a direct local tfsec run (hard parse
# error) — and tfsec's upstream project is now sunset/frozen at its final
# release per its own CLI banner, so this is permanent, not a version to
# bump. Every one of these checks referenced only `var.*` values (no resource
# attributes), so a `validation` block is a like-for-like replacement, not a
# weaker substitute — same fail-the-plan behavior, just attached to the
# variable it's about instead of floating at the top level.
