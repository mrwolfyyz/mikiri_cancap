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
  }
}

# -----------------------------------------------------------------------------
# Provider Configuration
# -----------------------------------------------------------------------------

provider "google" {
  project              = var.project_id
  region               = var.region
  billing_project      = var.project_id
  user_project_override = true
}

provider "google-beta" {
  project              = var.project_id
  region               = var.region
  billing_project      = var.project_id
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

# -----------------------------------------------------------------------------
# Core Module
# -----------------------------------------------------------------------------

module "core" {
  source = "../../modules/core"

  project_id           = var.project_id
  region               = var.region
  environment          = "prod"
  cors_allowed_origins = var.cors_allowed_origins
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
