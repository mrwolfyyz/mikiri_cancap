# =============================================================================
# Terraform Backend Configuration - Production
# =============================================================================

terraform {
  backend "gcs" {
    bucket = "mikiri-demo-test-terraform-state"
    prefix = "prod"
  }
}
