# =============================================================================
# Production Environment Configuration
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

# -----------------------------------------------------------------------------
# Provider Configuration
# -----------------------------------------------------------------------------

provider "google" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}

# -----------------------------------------------------------------------------
# Variables
# -----------------------------------------------------------------------------

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "northamerica-northeast1"
}

variable "cors_allowed_origins" {
  description = "CORS allowed origins (restrict to specific domains in production)"
  type        = string
  # Example: "https://your-project-skiptrace.web.app,https://your-project-origination.web.app"
}

variable "enable_sso" {
  description = "Enable Google Workspace SSO."
  type        = bool
  default     = true

  validation {
    condition     = var.enable_sso
    error_message = "Production requires enable_sso=true."
  }
}

variable "allowed_email_domains" {
  description = "Allowed email domains for SSO users."
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.allowed_email_domains) > 0
    error_message = "Production requires one or more allowed_email_domains."
  }
}

variable "workspace_domain" {
  description = "Workspace domain hint for Google sign-in popup."
  type        = string
  default     = ""
}

variable "google_workspace_oauth_client_id" {
  description = "OAuth client ID for Firebase Google provider."
  type        = string
  default     = ""

  validation {
    condition     = trimspace(var.google_workspace_oauth_client_id) != ""
    error_message = "google_workspace_oauth_client_id is required in production."
  }
}

variable "google_workspace_oauth_client_secret_id" {
  description = "Secret Manager secret ID storing OAuth client secret."
  type        = string
  default     = ""

  validation {
    condition     = trimspace(var.google_workspace_oauth_client_secret_id) != ""
    error_message = "google_workspace_oauth_client_secret_id is required in production."
  }
}

variable "app_check_enforced" {
  description = "Whether to enforce Firebase App Check tokens."
  type        = bool
  default     = true
}

variable "enable_iap" {
  description = "Phase 2 toggle for IAP-protected ingress."
  type        = bool
  default     = false
}

variable "frontend_results_base_url" {
  description = "Base URL for the skiptrace frontend. Prepended to Results URL in CSV exports."
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Core Module
# -----------------------------------------------------------------------------

module "core" {
  source = "../../modules/core"

  project_id                              = var.project_id
  region                                  = var.region
  environment                             = "prod"
  cors_allowed_origins                    = var.cors_allowed_origins
  enable_sso                              = var.enable_sso
  allowed_email_domains                   = var.allowed_email_domains
  workspace_domain                        = var.workspace_domain
  google_workspace_oauth_client_id        = var.google_workspace_oauth_client_id
  google_workspace_oauth_client_secret_id = var.google_workspace_oauth_client_secret_id
  app_check_enforced                      = var.app_check_enforced
  enable_iap                              = var.enable_iap
  frontend_results_base_url               = var.frontend_results_base_url
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "api_gateway_url" {
  description = "URL of the API Gateway"
  value       = module.core.api_gateway_url
}

output "function_urls" {
  description = "Map of all function URLs"
  value       = module.core.function_urls
  sensitive   = true
}

output "skiptrace_hosting_url" {
  description = "URL for skip trace frontend"
  value       = module.core.skiptrace_hosting_url
}

output "origination_hosting_url" {
  description = "URL for origination frontend"
  value       = module.core.origination_hosting_url
}
