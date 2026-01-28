# =============================================================================
# Core Module Variables
# =============================================================================

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for resources"
  type        = string
  default     = "northamerica-northeast1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be one of: dev, staging, prod."
  }
}

variable "cors_allowed_origins" {
  description = "CORS allowed origins for API Gateway (use '*' for dev, specific domains for prod)"
  type        = string
  default     = "*"
}

# -----------------------------------------------------------------------------
# Function Configuration
# -----------------------------------------------------------------------------

variable "function_memory" {
  description = "Memory allocation for each function"
  type        = map(string)
  default = {
    api_gateway                  = "256Mi"
    phase1_identity              = "512Mi"
    domain_enrichment            = "256Mi"
    address_geocoding            = "512Mi"
    company_domain_lookup        = "512Mi"
    aggregator                   = "256Mi"
    report_generator_skiptrace   = "2Gi"
    report_generator_origination = "2Gi"
    chat_handler                 = "512Mi"
    chat_handler_origination     = "512Mi"
    address_verification         = "512Mi"
  }
}

variable "function_timeout" {
  description = "Timeout in seconds for each function"
  type        = map(number)
  default = {
    api_gateway                  = 300
    phase1_identity              = 120
    domain_enrichment            = 60
    address_geocoding            = 600
    company_domain_lookup        = 60
    aggregator                   = 30
    report_generator_skiptrace   = 540
    report_generator_origination = 540
    chat_handler                 = 120
    chat_handler_origination     = 120
    address_verification         = 120
  }
}

variable "function_max_instances" {
  description = "Maximum instances for each function (0 = unlimited)"
  type        = map(number)
  default = {
    api_gateway                  = 10
    phase1_identity              = 5
    domain_enrichment            = 5
    address_geocoding            = 5
    company_domain_lookup        = 5
    aggregator                   = 5
    report_generator_skiptrace   = 3
    report_generator_origination = 3
    chat_handler                 = 5
    chat_handler_origination     = 5
    address_verification         = 5
  }
}

# -----------------------------------------------------------------------------
# Secret Names
# -----------------------------------------------------------------------------

variable "secret_names" {
  description = "List of secret names to create in Secret Manager"
  type        = list(string)
  default = [
    "REVIEWS_PSE_CX",
    "COMPLAINTS_PSE_CX",
    "HIBP_API_KEY",
  ]
}

# -----------------------------------------------------------------------------
# Workflow Names
# -----------------------------------------------------------------------------

variable "skiptrace_workflow_name" {
  description = "Name for the skip trace workflow"
  type        = string
  default     = "investigate-skiptrace"
}

variable "origination_workflow_name" {
  description = "Name for the origination workflow"
  type        = string
  default     = "investigate-origination"
}
