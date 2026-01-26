# =============================================================================
# Vertex AI Search Module Integration
# =============================================================================
# This module provides Discovery Engine search capabilities for LinkedIn,
# precision, and recall searches as replacements for Google Custom Search API.
# =============================================================================

module "vertex_ai_search" {
  source = "../vertex-ai-search"

  project_id      = var.project_id
  api_propagation = time_sleep.api_propagation
}
