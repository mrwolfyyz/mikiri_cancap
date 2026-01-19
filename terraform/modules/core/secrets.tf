# =============================================================================
# Secret Manager Secrets
# =============================================================================
# Creates Secret Manager secrets for API keys and sensitive configuration.
# Actual secret values are set via gcloud CLI after terraform apply.
# =============================================================================

# -----------------------------------------------------------------------------
# Create Secrets
# -----------------------------------------------------------------------------

resource "google_secret_manager_secret" "secrets" {
  for_each  = toset(var.secret_names)
  project   = var.project_id
  secret_id = each.value

  replication {
    auto {}
  }

  labels = local.common_labels

  depends_on = [
    google_project_service.apis["secretmanager.googleapis.com"],
    time_sleep.api_propagation
  ]
}

# -----------------------------------------------------------------------------
# Create Initial Placeholder Versions
# -----------------------------------------------------------------------------
# These are placeholder values that MUST be replaced after terraform apply.
# The lifecycle block prevents Terraform from overwriting values set via gcloud.

resource "google_secret_manager_secret_version" "placeholder" {
  for_each    = toset(var.secret_names)
  secret      = google_secret_manager_secret.secrets[each.value].id
  secret_data = "PLACEHOLDER_VALUE_SET_VIA_GCLOUD_AFTER_TERRAFORM_APPLY"

  lifecycle {
    # Don't update the secret data after initial creation
    # This allows values to be set via gcloud without Terraform overwriting them
    ignore_changes = [secret_data]
  }
}

# -----------------------------------------------------------------------------
# Secret IAM Bindings
# -----------------------------------------------------------------------------
# Grant the functions service account access to read secrets

resource "google_secret_manager_secret_iam_member" "functions_accessor" {
  for_each  = toset(var.secret_names)
  project   = var.project_id
  secret_id = google_secret_manager_secret.secrets[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.functions.email}"
}
