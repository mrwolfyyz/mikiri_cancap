#!/bin/bash
# =============================================================================
# Prepare Functions Script
# =============================================================================
# Prepares function source code for deployment by copying shared dependencies.
# Run this script before terraform apply to ensure all functions have required files.
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=========================================="
echo "Preparing function source files"
echo "=========================================="
echo ""

# Functions that require retry_utils.py
FUNCTIONS_NEEDING_RETRY=(
  "api_gateway"
  "phase1_identity"
  "domain_enrichment"
  "address_geocoding"
  "company_domain_lookup"
  "query_constructor"
  "report_generator_skiptrace"
  "report_generator_origination"
  "chat_handler"
  "address_verification"
  "contact_extraction"
)

# Source file location
RETRY_UTILS_SOURCE="$ROOT_DIR/gcp/shared/retry_utils.py"

# Check if source file exists
if [[ ! -f "$RETRY_UTILS_SOURCE" ]]; then
  echo "❌ ERROR: Source file not found: $RETRY_UTILS_SOURCE"
  exit 1
fi

echo "Source: $RETRY_UTILS_SOURCE"
echo ""

# Copy retry_utils.py to each function that needs it
echo "Copying retry_utils.py to functions..."
for func in "${FUNCTIONS_NEEDING_RETRY[@]}"; do
  DEST_DIR="$ROOT_DIR/gcp/functions/$func"
  DEST_FILE="$DEST_DIR/retry_utils.py"
  
  if [[ ! -d "$DEST_DIR" ]]; then
    echo "  ⚠️  Directory not found: $DEST_DIR"
    continue
  fi
  
  cp "$RETRY_UTILS_SOURCE" "$DEST_FILE"
  echo "  ✓ $func/retry_utils.py"
done

echo ""
echo "=========================================="
echo "Verification"
echo "=========================================="
echo ""

# Verify all functions have required files
echo "Verifying retry_utils.py presence..."
ALL_OK=true
for func in "${FUNCTIONS_NEEDING_RETRY[@]}"; do
  DEST_FILE="$ROOT_DIR/gcp/functions/$func/retry_utils.py"
  if [[ -f "$DEST_FILE" ]]; then
    echo "  ✓ $func/retry_utils.py"
  else
    echo "  ❌ $func/retry_utils.py - MISSING!"
    ALL_OK=false
  fi
done

echo ""

# Verify blocklist files for report generators
echo "Verifying blocklist files..."
for func in report_generator_skiptrace report_generator_origination; do
  BLOCKLIST_FILE="$ROOT_DIR/gcp/functions/$func/disposable_email_blocklist.conf"
  if [[ -f "$BLOCKLIST_FILE" ]]; then
    echo "  ✓ $func/disposable_email_blocklist.conf"
  else
    echo "  ❌ $func/disposable_email_blocklist.conf - MISSING!"
    ALL_OK=false
  fi
done

echo ""

# Verify main.py exists for all functions
echo "Verifying main.py presence..."
ALL_FUNCTIONS=(
  "api_gateway"
  "phase1_identity"
  "domain_enrichment"
  "address_geocoding"
  "company_domain_lookup"
  "query_constructor"
  "aggregator"
  "report_generator_skiptrace"
  "report_generator_origination"
  "chat_handler"
  "chat_handler_origination"
  "address_verification"
  "contact_extraction"
)

for func in "${ALL_FUNCTIONS[@]}"; do
  MAIN_FILE="$ROOT_DIR/gcp/functions/$func/main.py"
  if [[ -f "$MAIN_FILE" ]]; then
    echo "  ✓ $func/main.py"
  else
    echo "  ❌ $func/main.py - MISSING!"
    ALL_OK=false
  fi
done

echo ""
echo "=========================================="

if [[ "$ALL_OK" == "true" ]]; then
  echo "✓ All preparations complete!"
  exit 0
else
  echo "❌ Some files are missing. Please check the errors above."
  exit 1
fi
