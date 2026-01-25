# =============================================================================
# Vertex AI Search Module Outputs
# =============================================================================

output "linkedin_data_store_id" {
  description = "LinkedIn Data Store ID"
  value       = google_discovery_engine_data_store.linkedin.data_store_id
}

output "linkedin_engine_id" {
  description = "LinkedIn Search Engine ID"
  value       = google_discovery_engine_search_engine.linkedin.engine_id
}

output "precision_data_store_id" {
  description = "Precision Data Store ID"
  value       = google_discovery_engine_data_store.precision.data_store_id
}

output "precision_engine_id" {
  description = "Precision Search Engine ID"
  value       = google_discovery_engine_search_engine.precision.engine_id
}
