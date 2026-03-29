# =============================================================================
# Core Module - Main Configuration
# =============================================================================
# This module creates all core infrastructure for the skip trace & origination
# platform including APIs, Firestore, secrets, service accounts, functions,
# workflows, and Firebase configuration.
# =============================================================================

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

data "google_project" "project" {
  project_id = var.project_id
}

# -----------------------------------------------------------------------------
# Local Variables
# -----------------------------------------------------------------------------

locals {
  project_number = data.google_project.project.number

  # Eventarc service account (Google-managed, created when Eventarc API is enabled)
  eventarc_sa = "service-${local.project_number}@gcp-sa-eventarc.iam.gserviceaccount.com"

  # Cloud Build service account (Google-managed)
  cloudbuild_sa = "${local.project_number}@cloudbuild.gserviceaccount.com"

  # Default compute service account
  compute_sa = "${local.project_number}-compute@developer.gserviceaccount.com"

  # Functions that require retry_utils.py to be present
  functions_needing_retry_utils = toset([
    "api_gateway",
    "phase1_identity",
    "domain_enrichment",
    "address_geocoding",
    "company_domain_lookup",
    "report_generator_skiptrace",
    "report_generator_origination",
    "chat_handler",
    "address_verification"
  ])

  # Functions that DO NOT need retry_utils.py
  functions_without_retry_utils = toset([
    "aggregator",
    "chat_handler_origination"
  ])

  # All function names
  all_functions = setunion(local.functions_needing_retry_utils, local.functions_without_retry_utils)

  # Functions invoked by workflows (need OIDC auth, workflow SA needs invoker role)
  workflow_invoked_functions = toset([
    "phase1_identity",
    "domain_enrichment",
    "address_geocoding",
    "company_domain_lookup",
    "aggregator"
  ])

  # Functions invoked by Eventarc triggers (report generators)
  eventarc_triggered_functions = toset([
    "report_generator_skiptrace",
    "report_generator_origination"
  ])

  # Cloud Run invoker open to the internet (browser calls Firebase-backed gateway only)
  # Chat and address backends are invoked by api_gateway using a Google ID token
  public_functions = toset([
    "api_gateway"
  ])

  # Functions that need Vertex AI access
  functions_needing_vertex_ai = toset([
    "phase1_identity",
    "address_geocoding",
    "report_generator_skiptrace",
    "report_generator_origination",
    "chat_handler",
    "chat_handler_origination",
    "address_verification"
  ])

  # Functions that need Secret Manager access
  functions_needing_secrets = toset([
    "phase1_identity",
    "company_domain_lookup",
    "address_verification"
  ])

  # Common labels for all resources
  common_labels = {
    environment = var.environment
    managed_by  = "terraform"
    project     = "skip-trace-origination"
  }
}

# -----------------------------------------------------------------------------
# Provider Configuration (inherited from root)
# -----------------------------------------------------------------------------

terraform {
  required_providers {
    google = {
      source = "hashicorp/google"
    }
    google-beta = {
      source = "hashicorp/google-beta"
    }
    null = {
      source = "hashicorp/null"
    }
    local = {
      source = "hashicorp/local"
    }
    archive = {
      source = "hashicorp/archive"
    }
    time = {
      source = "hashicorp/time"
    }
  }
}
