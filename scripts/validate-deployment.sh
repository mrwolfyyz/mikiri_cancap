#!/bin/bash
# =============================================================================
# Validate Deployment Script
# =============================================================================
# Validates that all resources have been deployed correctly.
# Run this script after terraform apply to verify the deployment.
# =============================================================================

set -e

# Configuration
PROJECT_ID="${1:-$GCP_PROJECT}"
REGION="${2:-northamerica-northeast1}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "Usage: $0 <project_id> [region]"
  echo ""
  echo "Environment variable GCP_PROJECT can also be used."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=========================================="
echo "Validating deployment"
echo "Project: $PROJECT_ID"
echo "Region:  $REGION"
echo "=========================================="
echo ""

ALL_OK=true

# -----------------------------------------------------------------------------
# Check GCP APIs
# -----------------------------------------------------------------------------
echo "Checking enabled APIs..."

REQUIRED_APIS=(
  "cloudfunctions.googleapis.com"
  "run.googleapis.com"
  "workflows.googleapis.com"
  "firestore.googleapis.com"
  "secretmanager.googleapis.com"
  "aiplatform.googleapis.com"
  "firebase.googleapis.com"
  "identitytoolkit.googleapis.com"
)

for api in "${REQUIRED_APIS[@]}"; do
  if gcloud services list --project="$PROJECT_ID" --filter="config.name:$api" --format="value(config.name)" 2>/dev/null | grep -q "$api"; then
    echo "  ✓ $api"
  else
    echo "  ❌ $api - NOT ENABLED"
    ALL_OK=false
  fi
done

echo ""

# -----------------------------------------------------------------------------
# Check Cloud Functions
# -----------------------------------------------------------------------------
echo "Checking Cloud Functions (Gen2)..."

FUNCTIONS=(
  "api-gateway"
  "phase1-identity"
  "domain-enrichment"
  "address-geocoding"
  "company-domain-lookup"
  "aggregator"
  "report-generator-skiptrace"
  "report-generator-origination"
  "chat-handler"
  "chat-handler-origination"
  "address-verification"
)

for func in "${FUNCTIONS[@]}"; do
  if gcloud functions describe "$func" --project="$PROJECT_ID" --region="$REGION" --gen2 &>/dev/null; then
    URL=$(gcloud functions describe "$func" --project="$PROJECT_ID" --region="$REGION" --gen2 --format="value(serviceConfig.uri)" 2>/dev/null)
    STATE=$(gcloud functions describe "$func" --project="$PROJECT_ID" --region="$REGION" --gen2 --format="value(state)" 2>/dev/null)
    if [[ "$STATE" == "ACTIVE" ]]; then
      echo "  ✓ $func (ACTIVE)"
    else
      echo "  ⚠️  $func (STATE: $STATE)"
    fi
  else
    echo "  ❌ $func - NOT FOUND"
    ALL_OK=false
  fi
done

echo ""

# -----------------------------------------------------------------------------
# Check Workflows
# -----------------------------------------------------------------------------
echo "Checking Workflows..."

WORKFLOWS=(
  "investigate-skiptrace"
  "investigate-origination"
)

for workflow in "${WORKFLOWS[@]}"; do
  if gcloud workflows describe "$workflow" --project="$PROJECT_ID" --location="$REGION" &>/dev/null; then
    STATE=$(gcloud workflows describe "$workflow" --project="$PROJECT_ID" --location="$REGION" --format="value(state)" 2>/dev/null)
    echo "  ✓ $workflow ($STATE)"
  else
    echo "  ❌ $workflow - NOT FOUND"
    ALL_OK=false
  fi
done

echo ""

# -----------------------------------------------------------------------------
# Check Firestore
# -----------------------------------------------------------------------------
echo "Checking Firestore..."

if gcloud firestore databases describe --project="$PROJECT_ID" &>/dev/null; then
  DB_TYPE=$(gcloud firestore databases describe --project="$PROJECT_ID" --format="value(type)" 2>/dev/null)
  echo "  ✓ Firestore database exists ($DB_TYPE)"
else
  echo "  ❌ Firestore database - NOT FOUND"
  ALL_OK=false
fi

echo ""

# -----------------------------------------------------------------------------
# Check Service Accounts
# -----------------------------------------------------------------------------
echo "Checking Service Accounts..."

SERVICE_ACCOUNTS=(
  "workflow-sa@$PROJECT_ID.iam.gserviceaccount.com"
  "functions-sa@$PROJECT_ID.iam.gserviceaccount.com"
)

for sa in "${SERVICE_ACCOUNTS[@]}"; do
  if gcloud iam service-accounts describe "$sa" --project="$PROJECT_ID" &>/dev/null; then
    echo "  ✓ $sa"
  else
    echo "  ❌ $sa - NOT FOUND"
    ALL_OK=false
  fi
done

echo ""

# -----------------------------------------------------------------------------
# Check Secrets
# -----------------------------------------------------------------------------
echo "Checking Secrets..."

SECRETS=(
  "GOOGLE_SEARCH_API_KEY"
  "GOOGLE_SEARCH_CX"
  "PRECISION_PSE_CX"
  "RECALL_PSE_CX"
  "HIBP_API_KEY"
)

for secret in "${SECRETS[@]}"; do
  if gcloud secrets describe "$secret" --project="$PROJECT_ID" &>/dev/null; then
    # Check if secret has a non-placeholder value
    VERSION_COUNT=$(gcloud secrets versions list "$secret" --project="$PROJECT_ID" --format="value(name)" 2>/dev/null | wc -l)
    if [[ "$VERSION_COUNT" -gt 0 ]]; then
      echo "  ✓ $secret (${VERSION_COUNT} version(s))"
    else
      echo "  ⚠️  $secret (no versions)"
    fi
  else
    echo "  ❌ $secret - NOT FOUND"
    ALL_OK=false
  fi
done

echo ""

# -----------------------------------------------------------------------------
# Check Firebase Config Files
# -----------------------------------------------------------------------------
echo "Checking Firebase config files..."

if [[ -f "$ROOT_DIR/frontend/skiptrace/public/firebase-config.json" ]]; then
  echo "  ✓ frontend/skiptrace/public/firebase-config.json"
else
  echo "  ❌ frontend/skiptrace/public/firebase-config.json - NOT FOUND"
  ALL_OK=false
fi

if [[ -f "$ROOT_DIR/frontend/origination/public/firebase-config.json" ]]; then
  echo "  ✓ frontend/origination/public/firebase-config.json"
else
  echo "  ❌ frontend/origination/public/firebase-config.json - NOT FOUND"
  ALL_OK=false
fi

echo ""

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo "=========================================="

if [[ "$ALL_OK" == "true" ]]; then
  echo "✓ All validations passed!"
  echo ""
  echo "Next steps:"
  echo "  1. Set secret values if not already done:"
  echo "     gcloud secrets versions add SECRET_NAME --data-file=- --project=$PROJECT_ID"
  echo ""
  echo "  2. Deploy Firestore rules:"
  echo "     cd frontend/skiptrace && firebase deploy --only firestore:rules --project=$PROJECT_ID"
  echo ""
  echo "  3. Deploy frontend hosting:"
  echo "     cd frontend/skiptrace && firebase deploy --only hosting --project=$PROJECT_ID"
  echo ""
  echo "  4. Run smoke tests:"
  echo "     ./scripts/smoke-test.sh $PROJECT_ID"
  exit 0
else
  echo "❌ Some validations failed. Please check the errors above."
  exit 1
fi
