#!/bin/bash
# Check and fix Terraform authentication before deployment

set -e

PROJECT_ID="${1:-mikiri-demo-test}"

echo "==================================="
echo "Checking Terraform Authentication"
echo "==================================="

# Check if project is set
CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")
if [ "$CURRENT_PROJECT" != "$PROJECT_ID" ]; then
  echo "❌ Project not set or incorrect"
  echo "   Current: $CURRENT_PROJECT"
  echo "   Expected: $PROJECT_ID"
  echo ""
  echo "Run: gcloud config set project $PROJECT_ID"
  exit 1
fi
echo "✓ Project set correctly: $PROJECT_ID"

# Check if user is logged in
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q "@"; then
  echo "❌ Not logged in"
  echo "Run: gcloud auth login"
  exit 1
fi
echo "✓ User authenticated"

# Check ADC
if [ ! -f "$HOME/.config/gcloud/application_default_credentials.json" ]; then
  echo "❌ Application Default Credentials not set"
  echo "Run: gcloud auth application-default login"
  exit 1
fi
echo "✓ Application Default Credentials exist"

# Check quota project in ADC
QUOTA_PROJECT=$(jq -r '.quota_project_id // empty' "$HOME/.config/gcloud/application_default_credentials.json" 2>/dev/null || echo "")
if [ "$QUOTA_PROJECT" != "$PROJECT_ID" ]; then
  echo "❌ Quota project not set or incorrect"
  echo "   Current: $QUOTA_PROJECT"
  echo "   Expected: $PROJECT_ID"
  echo ""
  echo "Run: gcloud auth application-default set-quota-project $PROJECT_ID"
  exit 1
fi
echo "✓ Quota project set correctly: $QUOTA_PROJECT"

# Test GCS access (terraform backend)
if ! gsutil ls -b gs://mikiri-demo-test-terraform-state >/dev/null 2>&1; then
  echo "❌ Cannot access Terraform state bucket"
  echo "   Bucket: mikiri-demo-test-terraform-state"
  echo ""
  echo "Check IAM permissions or re-authenticate"
  exit 1
fi
echo "✓ Terraform state bucket accessible"

echo ""
echo "==================================="
echo "✓ All authentication checks passed"
echo "==================================="
