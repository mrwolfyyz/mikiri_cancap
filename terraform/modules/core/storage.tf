# =============================================================================
# Cloud Storage
# =============================================================================
# Creates GCS bucket for function source code storage.
# =============================================================================

# -----------------------------------------------------------------------------
# Function Source Bucket
# -----------------------------------------------------------------------------

resource "google_storage_bucket" "function_source" {
  project  = var.project_id
  name     = "${var.project_id}-function-source"
  location = var.region

  # Uniform bucket-level access (recommended)
  uniform_bucket_level_access = true

  # Lifecycle rule to clean up old function source archives
  lifecycle_rule {
    condition {
      age = 30 # Delete after 30 days
    }
    action {
      type = "Delete"
    }
  }

  # Versioning for safety
  versioning {
    enabled = true
  }

  labels = local.common_labels

  depends_on = [
    google_project_service.apis["storage.googleapis.com"],
    time_sleep.api_propagation
  ]
}

# Grant Cloud Build access to the bucket
resource "google_storage_bucket_iam_member" "cloudbuild_object_admin" {
  bucket = google_storage_bucket.function_source.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${local.cloudbuild_sa}"

  depends_on = [
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation
  ]
}
