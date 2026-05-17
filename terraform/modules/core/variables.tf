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

  validation {
    condition = (
      var.environment == "dev" ||
      trimspace(var.cors_allowed_origins) != "*"
    )
    error_message = "cors_allowed_origins cannot be '*' in staging or prod."
  }
}

# -----------------------------------------------------------------------------
# Authentication & App Check
# -----------------------------------------------------------------------------

variable "enable_sso" {
  description = "When true, Identity Platform is configured for Google Workspace SSO and anonymous auth is disabled."
  type        = bool
  default     = true

  validation {
    condition = (
      var.enable_sso ||
      !contains(["dev", "prod"], var.environment)
    )
    error_message = "enable_sso must be true for dev and prod environments."
  }
}

variable "allowed_email_domains" {
  description = "Email domains allowed to authenticate (lowercase, no '@', e.g. [\"cancap.ca\"])."
  type        = list(string)
  default     = []

  validation {
    condition = (
      !var.enable_sso ||
      length(var.allowed_email_domains) > 0
    )
    error_message = "allowed_email_domains must contain at least one domain when enable_sso is true."
  }

  validation {
    condition = alltrue([
      for d in var.allowed_email_domains :
      d == lower(d) && can(regex("^[a-z0-9.-]+\\.[a-z]{2,}$", d))
    ])
    error_message = "Each allowed_email_domains entry must be lowercase and look like a valid domain (example: cancap.ca)."
  }
}

variable "workspace_domain" {
  description = "Google Workspace domain used as the Firebase GoogleAuthProvider 'hd' hint (UX only)."
  type        = string
  default     = ""
}

variable "google_workspace_oauth_client_id" {
  description = "OAuth 2.0 client ID used by Firebase Auth Google provider."
  type        = string
  default     = ""

  validation {
    condition = (
      !var.enable_sso ||
      trimspace(var.google_workspace_oauth_client_id) != ""
    )
    error_message = "google_workspace_oauth_client_id is required when enable_sso is true."
  }
}

variable "google_workspace_oauth_client_secret_id" {
  description = "Secret Manager secret name containing the OAuth client secret used by Firebase Auth."
  type        = string
  default     = ""

  validation {
    condition = (
      !var.enable_sso ||
      trimspace(var.google_workspace_oauth_client_secret_id) != ""
    )
    error_message = "google_workspace_oauth_client_secret_id is required when enable_sso is true."
  }
}

variable "app_check_enforced" {
  description = "When true, API Gateway enforces valid Firebase App Check tokens."
  type        = bool
  default     = true

  validation {
    condition     = !var.app_check_enforced || var.enable_sso
    error_message = "app_check_enforced=true requires enable_sso=true. The reCAPTCHA Enterprise key and per-app App Check provider registrations that mint browser tokens are gated on enable_sso, so enabling enforcement without SSO silently breaks all browser clients (no valid App Check token can be produced, but API gateway and Firestore both require one)."
  }
}

variable "enable_iap" {
  description = "Phase 2 toggle: when true, remove public allUsers invoker binding from API Gateway."
  type        = bool
  default     = false
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
    contact_extraction           = "512Mi"
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
    phase1_identity              = 300
    domain_enrichment            = 60
    address_geocoding            = 600
    company_domain_lookup        = 240
    aggregator                   = 30
    contact_extraction           = 300
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
    contact_extraction           = 10
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

variable "frontend_results_base_url" {
  description = "Base URL for the skiptrace frontend (e.g. https://example.web.app). Prepended to Results URL in CSV exports. Empty = relative path fallback."
  type        = string
  default     = ""
}
