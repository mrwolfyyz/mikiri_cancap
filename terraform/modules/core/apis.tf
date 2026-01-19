# =============================================================================
# GCP API Enablement
# =============================================================================
# Enables all required GCP APIs for the skip trace & origination platform.
# APIs must be enabled before any dependent resources can be created.
# =============================================================================

locals {
  required_apis = [
    # Core infrastructure
    "cloudfunctions.googleapis.com",      # Cloud Functions Gen2
    "run.googleapis.com",                 # Cloud Run (required for Gen2 functions)
    "workflows.googleapis.com",           # Cloud Workflows
    "firestore.googleapis.com",           # Firestore
    "secretmanager.googleapis.com",       # Secret Manager
    "aiplatform.googleapis.com",          # Vertex AI
    "eventarc.googleapis.com",            # Eventarc (for Firestore triggers)
    "cloudbuild.googleapis.com",          # Cloud Build (for function deployment)
    "artifactregistry.googleapis.com",    # Artifact Registry (for container images)
    "storage.googleapis.com",             # Cloud Storage (for function source)

    # External services
    "drive.googleapis.com",               # Google Drive API (for report storage)
    "customsearch.googleapis.com",        # Google Custom Search (PSE)

    # Firebase
    "firebase.googleapis.com",            # Firebase
    "firebasehosting.googleapis.com",     # Firebase Hosting
    "identitytoolkit.googleapis.com",     # Firebase Auth / Identity Platform

    # IAM and project management
    "iam.googleapis.com",                 # IAM API
    "cloudresourcemanager.googleapis.com", # Resource Manager (for project-level IAM)
    "serviceusage.googleapis.com",        # Service Usage API
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.required_apis)
  project  = var.project_id
  service  = each.value

  # Don't disable dependent services when this resource is destroyed
  disable_dependent_services = false

  # Don't disable the API when the resource is destroyed
  # This prevents accidental data loss
  disable_on_destroy = false

  timeouts {
    create = "30m"
    update = "40m"
  }
}

# Wait for APIs to propagate before creating dependent resources
# Some APIs take a few moments to be fully ready after enablement
resource "time_sleep" "api_propagation" {
  depends_on = [google_project_service.apis]

  create_duration = "90s"  # Increased to 90s to allow Eventarc SA to be created
}
