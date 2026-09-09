output "cloud_run_service" {
  description = "Cloud Run API service name."
  value       = google_cloud_run_v2_service.api.name
}

output "cloud_run_url" {
  description = "Public base URL of the API service."
  value       = google_cloud_run_v2_service.api.uri
}

output "backup_job" {
  description = "Cloud Run backup job name."
  value       = google_cloud_run_v2_job.backup.name
}

output "backup_bucket" {
  description = "GCS backup bucket."
  value       = google_storage_bucket.backups.name
}

output "bigquery_results_table" {
  description = "Fully-qualified BigQuery results table (project.dataset.table)."
  value       = "${var.project_id}.${google_bigquery_table.results.dataset_id}.${google_bigquery_table.results.table_id}"
}
