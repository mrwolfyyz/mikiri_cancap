# =============================================================================
# Vertex AI Search Module Variables
# =============================================================================

variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "api_propagation" {
  description = "Time sleep resource for API propagation (for dependency management)"
  type        = any
}
