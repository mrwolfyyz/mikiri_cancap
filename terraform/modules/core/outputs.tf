# =============================================================================
# Core Module Outputs
# =============================================================================
# Outputs function URLs, Firebase configuration, and other deployment values
# for use by environment configurations and deployment scripts.
# =============================================================================

# -----------------------------------------------------------------------------
# Function URLs
# -----------------------------------------------------------------------------

output "api_gateway_url" {
  description = "URL of the API Gateway function"
  value       = google_cloudfunctions2_function.api_gateway.service_config[0].uri
}

output "phase1_identity_url" {
  description = "URL of the Phase 1 Identity function"
  value       = google_cloudfunctions2_function.phase1_identity.service_config[0].uri
}

output "domain_enrichment_url" {
  description = "URL of the Domain Enrichment function"
  value       = google_cloudfunctions2_function.domain_enrichment.service_config[0].uri
}

output "address_geocoding_url" {
  description = "URL of the Address Geocoding function"
  value       = google_cloudfunctions2_function.address_geocoding.service_config[0].uri
}

output "company_domain_lookup_url" {
  description = "URL of the Company Domain Lookup function"
  value       = google_cloudfunctions2_function.company_domain_lookup.service_config[0].uri
}

output "aggregator_url" {
  description = "URL of the Aggregator function"
  value       = google_cloudfunctions2_function.aggregator.service_config[0].uri
}

output "report_generator_skiptrace_url" {
  description = "URL of the Skip Trace Report Generator function"
  value       = google_cloudfunctions2_function.report_generator_skiptrace.service_config[0].uri
}

output "report_generator_origination_url" {
  description = "URL of the Origination Report Generator function"
  value       = google_cloudfunctions2_function.report_generator_origination.service_config[0].uri
}

output "chat_handler_url" {
  description = "URL of the Chat Handler function"
  value       = google_cloudfunctions2_function.chat_handler.service_config[0].uri
}

output "chat_handler_origination_url" {
  description = "URL of the Chat Handler Origination function"
  value       = google_cloudfunctions2_function.chat_handler_origination.service_config[0].uri
}

output "address_verification_url" {
  description = "URL of the Address Verification function"
  value       = google_cloudfunctions2_function.address_verification.service_config[0].uri
}

# All function URLs as a map
output "function_urls" {
  description = "Map of all function names to their URLs"
  value = {
    api_gateway                  = google_cloudfunctions2_function.api_gateway.service_config[0].uri
    phase1_identity              = google_cloudfunctions2_function.phase1_identity.service_config[0].uri
    domain_enrichment            = google_cloudfunctions2_function.domain_enrichment.service_config[0].uri
    address_geocoding            = google_cloudfunctions2_function.address_geocoding.service_config[0].uri
    company_domain_lookup        = google_cloudfunctions2_function.company_domain_lookup.service_config[0].uri
    aggregator                   = google_cloudfunctions2_function.aggregator.service_config[0].uri
    report_generator_skiptrace   = google_cloudfunctions2_function.report_generator_skiptrace.service_config[0].uri
    report_generator_origination = google_cloudfunctions2_function.report_generator_origination.service_config[0].uri
    chat_handler                 = google_cloudfunctions2_function.chat_handler.service_config[0].uri
    chat_handler_origination     = google_cloudfunctions2_function.chat_handler_origination.service_config[0].uri
    address_verification         = google_cloudfunctions2_function.address_verification.service_config[0].uri
  }
}

# -----------------------------------------------------------------------------
# Workflow Information
# -----------------------------------------------------------------------------

output "skiptrace_workflow_name" {
  description = "Name of the skip trace workflow"
  value       = google_workflows_workflow.skiptrace.name
}

output "origination_workflow_name" {
  description = "Name of the origination workflow"
  value       = google_workflows_workflow.origination.name
}

# -----------------------------------------------------------------------------
# Firebase Configuration
# -----------------------------------------------------------------------------

output "firebase_config_skiptrace" {
  description = "Firebase configuration for skip trace frontend"
  value = {
    apiKey            = data.google_firebase_web_app_config.skiptrace.api_key
    authDomain        = "${var.project_id}.firebaseapp.com"
    projectId         = var.project_id
    storageBucket     = "${var.project_id}.firebasestorage.app"
    messagingSenderId = data.google_firebase_web_app_config.skiptrace.messaging_sender_id
    appId             = google_firebase_web_app.skiptrace.app_id
    apiUrl            = google_cloudfunctions2_function.api_gateway.service_config[0].uri
  }
  sensitive = true
}

output "firebase_config_origination" {
  description = "Firebase configuration for origination frontend"
  value = {
    apiKey            = data.google_firebase_web_app_config.origination.api_key
    authDomain        = "${var.project_id}.firebaseapp.com"
    projectId         = var.project_id
    storageBucket     = "${var.project_id}.firebasestorage.app"
    messagingSenderId = data.google_firebase_web_app_config.origination.messaging_sender_id
    appId             = google_firebase_web_app.origination.app_id
    apiUrl            = google_cloudfunctions2_function.api_gateway.service_config[0].uri
  }
  sensitive = true
}

# -----------------------------------------------------------------------------
# Firebase Hosting
# -----------------------------------------------------------------------------

output "skiptrace_hosting_site_id" {
  description = "Firebase Hosting site ID for skip trace"
  value       = google_firebase_hosting_site.skiptrace.site_id
}

output "origination_hosting_site_id" {
  description = "Firebase Hosting site ID for origination"
  value       = google_firebase_hosting_site.origination.site_id
}

output "skiptrace_hosting_url" {
  description = "URL for skip trace frontend hosting"
  value       = "https://${google_firebase_hosting_site.skiptrace.site_id}.web.app"
}

output "origination_hosting_url" {
  description = "URL for origination frontend hosting"
  value       = "https://${google_firebase_hosting_site.origination.site_id}.web.app"
}

# -----------------------------------------------------------------------------
# Service Accounts
# -----------------------------------------------------------------------------

output "workflow_service_account_email" {
  description = "Email of the workflow service account"
  value       = google_service_account.workflow.email
}

output "functions_service_account_email" {
  description = "Email of the functions service account"
  value       = google_service_account.functions.email
}

# -----------------------------------------------------------------------------
# Secret Names
# -----------------------------------------------------------------------------

output "secret_names" {
  description = "List of created secret names"
  value       = [for s in google_secret_manager_secret.secrets : s.secret_id]
}

# -----------------------------------------------------------------------------
# Project Information
# -----------------------------------------------------------------------------

output "project_id" {
  description = "GCP project ID"
  value       = var.project_id
}

output "region" {
  description = "GCP region"
  value       = var.region
}

output "project_number" {
  description = "GCP project number"
  value       = local.project_number
}
