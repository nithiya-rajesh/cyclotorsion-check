variable "project_id" {
  description = "Google Cloud project id, e.g. cyclotorsion-check"
  type        = string
}

variable "region" {
  description = "Deployment region. Defaults to us-central1 for the cloud TRIAL account (synthetic data only). Override to an India region (e.g. asia-south1/asia-south2) for any real-patient deployment to satisfy PRD US-5.2 / R1 residency."
  type        = string
  default     = "us-central1"

  # Residency (HIGH/LOW-7): fail the plan if real-patient mode is on but the
  # region is not an India region (PRD US-5.2 / R1). Was a top-level `check`
  # block in main.tf; moved here because tfsec's parser (as pinned in this
  # project's CI, and — per its own startup banner — now a frozen, sunset
  # project with no further releases) does not support the `check` block
  # type at all (confirmed: `terraform validate` accepts it fine, but tfsec
  # hard-fails to even parse the file). A `validation` block covers the same
  # variables-only constraint and is supported far more widely.
  validation {
    condition     = !var.enforce_india_residency || contains(["asia-south1", "asia-south2"], var.region)
    error_message = "Real patient data requires an India region (asia-south1/asia-south2). Set region before enabling enforce_india_residency."
  }
}

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
  default     = "cyclotorsion-check-api"
}

variable "image" {
  description = "Container image (uss-docker.pkg.dev/.../api:tag)."
  type        = string
}

variable "backup_image" {
  description = "Container image that runs scripts/backup_export.py."
  type        = string
}

variable "frontend_origin" {
  description = "Firebase Hosting origin; single, exact origin for CORS."
  type        = string
}

variable "public_ingress" {
  description = "When true, binds roles/run.invoker to allUsers. Default false: production should route through a gateway/IAP (IAP-backed GCLB) instead of public ingress. The app-layer auth (CC_AUTH_ENABLED) is defense-in-depth only."
  type        = bool
  default     = false

  # Public ingress (HIGH-1): when patient data is on, forbid allUsers invoker.
  # Was a `check` block in main.tf — see the note on variable "region" above
  # for why it moved (tfsec doesn't parse `check` blocks at all).
  validation {
    condition     = !var.enforce_india_residency || !var.public_ingress
    error_message = "Patient-data deployments must not use public allUsers ingress; route through an IAP-backed gateway."
  }
}

variable "firebase_storage_secret" {
  description = "Secret Manager version holding the Firebase service-account JSON (projects/P/secrets/x/versions/latest)."
  type        = string
  default     = ""
}

variable "gemini_api_key_secret" {
  description = "Secret Manager version holding the Gemini HTTP API key (projects/P/secrets/x/versions/latest)."
  type        = string
  default     = ""
}

variable "gemini_model" {
  description = "Gemini model name (HTTP API)."
  type        = string
  default     = "gemini-2.5-flash"
}

variable "bigquery_dataset" {
  description = "BigQuery dataset that holds the results table."
  type        = string
  default     = "cyclotorsion_check"
}

variable "bigquery_table" {
  description = "BigQuery results table, dataset.table."
  type        = string
  default     = "cyclotorsion_check.results"
}

variable "backup_bucket_name" {
  description = "GCS bucket for nightly backups (trial runs in us-central1; place in an India region with the rest of the stack before real patient data)."
  type        = string
}

variable "backup_retention_days" {
  description = "Days to retain nightly backups before lifecycle deletion."
  type        = number
  default     = 30
}

variable "enforce_india_residency" {
  description = "When true, Terraform `check`s assert region is asia-south1/asia-south2. Set true before onboarding any real patient data (PRD US-5.2 / R1)."
  type        = bool
  default     = false
}

variable "use_cmek" {
  description = "When true, encrypt BigQuery + the backup bucket with a Google-managed CMEK (KMS) key instead of provider-managed keys. Enable before storing real patient data (LOW-7 hardening)."
  type        = bool
  default     = false
}

variable "kms_key_name" {
  description = "Existing Cloud KMS CryptoKey name for CMEK (projects/P/locations/L/keyRings/R/cryptoKeys/K). Required when use_cmek=true."
  type        = string
  default     = ""

  # CMEK (LOW-7): when use_cmek, a KMS key must be supplied. Was a `check`
  # block in main.tf — see the note on variable "region" above for why it
  # moved (tfsec doesn't parse `check` blocks at all).
  validation {
    condition     = !var.use_cmek || var.kms_key_name != ""
    error_message = "use_cmek requires kms_key_name (projects/P/locations/L/keyRings/R/cryptoKeys/K)."
  }
}

variable "use_patient_store_cloudsql" {
  description = "When true, provision a Cloud SQL (PostgreSQL) instance for the Epic 7 patient-profile store (TDD Section 3.2a) and point Cloud Run at it. The demo trial (synthetic data, patient store default 'memory') keeps this false."
  type        = bool
  default     = false
}

variable "cloud_sql_instance_name" {
  description = "Cloud SQL (PostgreSQL) instance name for the patient-profile store."
  type        = string
  default     = "cyclotorsion-patients"
}

variable "cloud_sql_db_name" {
  description = "Database name inside the Cloud SQL patients instance."
  type        = string
  default     = "cyclotorsion"
}

variable "cloud_sql_tier" {
  description = "Cloud SQL machine tier for the patients instance."
  type        = string
  default     = "db-f1-micro"
}

variable "cloud_sql_password_secret" {
  description = "Secret Manager version holding the Cloud SQL postgres password (projects/P/secrets/x/versions/latest). Required when use_patient_store_cloudsql=true."
  type        = string
  default     = ""
}

variable "cloud_sql_public_ip" {
  description = "When true, assign a public IP to the Cloud SQL patients instance. Production should prefer the private IP + a Cloud Run VPC connector; the public-IP option exists for simpler trial deploys."
  type        = bool
  default     = true
}

variable "min_instances" {
  description = "Minimum Cloud Run instances. The rate limiter and concurrency guard are in-memory, per-replica (V1 design); see allow_multi_replica_rate_limits. Keep at 1 unless a shared backing store is in place."
  type        = number
  default     = 1

  # Per-instance rate/concurrency state (architecture review Major): rate_limit
  # and the concurrency guard are in-memory, per-replica. Scaling beyond one
  # replica silently bypasses them cluster-wide and splits in-memory /stats in
  # dev mode. Fail the plan unless the operator has explicitly acknowledged
  # the tradeoff. Was a `check` block in main.tf — see the note on variable
  # "region" above for why it moved (tfsec doesn't parse `check` blocks).
  validation {
    condition     = !(var.min_instances > 1) || var.allow_multi_replica_rate_limits
    error_message = "min_instances>1 bypasses the in-memory rate limiter & concurrency guard across replicas and splits in-memory /stats. Set allow_multi_replica_rate_limits=true only after moving rate/concurrency/analytics state to a shared backing store (Memorystore/Firestore)."
  }
}

variable "max_instances" {
  description = "Maximum Cloud Run instances. Autoscaling above 1 replica silently fragments the in-memory rate limiter, concurrency guard, and analytics queue the same way min_instances>1 does (staff review Major) — gated by the same allow_multi_replica_rate_limits acknowledgment."
  type        = number
  default     = 1

  # Same invariant as min_instances above, but for the actual scale CEILING
  # (staff review Major): gating only min_instances misses the failure mode
  # that matters most — Cloud Run autoscaling out to max_instances under real
  # load, which is exactly when the rate limiter and concurrency guard exist
  # to protect the service. Was a `check` block in main.tf.
  validation {
    condition     = !(var.max_instances > 1) || var.allow_multi_replica_rate_limits
    error_message = "max_instances>1 lets Cloud Run autoscale out under load, fragmenting the in-memory rate limiter, concurrency guard, and analytics queue across replicas exactly when they matter most. Set allow_multi_replica_rate_limits=true only after moving that state to a shared backing store (Memorystore/Firestore)."
  }
}

variable "allow_multi_replica_rate_limits" {
  description = "Acknowledge that rate/concurrency limits are per-replica when min_instances>1. Setting this true suppresses the Terraform `check` so operators can scale out only after moving those stores to a shared backing store (Memorystore/Firestore)."
  type        = bool
  default     = false
}

variable "notification_email" {
  description = "Alert notification email (monitoring notification channel)."
  type        = string
}

variable "shutdown_hook" {
  description = "Internal placeholder reserved for future use."
  type        = string
  default     = ""
}
