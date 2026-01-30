# =============================================================================
# Cloud Workflows
# =============================================================================
# Deploys Cloud Workflows for skip trace and origination investigations.
# Workflow YAMLs are templates that get function URLs injected via templatefile().
# =============================================================================

# -----------------------------------------------------------------------------
# Skip Trace Workflow
# -----------------------------------------------------------------------------

resource "google_workflows_workflow" "skiptrace" {
  project         = var.project_id
  name            = var.skiptrace_workflow_name
  region          = var.region
  description     = "Skip trace investigation workflow - orchestrates identity resolution, domain enrichment, and address geocoding"
  service_account = google_service_account.workflow.id

  # Inject function URLs into workflow template
  source_contents = templatefile("${path.module}/../../../gcp/workflows/investigate-skiptrace.yaml.tpl", {
    project_id                = var.project_id
    company_domain_lookup_url = google_cloudfunctions2_function.company_domain_lookup.service_config[0].uri
    phase1_identity_url       = google_cloudfunctions2_function.phase1_identity.service_config[0].uri
    domain_enrichment_url     = google_cloudfunctions2_function.domain_enrichment.service_config[0].uri
    address_geocoding_url     = google_cloudfunctions2_function.address_geocoding.service_config[0].uri
    contact_extraction_url    = google_cloudfunctions2_function.contact_extraction.service_config[0].uri
    aggregator_url            = google_cloudfunctions2_function.aggregator.service_config[0].uri
  })

  labels = local.common_labels

  depends_on = [
    google_project_service.apis["workflows.googleapis.com"],
    time_sleep.api_propagation,
    google_cloudfunctions2_function.company_domain_lookup,
    google_cloudfunctions2_function.phase1_identity,
    google_cloudfunctions2_function.domain_enrichment,
    google_cloudfunctions2_function.address_geocoding,
    google_cloudfunctions2_function.contact_extraction,
    google_cloudfunctions2_function.aggregator,
    # Ensure workflow SA has invoker permissions before workflow is created
    google_cloud_run_service_iam_member.workflow_invoke_phase1_identity,
    google_cloud_run_service_iam_member.workflow_invoke_domain_enrichment,
    google_cloud_run_service_iam_member.workflow_invoke_address_geocoding,
    google_cloud_run_service_iam_member.workflow_invoke_company_domain_lookup,
    google_cloud_run_service_iam_member.workflow_invoke_contact_extraction,
    google_cloud_run_service_iam_member.workflow_invoke_aggregator,
  ]
}

# -----------------------------------------------------------------------------
# Origination Workflow
# -----------------------------------------------------------------------------

resource "google_workflows_workflow" "origination" {
  project         = var.project_id
  name            = var.origination_workflow_name
  region          = var.region
  description     = "Loan origination investigation workflow - orchestrates identity resolution, domain enrichment, address geocoding, and aggregation"
  service_account = google_service_account.workflow.id

  # Inject function URLs into workflow template
  source_contents = templatefile("${path.module}/../../../gcp/workflows/investigate-origination.yaml.tpl", {
    project_id                = var.project_id
    company_domain_lookup_url = google_cloudfunctions2_function.company_domain_lookup.service_config[0].uri
    phase1_identity_url       = google_cloudfunctions2_function.phase1_identity.service_config[0].uri
    domain_enrichment_url     = google_cloudfunctions2_function.domain_enrichment.service_config[0].uri
    address_geocoding_url     = google_cloudfunctions2_function.address_geocoding.service_config[0].uri
    contact_extraction_url    = google_cloudfunctions2_function.contact_extraction.service_config[0].uri
    aggregator_url            = google_cloudfunctions2_function.aggregator.service_config[0].uri
  })

  labels = local.common_labels

  depends_on = [
    google_project_service.apis["workflows.googleapis.com"],
    time_sleep.api_propagation,
    google_cloudfunctions2_function.company_domain_lookup,
    google_cloudfunctions2_function.phase1_identity,
    google_cloudfunctions2_function.domain_enrichment,
    google_cloudfunctions2_function.address_geocoding,
    google_cloudfunctions2_function.contact_extraction,
    google_cloudfunctions2_function.aggregator,
    # Ensure workflow SA has invoker permissions before workflow is created
    google_cloud_run_service_iam_member.workflow_invoke_phase1_identity,
    google_cloud_run_service_iam_member.workflow_invoke_domain_enrichment,
    google_cloud_run_service_iam_member.workflow_invoke_address_geocoding,
    google_cloud_run_service_iam_member.workflow_invoke_company_domain_lookup,
    google_cloud_run_service_iam_member.workflow_invoke_contact_extraction,
    google_cloud_run_service_iam_member.workflow_invoke_aggregator,
  ]
}
