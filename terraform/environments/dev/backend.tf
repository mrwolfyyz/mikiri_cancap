# =============================================================================
# Terraform Backend Configuration (Example)
# =============================================================================
# Copy this file to backend.tf and update the bucket name before running
# terraform init.
#
# IMPORTANT: Create the GCS bucket first:
#   gsutil mb -p YOUR_PROJECT_ID -l northamerica-northeast1 gs://YOUR_PROJECT_ID-terraform-state
#   gsutil versioning set on gs://YOUR_PROJECT_ID-terraform-state
# =============================================================================

terraform {
  backend "gcs" {
    bucket = "YOUR_PROJECT_ID-terraform-state"
    prefix = "dev"
    # GCS backend automatically handles state locking - no additional config needed
  }
}
