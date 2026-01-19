#!/bin/bash
# =============================================================================
# Get Function URLs Script
# =============================================================================
# Helper script to retrieve all function URLs from a deployment.
# Useful for verification and documentation.
# =============================================================================

PROJECT_ID="${1:-$GCP_PROJECT}"
REGION="${2:-northamerica-northeast1}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "Usage: $0 <project_id> [region]"
  exit 1
fi

echo "=========================================="
echo "Function URLs for $PROJECT_ID"
echo "=========================================="
echo ""

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
  URL=$(gcloud functions describe "$func" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --gen2 \
    --format="value(serviceConfig.uri)" 2>/dev/null)
  
  if [[ -n "$URL" ]]; then
    printf "%-35s %s\n" "$func:" "$URL"
  else
    printf "%-35s %s\n" "$func:" "(not found)"
  fi
done
