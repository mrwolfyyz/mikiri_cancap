# =============================================================================
# Development Environment Configuration
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.0"
    }
  }
}

# -----------------------------------------------------------------------------
# Provider Configuration
# -----------------------------------------------------------------------------

provider "google" {
  project       = var.project_id
  region        = var.region
  billing_project = var.project_id
}

provider "google-beta" {
  project       = var.project_id
  region        = var.region
  billing_project = var.project_id
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

# -----------------------------------------------------------------------------
# Core Module
# -----------------------------------------------------------------------------

module "core" {
  source = "../../modules/core"

  project_id           = var.project_id
  region               = var.region
  environment          = "dev"
  cors_allowed_origins = "*" # Permissive for development
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
}

output "skiptrace_hosting_url" {
  description = "URL for skip trace frontend"
  value       = module.core.skiptrace_hosting_url
}

output "origination_hosting_url" {
  description = "URL for origination frontend"
  value       = module.core.origination_hosting_url
}

output "workflow_service_account" {
  description = "Workflow service account email"
  value       = module.core.workflow_service_account_email
}

output "project_number" {
  description = "GCP project number"
  value       = module.core.project_number
}
