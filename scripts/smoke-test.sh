#!/bin/bash
# =============================================================================
# Smoke Test Script
# =============================================================================
# Performs basic functionality tests after deployment.
# Run this script after validate-deployment.sh passes.
# =============================================================================

set -e

# Configuration
PROJECT_ID="${1:-$GCP_PROJECT}"
REGION="${2:-northamerica-northeast1}"
TEST_ORIGIN="${SMOKE_TEST_ORIGIN:-https://${PROJECT_ID}-skiptrace.web.app}"
STRICT_CORS="${SMOKE_TEST_STRICT_CORS:-false}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "Usage: $0 <project_id> [region]"
  echo ""
  echo "Environment variable GCP_PROJECT can also be used."
  exit 1
fi

echo "=========================================="
echo "Running smoke tests"
echo "Project: $PROJECT_ID"
echo "Region:  $REGION"
echo "Origin:  $TEST_ORIGIN"
echo "=========================================="
echo ""

ALL_OK=true

# -----------------------------------------------------------------------------
# Get API Gateway URL
# -----------------------------------------------------------------------------
echo "Getting API Gateway URL..."

API_URL=$(gcloud functions describe api-gateway \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --gen2 \
  --format="value(serviceConfig.uri)" 2>/dev/null)

if [[ -z "$API_URL" ]]; then
  echo "❌ Could not get API Gateway URL"
  exit 1
fi

echo "  API Gateway: $API_URL"
echo ""

# -----------------------------------------------------------------------------
# Test: Health Endpoint
# -----------------------------------------------------------------------------
echo "Test 1: Health endpoint..."

HEALTH_RESPONSE=$(curl -s -w "\n%{http_code}" "$API_URL/health" 2>/dev/null)
HTTP_CODE=$(echo "$HEALTH_RESPONSE" | tail -n1)
BODY=$(echo "$HEALTH_RESPONSE" | sed '$d')

if [[ "$HTTP_CODE" == "200" ]]; then
  echo "  ✓ Health check passed (HTTP 200)"
  echo "  Response: $BODY"
else
  echo "  ❌ Health check failed (HTTP $HTTP_CODE)"
  echo "  Response: $BODY"
  ALL_OK=false
fi

echo ""

# -----------------------------------------------------------------------------
# Test: CORS Headers
# -----------------------------------------------------------------------------
echo "Test 2: CORS headers..."

CORS_RESPONSE=$(curl -s -I -X OPTIONS "$API_URL/health" \
  -H "Origin: $TEST_ORIGIN" \
  -H "Access-Control-Request-Method: GET" 2>/dev/null)

ALLOWED_ORIGIN=$(echo "$CORS_RESPONSE" | awk -F': ' 'tolower($1) == "access-control-allow-origin" {gsub("\r","",$2); print $2; exit}')

if [[ "$ALLOWED_ORIGIN" == "$TEST_ORIGIN" || "$ALLOWED_ORIGIN" == "*" ]]; then
  echo "  ✓ CORS headers present and origin allowed"
  echo "  access-control-allow-origin: $ALLOWED_ORIGIN"
else
  echo "  ⚠️  CORS headers may not be configured correctly"
  echo "  Expected origin: $TEST_ORIGIN"
  echo "  Returned origin: ${ALLOWED_ORIGIN:-<missing>}"
  if [[ "$STRICT_CORS" == "true" ]]; then
    ALL_OK=false
  fi
fi

echo ""

# -----------------------------------------------------------------------------
# Test: Authentication Required
# -----------------------------------------------------------------------------
echo "Test 3: Authentication requirement..."

AUTH_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$API_URL/investigate-skiptrace" \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","full_name":"Test User","city":"Toronto"}' 2>/dev/null)

HTTP_CODE=$(echo "$AUTH_RESPONSE" | tail -n1)
BODY=$(echo "$AUTH_RESPONSE" | sed '$d')

if [[ "$HTTP_CODE" == "401" ]]; then
  echo "  ✓ Authentication required (HTTP 401) - as expected"
else
  echo "  ⚠️  Unexpected response code: $HTTP_CODE"
  echo "  Response: $BODY"
fi

echo ""

# -----------------------------------------------------------------------------
# Test: Workflow Exists
# -----------------------------------------------------------------------------
echo "Test 4: Workflow accessibility..."

for workflow in "investigate-skiptrace" "investigate-origination"; do
  WORKFLOW_STATE=$(gcloud workflows describe "$workflow" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --format="value(state)" 2>/dev/null)
  
  if [[ "$WORKFLOW_STATE" == "ACTIVE" ]]; then
    echo "  ✓ $workflow is ACTIVE"
  else
    echo "  ❌ $workflow state: $WORKFLOW_STATE"
    ALL_OK=false
  fi
done

echo ""

# -----------------------------------------------------------------------------
# Test: Function URLs are Gen2 format
# -----------------------------------------------------------------------------
echo "Test 5: Function URL format (Gen2)..."

# Get a function URL and check it's Cloud Run format
FUNC_URL=$(gcloud functions describe chat-handler \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --gen2 \
  --format="value(serviceConfig.uri)" 2>/dev/null)

if [[ "$FUNC_URL" == *".run.app" ]]; then
  echo "  ✓ Functions use Gen2 (Cloud Run) URLs"
  echo "  Example: $FUNC_URL"
else
  echo "  ❌ Functions may not be Gen2 format"
  echo "  URL: $FUNC_URL"
  ALL_OK=false
fi

echo ""

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo "=========================================="

if [[ "$ALL_OK" == "true" ]]; then
  echo "✓ All smoke tests passed!"
  echo ""
  echo "The deployment appears to be working correctly."
  echo ""
  echo "To test full functionality:"
  echo "  1. Open the frontend URL in a browser"
  echo "  2. Sign in with Google SSO (allowed domain account)"
  echo "  3. Submit a test investigation"
  echo "  4. Verify the job completes successfully"
  exit 0
else
  echo "❌ Some smoke tests failed. Please check the errors above."
  exit 1
fi
