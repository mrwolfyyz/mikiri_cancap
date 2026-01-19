# =============================================================================
# Service Accounts
# =============================================================================
# Creates service accounts for workflows and functions with appropriate
# permissions for their respective operations.
# =============================================================================

# -----------------------------------------------------------------------------
# Workflow Service Account
# -----------------------------------------------------------------------------
# Used by Cloud Workflows to invoke functions with OIDC authentication.
# Must be specified in google_workflows_workflow resource.

resource "google_service_account" "workflow" {
  project      = var.project_id
  account_id   = "workflow-sa"
  display_name = "Workflow Service Account"
  description  = "Service account for Cloud Workflows to invoke functions with OIDC authentication"

  depends_on = [
    google_project_service.apis["iam.googleapis.com"],
    time_sleep.api_propagation
  ]
}

# -----------------------------------------------------------------------------
# Functions Service Account
# -----------------------------------------------------------------------------
# Used by Cloud Functions for accessing GCP services (Vertex AI, Secrets, etc.)

resource "google_service_account" "functions" {
  project      = var.project_id
  account_id   = "functions-sa"
  display_name = "Cloud Functions Service Account"
  description  = "Service account for Cloud Functions to access GCP services"

  depends_on = [
    google_project_service.apis["iam.googleapis.com"],
    time_sleep.api_propagation
  ]
}
