# =============================================================================
# IAM Role Bindings
# =============================================================================
# Configures IAM permissions for service accounts to access GCP resources.
# =============================================================================

# -----------------------------------------------------------------------------
# Workflow Service Account Permissions
# -----------------------------------------------------------------------------

# Workflow SA needs Firestore access to read/write job documents
resource "google_project_iam_member" "workflow_datastore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.workflow.email}"
}

# Workflow SA needs to read workflow state
resource "google_project_iam_member" "workflow_workflows_invoker" {
  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${google_service_account.workflow.email}"
}

# -----------------------------------------------------------------------------
# Functions Service Account Permissions
# -----------------------------------------------------------------------------

# Functions SA needs Vertex AI access for LLM calls
resource "google_project_iam_member" "functions_aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.functions.email}"
}

# Functions SA needs Firestore access
resource "google_project_iam_member" "functions_datastore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.functions.email}"
}

# Functions SA needs to invoke workflows (for API Gateway)
resource "google_project_iam_member" "functions_workflows_invoker" {
  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${google_service_account.functions.email}"
}

# Functions SA needs Google Drive access (for report generators)
# NOTE: roles/drive.file is NOT a project-level IAM role - it's a Drive API scope.
# Drive access must be configured via OAuth scopes or domain-wide delegation, not project IAM.
# This binding has been removed as it's invalid for project-level IAM.
# If Drive API access is needed, configure it separately via domain-wide delegation or OAuth.
# resource "google_project_iam_member" "functions_drive_user" {
#   project = var.project_id
#   role    = "roles/drive.file"
#   member  = "serviceAccount:${google_service_account.functions.email}"
# }

# -----------------------------------------------------------------------------
# Cloud Build Service Account Permissions
# -----------------------------------------------------------------------------
# Cloud Build SA needs permissions to deploy functions

resource "google_project_iam_member" "cloudbuild_functions_developer" {
  project = var.project_id
  role    = "roles/cloudfunctions.developer"
  member  = "serviceAccount:${local.cloudbuild_sa}"

  depends_on = [
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation
  ]
}

resource "google_project_iam_member" "cloudbuild_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${local.cloudbuild_sa}"

  depends_on = [
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation
  ]
}

resource "google_project_iam_member" "cloudbuild_service_account_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${local.cloudbuild_sa}"

  depends_on = [
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation
  ]
}

# Cloud Build SA needs storage permissions at project level to access source code
resource "google_project_iam_member" "cloudbuild_storage_viewer" {
  project = var.project_id
  role    = "roles/storage.objectViewer"
  member  = "serviceAccount:${local.cloudbuild_sa}"

  depends_on = [
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation
  ]
}

# Cloud Build SA needs Artifact Registry permissions to push container images
resource "google_project_iam_member" "cloudbuild_artifactregistry_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${local.cloudbuild_sa}"

  depends_on = [
    google_project_service.apis["artifactregistry.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation
  ]
}

# -----------------------------------------------------------------------------
# Default Compute Service Account Permissions
# -----------------------------------------------------------------------------
# As of May-June 2024, GCP changed Cloud Build default service account behavior:
# New projects now use the default Compute Engine service account for Cloud Build
# instead of the legacy Cloud Build service account. Cloud Functions Gen2 builds
# (which use Cloud Build internally) therefore use the Compute SA by default.
#
# We grant the same permissions to Compute SA that Cloud Build SA has, so that
# Functions Gen2 builds can access source buckets (including gcf-v2-sources-*),
# Artifact Registry, and other required resources.
#
# Note: This is expected GCP behavior due to the 2024 default change, not a workaround.
# Both service accounts may be used in different contexts, so both need permissions.

resource "google_project_iam_member" "compute_functions_developer" {
  project = var.project_id
  role    = "roles/cloudfunctions.developer"
  member  = "serviceAccount:${local.compute_sa}"

  depends_on = [
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation
  ]
}

resource "google_project_iam_member" "compute_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${local.compute_sa}"

  depends_on = [
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation
  ]
}

resource "google_project_iam_member" "compute_service_account_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${local.compute_sa}"

  depends_on = [
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation
  ]
}

# Compute SA needs storage permissions at project level to access source code
# This is required for accessing gcf-v2-sources-* buckets used by Functions Gen2
resource "google_project_iam_member" "compute_storage_viewer" {
  project = var.project_id
  role    = "roles/storage.objectViewer"
  member  = "serviceAccount:${local.compute_sa}"

  depends_on = [
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation
  ]
}

# Compute SA needs Artifact Registry permissions to push container images
resource "google_project_iam_member" "compute_artifactregistry_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${local.compute_sa}"

  depends_on = [
    google_project_service.apis["artifactregistry.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation
  ]
}

# Compute SA needs storage permissions on function source bucket
resource "google_storage_bucket_iam_member" "compute_object_admin" {
  bucket = google_storage_bucket.function_source.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${local.compute_sa}"

  depends_on = [
    google_project_service.apis["cloudbuild.googleapis.com"],
    google_storage_bucket.function_source,
    time_sleep.api_propagation
  ]
}

# Compute SA needs Eventarc permissions for trigger creation/validation
# When Cloud Functions Gen2 creates Eventarc triggers (e.g., Firestore triggers),
# GCP validates that the build service account (Compute SA) has eventarc.events.receiveEvent
# permission. This is required even though Eventarc SA is the one that actually delivers events.
resource "google_project_iam_member" "compute_eventarc_eventreceiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${local.compute_sa}"

  depends_on = [
    google_project_service.apis["eventarc.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation
  ]
}

# -----------------------------------------------------------------------------
# Eventarc Service Account Permissions
# -----------------------------------------------------------------------------
# Eventarc SA needs roles/eventarc.eventReceiver to receive events from event providers.
# This is required by GCP Eventarc documentation for triggers to function properly.
#
# Note: Eventarc SA is Google-managed and created automatically when Eventarc API is enabled.
# However, it may take 2-3 minutes to be created after API enablement. This binding may fail
# on first apply if the SA doesn't exist yet. This is expected GCP behavior, not a Terraform flaw.
# 
# Functions with event_trigger blocks do NOT depend on this binding at creation time - they
# need it at runtime. Therefore, this binding is removed from function depends_on lists to
# allow functions to be created independently. If this binding fails on first apply, it will
# succeed on second apply once the Eventarc SA is created.
#
# The Cloud Run IAM bindings (roles/run.invoker) for Eventarc SA are also required and are
# created independently in functions.tf after the functions are created.
resource "google_project_iam_member" "eventarc_eventreceiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${local.eventarc_sa}"

  depends_on = [
    google_project_service.apis["eventarc.googleapis.com"],
    time_sleep.api_propagation
  ]
}
