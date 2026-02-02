# =============================================================================
# Cloud Functions Gen2
# =============================================================================
# Deploys all Cloud Functions using Gen2 (Cloud Run-backed).
# Functions are organized by their authentication requirements:
# - Public: api_gateway, chat_handler, chat_handler_origination, address_verification
# - OIDC (workflow-invoked): phase1_identity, domain_enrichment, address_geocoding, company_domain_lookup, aggregator
# - Eventarc-triggered: report_generator_skiptrace, report_generator_origination
# =============================================================================

# -----------------------------------------------------------------------------
# Local Variables - Common Dependencies
# -----------------------------------------------------------------------------

locals {
  # Common dependencies for all Cloud Functions deployments
  # All functions require Cloud Build (and Compute SA) to have permissions and bucket access before deployment
  # Note: As of May-June 2024, GCP changed Cloud Build default to use Compute SA instead of Cloud Build SA.
  # Cloud Functions Gen2 builds use Compute SA by default, so both service accounts need permissions.
  cloud_build_dependencies = [
    google_project_service.apis["cloudfunctions.googleapis.com"],
    google_project_service.apis["run.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation,
    # Cloud Build SA permissions (for explicit Cloud Build triggers)
    google_project_iam_member.cloudbuild_functions_developer,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_service_account_user,
    google_storage_bucket_iam_member.cloudbuild_object_admin,
    # Compute SA permissions (for Functions Gen2 internal builds - default as of 2024)
    google_project_iam_member.compute_functions_developer,
    google_project_iam_member.compute_run_admin,
    google_project_iam_member.compute_service_account_user,
    google_project_iam_member.compute_storage_viewer,
    google_project_iam_member.compute_artifactregistry_writer,
    google_storage_bucket_iam_member.compute_object_admin,
    # Note: Eventarc permission is NOT included here as it's only needed for Eventarc-triggered functions
  ]
}

# -----------------------------------------------------------------------------
# Source Preparation
# -----------------------------------------------------------------------------
# Ensure retry_utils.py is present in domain_enrichment (it's missing in source)

resource "null_resource" "prepare_domain_enrichment" {
  triggers = {
    # Re-run if the source file changes
    source_hash = filemd5("${path.module}/../../../gcp/shared/retry_utils.py")
  }

  provisioner "local-exec" {
    command = "cp ${path.module}/../../../gcp/shared/retry_utils.py ${path.module}/../../../gcp/functions/domain_enrichment/"
  }
}

# -----------------------------------------------------------------------------
# Function Source Archives
# -----------------------------------------------------------------------------

data "archive_file" "api_gateway" {
  type        = "zip"
  source_dir  = "${path.module}/../../../gcp/functions/api_gateway"
  output_path = "${path.module}/../../../.build/api_gateway.zip"
}

data "archive_file" "phase1_identity" {
  type        = "zip"
  source_dir  = "${path.module}/../../../gcp/functions/phase1_identity"
  output_path = "${path.module}/../../../.build/phase1_identity.zip"
}

data "archive_file" "domain_enrichment" {
  type        = "zip"
  source_dir  = "${path.module}/../../../gcp/functions/domain_enrichment"
  output_path = "${path.module}/../../../.build/domain_enrichment.zip"

  depends_on = [null_resource.prepare_domain_enrichment]
}

data "archive_file" "address_geocoding" {
  type        = "zip"
  source_dir  = "${path.module}/../../../gcp/functions/address_geocoding"
  output_path = "${path.module}/../../../.build/address_geocoding.zip"
}

data "archive_file" "company_domain_lookup" {
  type        = "zip"
  source_dir  = "${path.module}/../../../gcp/functions/company_domain_lookup"
  output_path = "${path.module}/../../../.build/company_domain_lookup.zip"
}

data "archive_file" "aggregator" {
  type        = "zip"
  source_dir  = "${path.module}/../../../gcp/functions/aggregator"
  output_path = "${path.module}/../../../.build/aggregator.zip"
}

data "archive_file" "report_generator_skiptrace" {
  type        = "zip"
  source_dir  = "${path.module}/../../../gcp/functions/report_generator_skiptrace"
  output_path = "${path.module}/../../../.build/report_generator_skiptrace.zip"
}

data "archive_file" "report_generator_origination" {
  type        = "zip"
  source_dir  = "${path.module}/../../../gcp/functions/report_generator_origination"
  output_path = "${path.module}/../../../.build/report_generator_origination.zip"
}

data "archive_file" "chat_handler" {
  type        = "zip"
  source_dir  = "${path.module}/../../../gcp/functions/chat_handler"
  output_path = "${path.module}/../../../.build/chat_handler.zip"
}

data "archive_file" "chat_handler_origination" {
  type        = "zip"
  source_dir  = "${path.module}/../../../gcp/functions/chat_handler_origination"
  output_path = "${path.module}/../../../.build/chat_handler_origination.zip"
}

data "archive_file" "address_verification" {
  type        = "zip"
  source_dir  = "${path.module}/../../../gcp/functions/address_verification"
  output_path = "${path.module}/../../../.build/address_verification.zip"
}

data "archive_file" "contact_extraction" {
  type        = "zip"
  source_dir  = "${path.module}/../../../gcp/functions/contact_extraction"
  output_path = "${path.module}/../../../.build/contact_extraction.zip"
}

# -----------------------------------------------------------------------------
# Upload Source Archives to GCS
# -----------------------------------------------------------------------------

resource "google_storage_bucket_object" "api_gateway" {
  name   = "api_gateway-${data.archive_file.api_gateway.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.api_gateway.output_path
}

resource "google_storage_bucket_object" "phase1_identity" {
  name   = "phase1_identity-${data.archive_file.phase1_identity.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.phase1_identity.output_path
}

resource "google_storage_bucket_object" "domain_enrichment" {
  name   = "domain_enrichment-${data.archive_file.domain_enrichment.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.domain_enrichment.output_path
}

resource "google_storage_bucket_object" "address_geocoding" {
  name   = "address_geocoding-${data.archive_file.address_geocoding.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.address_geocoding.output_path
}

resource "google_storage_bucket_object" "company_domain_lookup" {
  name   = "company_domain_lookup-${data.archive_file.company_domain_lookup.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.company_domain_lookup.output_path
}

resource "google_storage_bucket_object" "aggregator" {
  name   = "aggregator-${data.archive_file.aggregator.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.aggregator.output_path
}

resource "google_storage_bucket_object" "report_generator_skiptrace" {
  name   = "report_generator_skiptrace-${data.archive_file.report_generator_skiptrace.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.report_generator_skiptrace.output_path
}

resource "google_storage_bucket_object" "report_generator_origination" {
  name   = "report_generator_origination-${data.archive_file.report_generator_origination.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.report_generator_origination.output_path
}

resource "google_storage_bucket_object" "chat_handler" {
  name   = "chat_handler-${data.archive_file.chat_handler.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.chat_handler.output_path
}

resource "google_storage_bucket_object" "chat_handler_origination" {
  name   = "chat_handler_origination-${data.archive_file.chat_handler_origination.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.chat_handler_origination.output_path
}

resource "google_storage_bucket_object" "address_verification" {
  name   = "address_verification-${data.archive_file.address_verification.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.address_verification.output_path
}

resource "google_storage_bucket_object" "contact_extraction" {
  name   = "contact_extraction-${data.archive_file.contact_extraction.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.contact_extraction.output_path
}

# =============================================================================
# CLOUD FUNCTIONS - DEPLOY DEPENDENT FUNCTIONS FIRST
# =============================================================================
# chat_handler, chat_handler_origination, address_verification must be deployed
# BEFORE api_gateway because api_gateway needs their URLs as environment variables

# -----------------------------------------------------------------------------
# Chat Handler (Public)
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "chat_handler" {
  project  = var.project_id
  name     = "chat-handler"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.chat_handler.name
      }
    }
  }

  service_config {
    max_instance_count    = var.function_max_instances["chat_handler"]
    min_instance_count    = 0
    available_memory      = var.function_memory["chat_handler"]
    timeout_seconds       = var.function_timeout["chat_handler"]
    service_account_email = google_service_account.functions.email

    environment_variables = {
      GCP_PROJECT  = var.project_id
      GCP_LOCATION = "global"  # Use global endpoint for Gemini models
    }
  }

  labels = local.common_labels

  # Cloud Build dependencies required for all function deployments
  # Includes both Cloud Build SA and Compute SA permissions (required as of 2024 default change)
  depends_on = [
    google_project_service.apis["cloudfunctions.googleapis.com"],
    google_project_service.apis["run.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation,
    # Cloud Build SA permissions (for explicit Cloud Build triggers)
    google_project_iam_member.cloudbuild_functions_developer,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_service_account_user,
    google_storage_bucket_iam_member.cloudbuild_object_admin,
    # Compute SA permissions (for Functions Gen2 internal builds - default as of 2024)
    google_project_iam_member.compute_functions_developer,
    google_project_iam_member.compute_run_admin,
    google_project_iam_member.compute_service_account_user,
    google_project_iam_member.compute_storage_viewer,
    google_project_iam_member.compute_artifactregistry_writer,
    google_storage_bucket_iam_member.compute_object_admin,
  ]
}

# Allow public access to chat_handler (API Gateway verifies Firebase token before proxying)
resource "google_cloud_run_service_iam_member" "chat_handler_invoker" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.chat_handler.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# -----------------------------------------------------------------------------
# Chat Handler Origination (Public)
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "chat_handler_origination" {
  project  = var.project_id
  name     = "chat-handler-origination"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.chat_handler_origination.name
      }
    }
  }

  service_config {
    max_instance_count    = var.function_max_instances["chat_handler_origination"]
    min_instance_count    = 0
    available_memory      = var.function_memory["chat_handler_origination"]
    timeout_seconds       = var.function_timeout["chat_handler_origination"]
    service_account_email = google_service_account.functions.email

    environment_variables = {
      GCP_PROJECT  = var.project_id
      GCP_LOCATION = "global"  # Use global endpoint for Gemini models
    }
  }

  labels = local.common_labels

  # Cloud Build dependencies required for all function deployments
  # Includes both Cloud Build SA and Compute SA permissions (required as of 2024 default change)
  depends_on = [
    google_project_service.apis["cloudfunctions.googleapis.com"],
    google_project_service.apis["run.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation,
    # Cloud Build SA permissions (for explicit Cloud Build triggers)
    google_project_iam_member.cloudbuild_functions_developer,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_service_account_user,
    google_storage_bucket_iam_member.cloudbuild_object_admin,
    # Compute SA permissions (for Functions Gen2 internal builds - default as of 2024)
    google_project_iam_member.compute_functions_developer,
    google_project_iam_member.compute_run_admin,
    google_project_iam_member.compute_service_account_user,
    google_project_iam_member.compute_storage_viewer,
    google_project_iam_member.compute_artifactregistry_writer,
    google_storage_bucket_iam_member.compute_object_admin,
  ]
}

resource "google_cloud_run_service_iam_member" "chat_handler_origination_invoker" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.chat_handler_origination.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# -----------------------------------------------------------------------------
# Address Verification (Public)
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "address_verification" {
  project  = var.project_id
  name     = "address-verification"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.address_verification.name
      }
    }
  }

  service_config {
    max_instance_count    = var.function_max_instances["address_verification"]
    min_instance_count    = 0
    available_memory      = var.function_memory["address_verification"]
    timeout_seconds       = var.function_timeout["address_verification"]
    service_account_email = google_service_account.functions.email

    environment_variables = {
      GCP_PROJECT  = var.project_id
      GCP_LOCATION = "global"  # Use global endpoint for Gemini models
    }
  }

  labels = local.common_labels

  # Cloud Build dependencies + secret manager
  # Includes both Cloud Build SA and Compute SA permissions (required as of 2024 default change)
  depends_on = [
    google_project_service.apis["cloudfunctions.googleapis.com"],
    google_project_service.apis["run.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation,
    # Cloud Build SA permissions (for explicit Cloud Build triggers)
    google_project_iam_member.cloudbuild_functions_developer,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_service_account_user,
    google_storage_bucket_iam_member.cloudbuild_object_admin,
    # Compute SA permissions (for Functions Gen2 internal builds - default as of 2024)
    google_project_iam_member.compute_functions_developer,
    google_project_iam_member.compute_run_admin,
    google_project_iam_member.compute_service_account_user,
    google_project_iam_member.compute_storage_viewer,
    google_project_iam_member.compute_artifactregistry_writer,
    google_storage_bucket_iam_member.compute_object_admin,
    # Secret manager
    google_secret_manager_secret_version.placeholder,
  ]
}

resource "google_cloud_run_service_iam_member" "address_verification_invoker" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.address_verification.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# =============================================================================
# API GATEWAY (depends on chat_handler, chat_handler_origination, address_verification)
# =============================================================================

resource "google_cloudfunctions2_function" "api_gateway" {
  project  = var.project_id
  name     = "api-gateway"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.api_gateway.name
      }
    }
  }

  service_config {
    max_instance_count    = var.function_max_instances["api_gateway"]
    min_instance_count    = 0
    available_memory      = var.function_memory["api_gateway"]
    timeout_seconds       = var.function_timeout["api_gateway"]
    service_account_email = google_service_account.functions.email

    environment_variables = {
      GCP_PROJECT                  = var.project_id
      GCP_LOCATION                 = var.region
      SKIPTRACE_WORKFLOW_NAME      = var.skiptrace_workflow_name
      ORIGINATION_WORKFLOW_NAME    = var.origination_workflow_name
      CORS_ALLOWED_ORIGINS         = var.cors_allowed_origins
      CHAT_HANDLER_URL             = google_cloudfunctions2_function.chat_handler.service_config[0].uri
      CHAT_HANDLER_ORIGINATION_URL = google_cloudfunctions2_function.chat_handler_origination.service_config[0].uri
      ADDRESS_VERIFICATION_URL     = google_cloudfunctions2_function.address_verification.service_config[0].uri
    }
  }

  labels = local.common_labels

  # Cloud Build dependencies + function dependencies (api_gateway needs URLs from other functions)
  # Includes both Cloud Build SA and Compute SA permissions (required as of 2024 default change)
  depends_on = [
    google_project_service.apis["cloudfunctions.googleapis.com"],
    google_project_service.apis["run.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation,
    # Cloud Build SA permissions (for explicit Cloud Build triggers)
    google_project_iam_member.cloudbuild_functions_developer,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_service_account_user,
    google_storage_bucket_iam_member.cloudbuild_object_admin,
    # Compute SA permissions (for Functions Gen2 internal builds - default as of 2024)
    google_project_iam_member.compute_functions_developer,
    google_project_iam_member.compute_run_admin,
    google_project_iam_member.compute_service_account_user,
    google_project_iam_member.compute_storage_viewer,
    google_project_iam_member.compute_artifactregistry_writer,
    google_storage_bucket_iam_member.compute_object_admin,
    # Function dependencies (api_gateway needs URLs from other functions)
    google_cloudfunctions2_function.chat_handler,
    google_cloudfunctions2_function.chat_handler_origination,
    google_cloudfunctions2_function.address_verification,
  ]
}

# Allow public access to API Gateway
resource "google_cloud_run_service_iam_member" "api_gateway_invoker" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.api_gateway.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# =============================================================================
# WORKFLOW-INVOKED FUNCTIONS (OIDC authenticated)
# =============================================================================

# -----------------------------------------------------------------------------
# Phase 1 Identity
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "phase1_identity" {
  project  = var.project_id
  name     = "phase1-identity"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.phase1_identity.name
      }
    }
  }

  service_config {
    max_instance_count    = var.function_max_instances["phase1_identity"]
    min_instance_count    = 0
    available_memory      = var.function_memory["phase1_identity"]
    timeout_seconds       = var.function_timeout["phase1_identity"]
    service_account_email = google_service_account.functions.email

    environment_variables = {
      GCP_PROJECT            = var.project_id
      GCP_LOCATION           = "global"  # Use global endpoint for Gemini models
      LINKEDIN_ENGINE_ID     = "linkedin-search-engine"
      LINKEDIN_USE_VERTEX_AI = "true"
      PRECISION_ENGINE_ID    = "precision-search-engine"
      PRECISION_USE_VERTEX_AI = "true"
      RECALL_ENGINE_ID       = "recall-search-engine"
      RECALL_USE_VERTEX_AI   = "true"
    }

    secret_environment_variables {
      key        = "HIBP_API_KEY"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["HIBP_API_KEY"].secret_id
      version    = "latest"
    }
  }

  labels = local.common_labels

  # Cloud Build dependencies + secret manager
  # Includes both Cloud Build SA and Compute SA permissions (required as of 2024 default change)
  depends_on = [
    google_project_service.apis["cloudfunctions.googleapis.com"],
    google_project_service.apis["run.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation,
    # Cloud Build SA permissions (for explicit Cloud Build triggers)
    google_project_iam_member.cloudbuild_functions_developer,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_service_account_user,
    google_storage_bucket_iam_member.cloudbuild_object_admin,
    # Compute SA permissions (for Functions Gen2 internal builds - default as of 2024)
    google_project_iam_member.compute_functions_developer,
    google_project_iam_member.compute_run_admin,
    google_project_iam_member.compute_service_account_user,
    google_project_iam_member.compute_storage_viewer,
    google_project_iam_member.compute_artifactregistry_writer,
    google_storage_bucket_iam_member.compute_object_admin,
    # Secret manager
    google_secret_manager_secret_version.placeholder,
  ]
}

# Grant workflow SA permission to invoke
resource "google_cloud_run_service_iam_member" "workflow_invoke_phase1_identity" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.phase1_identity.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.workflow.email}"
}

# -----------------------------------------------------------------------------
# Domain Enrichment
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "domain_enrichment" {
  project  = var.project_id
  name     = "domain-enrichment"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.domain_enrichment.name
      }
    }
  }

  service_config {
    max_instance_count    = var.function_max_instances["domain_enrichment"]
    min_instance_count    = 0
    available_memory      = var.function_memory["domain_enrichment"]
    timeout_seconds       = var.function_timeout["domain_enrichment"]
    service_account_email = google_service_account.functions.email

    environment_variables = {
      GCP_PROJECT  = var.project_id
      GCP_LOCATION = var.region
    }
  }

  labels = local.common_labels

  # Cloud Build dependencies + domain_enrichment preparation
  # Includes both Cloud Build SA and Compute SA permissions (required as of 2024 default change)
  depends_on = [
    google_project_service.apis["cloudfunctions.googleapis.com"],
    google_project_service.apis["run.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation,
    # Cloud Build SA permissions (for explicit Cloud Build triggers)
    google_project_iam_member.cloudbuild_functions_developer,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_service_account_user,
    google_storage_bucket_iam_member.cloudbuild_object_admin,
    # Compute SA permissions (for Functions Gen2 internal builds - default as of 2024)
    google_project_iam_member.compute_functions_developer,
    google_project_iam_member.compute_run_admin,
    google_project_iam_member.compute_service_account_user,
    google_project_iam_member.compute_storage_viewer,
    google_project_iam_member.compute_artifactregistry_writer,
    google_storage_bucket_iam_member.compute_object_admin,
    # Domain enrichment preparation
    null_resource.prepare_domain_enrichment,
  ]
}

resource "google_cloud_run_service_iam_member" "workflow_invoke_domain_enrichment" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.domain_enrichment.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.workflow.email}"
}

# -----------------------------------------------------------------------------
# Address Geocoding
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "address_geocoding" {
  project  = var.project_id
  name     = "address-geocoding"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.address_geocoding.name
      }
    }
  }

  service_config {
    max_instance_count    = var.function_max_instances["address_geocoding"]
    min_instance_count    = 0
    available_memory      = var.function_memory["address_geocoding"]
    timeout_seconds       = var.function_timeout["address_geocoding"]
    service_account_email = google_service_account.functions.email

    environment_variables = {
      GCP_PROJECT  = var.project_id
      GCP_LOCATION = "global"  # Use global endpoint for Gemini models
    }
  }

  labels = local.common_labels

  # Cloud Build dependencies required for all function deployments
  # Includes both Cloud Build SA and Compute SA permissions (required as of 2024 default change)
  depends_on = [
    google_project_service.apis["cloudfunctions.googleapis.com"],
    google_project_service.apis["run.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation,
    # Cloud Build SA permissions (for explicit Cloud Build triggers)
    google_project_iam_member.cloudbuild_functions_developer,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_service_account_user,
    google_storage_bucket_iam_member.cloudbuild_object_admin,
    # Compute SA permissions (for Functions Gen2 internal builds - default as of 2024)
    google_project_iam_member.compute_functions_developer,
    google_project_iam_member.compute_run_admin,
    google_project_iam_member.compute_service_account_user,
    google_project_iam_member.compute_storage_viewer,
    google_project_iam_member.compute_artifactregistry_writer,
    google_storage_bucket_iam_member.compute_object_admin,
  ]
}

resource "google_cloud_run_service_iam_member" "workflow_invoke_address_geocoding" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.address_geocoding.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.workflow.email}"
}

# -----------------------------------------------------------------------------
# Company Domain Lookup
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "company_domain_lookup" {
  project  = var.project_id
  name     = "company-domain-lookup"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.company_domain_lookup.name
      }
    }
  }

  service_config {
    max_instance_count    = var.function_max_instances["company_domain_lookup"]
    min_instance_count    = 0
    available_memory      = var.function_memory["company_domain_lookup"]
    timeout_seconds       = var.function_timeout["company_domain_lookup"]
    service_account_email = google_service_account.functions.email

    environment_variables = {
      GCP_PROJECT  = var.project_id
      GCP_LOCATION = "global"  # Use global endpoint for Gemini models
    }
  }

  labels = local.common_labels

  # Cloud Build dependencies + secret manager
  # Includes both Cloud Build SA and Compute SA permissions (required as of 2024 default change)
  depends_on = [
    google_project_service.apis["cloudfunctions.googleapis.com"],
    google_project_service.apis["run.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation,
    # Cloud Build SA permissions (for explicit Cloud Build triggers)
    google_project_iam_member.cloudbuild_functions_developer,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_service_account_user,
    google_storage_bucket_iam_member.cloudbuild_object_admin,
    # Compute SA permissions (for Functions Gen2 internal builds - default as of 2024)
    google_project_iam_member.compute_functions_developer,
    google_project_iam_member.compute_run_admin,
    google_project_iam_member.compute_service_account_user,
    google_project_iam_member.compute_storage_viewer,
    google_project_iam_member.compute_artifactregistry_writer,
    google_storage_bucket_iam_member.compute_object_admin,
    # Secret manager
    google_secret_manager_secret_version.placeholder,
  ]
}

resource "google_cloud_run_service_iam_member" "workflow_invoke_company_domain_lookup" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.company_domain_lookup.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.workflow.email}"
}

# -----------------------------------------------------------------------------
# Aggregator
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "aggregator" {
  project  = var.project_id
  name     = "aggregator"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.aggregator.name
      }
    }
  }

  service_config {
    max_instance_count    = var.function_max_instances["aggregator"]
    min_instance_count    = 0
    available_memory      = var.function_memory["aggregator"]
    timeout_seconds       = var.function_timeout["aggregator"]
    service_account_email = google_service_account.functions.email

    environment_variables = {
      GCP_PROJECT  = var.project_id
      GCP_LOCATION = var.region
    }
  }

  labels = local.common_labels

  # Cloud Build dependencies required for all function deployments
  # Includes both Cloud Build SA and Compute SA permissions (required as of 2024 default change)
  depends_on = [
    google_project_service.apis["cloudfunctions.googleapis.com"],
    google_project_service.apis["run.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation,
    # Cloud Build SA permissions (for explicit Cloud Build triggers)
    google_project_iam_member.cloudbuild_functions_developer,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_service_account_user,
    google_storage_bucket_iam_member.cloudbuild_object_admin,
    # Compute SA permissions (for Functions Gen2 internal builds - default as of 2024)
    google_project_iam_member.compute_functions_developer,
    google_project_iam_member.compute_run_admin,
    google_project_iam_member.compute_service_account_user,
    google_project_iam_member.compute_storage_viewer,
    google_project_iam_member.compute_artifactregistry_writer,
    google_storage_bucket_iam_member.compute_object_admin,
  ]
}

resource "google_cloud_run_service_iam_member" "workflow_invoke_aggregator" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.aggregator.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.workflow.email}"
}

# -----------------------------------------------------------------------------
# Contact Extraction
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "contact_extraction" {
  project  = var.project_id
  name     = "contact-extraction"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.contact_extraction.name
      }
    }
  }

  service_config {
    max_instance_count    = var.function_max_instances["contact_extraction"]
    min_instance_count    = 0
    available_memory      = var.function_memory["contact_extraction"]
    timeout_seconds       = var.function_timeout["contact_extraction"]
    service_account_email = google_service_account.functions.email

    environment_variables = {
      GCP_PROJECT  = var.project_id
      GCP_LOCATION = "global"  # Use global endpoint for Gemini
    }
  }

  labels = local.common_labels

  # Cloud Build dependencies required for all function deployments
  # Includes both Cloud Build SA and Compute SA permissions (required as of 2024 default change)
  depends_on = [
    google_project_service.apis["cloudfunctions.googleapis.com"],
    google_project_service.apis["run.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation,
    # Cloud Build SA permissions (for explicit Cloud Build triggers)
    google_project_iam_member.cloudbuild_functions_developer,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_service_account_user,
    google_storage_bucket_iam_member.cloudbuild_object_admin,
    # Compute SA permissions (for Functions Gen2 internal builds - default as of 2024)
    google_project_iam_member.compute_functions_developer,
    google_project_iam_member.compute_run_admin,
    google_project_iam_member.compute_service_account_user,
    google_project_iam_member.compute_storage_viewer,
    google_project_iam_member.compute_artifactregistry_writer,
    google_storage_bucket_iam_member.compute_object_admin,
  ]
}

# Grant workflow SA permission to invoke
resource "google_cloud_run_service_iam_member" "workflow_invoke_contact_extraction" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.contact_extraction.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.workflow.email}"
}

# =============================================================================
# EVENTARC-TRIGGERED FUNCTIONS (Report Generators)
# =============================================================================

# -----------------------------------------------------------------------------
# Report Generator - Skip Trace
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "report_generator_skiptrace" {
  project  = var.project_id
  name     = "report-generator-skiptrace"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "on_job_updated"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.report_generator_skiptrace.name
      }
    }
  }

  service_config {
    max_instance_count    = var.function_max_instances["report_generator_skiptrace"]
    min_instance_count    = 0
    available_memory      = var.function_memory["report_generator_skiptrace"]
    timeout_seconds       = var.function_timeout["report_generator_skiptrace"]
    service_account_email = google_service_account.functions.email

    environment_variables = {
      GCP_PROJECT  = var.project_id
      GCP_LOCATION = "global"  # Use global endpoint for Gemini models
    }
  }

  # Eventarc trigger for Firestore document updates
  event_trigger {
    trigger_region = var.region
    event_type     = "google.cloud.firestore.document.v1.updated"
    retry_policy   = "RETRY_POLICY_RETRY"

    event_filters {
      attribute = "database"
      value     = "(default)"
    }
    event_filters {
      attribute = "document"
      value     = "jobs/{jobId}"
      operator  = "match-path-pattern"
    }
  }

  labels = local.common_labels

  # Cloud Build dependencies + Eventarc dependencies
  # Includes both Cloud Build SA and Compute SA permissions (required as of 2024 default change)
  # Note: eventarc_eventreceiver binding is not in depends_on because Eventarc SA is Google-managed
  # and may not exist immediately after API enablement. Functions can be created independently;
  # the IAM binding will succeed on second apply if it failed on first.
  depends_on = [
    google_project_service.apis["cloudfunctions.googleapis.com"],
    google_project_service.apis["run.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation,
    # Cloud Build SA permissions (for explicit Cloud Build triggers)
    google_project_iam_member.cloudbuild_functions_developer,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_service_account_user,
    google_storage_bucket_iam_member.cloudbuild_object_admin,
    # Compute SA permissions (for Functions Gen2 internal builds - default as of 2024)
    google_project_iam_member.compute_functions_developer,
    google_project_iam_member.compute_run_admin,
    google_project_iam_member.compute_service_account_user,
    google_project_iam_member.compute_storage_viewer,
    google_project_iam_member.compute_artifactregistry_writer,
    google_storage_bucket_iam_member.compute_object_admin,
    # Compute SA Eventarc permission (required for Eventarc trigger creation/validation)
    google_project_iam_member.compute_eventarc_eventreceiver,
    # Eventarc and Firestore dependencies
    google_project_service.apis["eventarc.googleapis.com"],
    google_firestore_database.default,
  ]
}

# Grant Eventarc SA permission to invoke
resource "google_cloud_run_service_iam_member" "eventarc_invoke_report_skiptrace" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.report_generator_skiptrace.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.eventarc_sa}"
}

# -----------------------------------------------------------------------------
# Report Generator - Origination
# -----------------------------------------------------------------------------

resource "google_cloudfunctions2_function" "report_generator_origination" {
  project  = var.project_id
  name     = "report-generator-origination"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "on_job_updated"
    source {
      storage_source {
        bucket = google_storage_bucket.function_source.name
        object = google_storage_bucket_object.report_generator_origination.name
      }
    }
  }

  service_config {
    max_instance_count    = var.function_max_instances["report_generator_origination"]
    min_instance_count    = 0
    available_memory      = var.function_memory["report_generator_origination"]
    timeout_seconds       = var.function_timeout["report_generator_origination"]
    service_account_email = google_service_account.functions.email

    environment_variables = {
      GCP_PROJECT  = var.project_id
      GCP_LOCATION = "global"  # Use global endpoint for Gemini models
    }
  }

  # Eventarc trigger for Firestore document updates
  event_trigger {
    trigger_region = var.region
    event_type     = "google.cloud.firestore.document.v1.updated"
    retry_policy   = "RETRY_POLICY_RETRY"

    event_filters {
      attribute = "database"
      value     = "(default)"
    }
    event_filters {
      attribute = "document"
      value     = "jobs/{jobId}"
      operator  = "match-path-pattern"
    }
  }

  labels = local.common_labels

  # Cloud Build dependencies + Eventarc dependencies
  # Includes both Cloud Build SA and Compute SA permissions (required as of 2024 default change)
  # Note: eventarc_eventreceiver binding is not in depends_on because Eventarc SA is Google-managed
  # and may not exist immediately after API enablement. Functions can be created independently;
  # the IAM binding will succeed on second apply if it failed on first.
  depends_on = [
    google_project_service.apis["cloudfunctions.googleapis.com"],
    google_project_service.apis["run.googleapis.com"],
    google_project_service.apis["cloudbuild.googleapis.com"],
    time_sleep.api_propagation,
    # Cloud Build SA permissions (for explicit Cloud Build triggers)
    google_project_iam_member.cloudbuild_functions_developer,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_service_account_user,
    google_storage_bucket_iam_member.cloudbuild_object_admin,
    # Compute SA permissions (for Functions Gen2 internal builds - default as of 2024)
    google_project_iam_member.compute_functions_developer,
    google_project_iam_member.compute_run_admin,
    google_project_iam_member.compute_service_account_user,
    google_project_iam_member.compute_storage_viewer,
    google_project_iam_member.compute_artifactregistry_writer,
    google_storage_bucket_iam_member.compute_object_admin,
    # Compute SA Eventarc permission (required for Eventarc trigger creation/validation)
    google_project_iam_member.compute_eventarc_eventreceiver,
    # Eventarc and Firestore dependencies
    google_project_service.apis["eventarc.googleapis.com"],
    google_firestore_database.default,
  ]
}

resource "google_cloud_run_service_iam_member" "eventarc_invoke_report_origination" {
  project  = var.project_id
  location = var.region
  service  = google_cloudfunctions2_function.report_generator_origination.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.eventarc_sa}"
}
