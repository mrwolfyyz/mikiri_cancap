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
  "chat_handler_origination"
  "address_verification"
  "contact_extraction"
)

# Functions that require address_utils.py
FUNCTIONS_NEEDING_ADDRESS_UTILS=(
  "contact_extraction"
  "address_geocoding"
  "address_verification"
  "report_generator_skiptrace"
  "report_generator_origination"
)

# Functions that require contact_extraction_utils.py
FUNCTIONS_NEEDING_CONTACT_EXTRACTION=(
  "contact_extraction"
  "report_generator_skiptrace"
  "report_generator_origination"
)

# Functions that require domain_utils.py
FUNCTIONS_NEEDING_DOMAIN_UTILS=(
  "domain_enrichment"
  "phase1_identity"
  "report_generator_skiptrace"
  "report_generator_origination"
)

# Functions that require report_utils.py
FUNCTIONS_NEEDING_REPORT_UTILS=(
  "report_generator_skiptrace"
  "report_generator_origination"
)

# Functions that require chat_handler_base.py
FUNCTIONS_NEEDING_CHAT_HANDLER_BASE=(
  "chat_handler"
  "chat_handler_origination"
)

# Source file locations
RETRY_UTILS_SOURCE="$ROOT_DIR/gcp/shared/retry_utils.py"
ADDRESS_UTILS_SOURCE="$ROOT_DIR/gcp/shared/address_utils.py"
CONTACT_EXTRACTION_UTILS_SOURCE="$ROOT_DIR/gcp/shared/contact_extraction_utils.py"
DOMAIN_UTILS_SOURCE="$ROOT_DIR/gcp/shared/domain_utils.py"
REPORT_UTILS_SOURCE="$ROOT_DIR/gcp/shared/report_utils.py"
CHAT_HANDLER_BASE_SOURCE="$ROOT_DIR/gcp/shared/chat_handler_base.py"

# Check if source files exist
ALL_SOURCES_OK=true
for source_file in "$RETRY_UTILS_SOURCE" "$ADDRESS_UTILS_SOURCE" "$CONTACT_EXTRACTION_UTILS_SOURCE" "$DOMAIN_UTILS_SOURCE" "$REPORT_UTILS_SOURCE" "$CHAT_HANDLER_BASE_SOURCE"; do
  if [[ ! -f "$source_file" ]]; then
    echo "❌ ERROR: Source file not found: $source_file"
    ALL_SOURCES_OK=false
  fi
done

if [[ "$ALL_SOURCES_OK" != "true" ]]; then
  exit 1
fi

echo "Sources:"
echo "  $RETRY_UTILS_SOURCE"
echo "  $ADDRESS_UTILS_SOURCE"
echo "  $CONTACT_EXTRACTION_UTILS_SOURCE"
echo "  $DOMAIN_UTILS_SOURCE"
echo "  $REPORT_UTILS_SOURCE"
echo "  $CHAT_HANDLER_BASE_SOURCE"
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

# Copy address_utils.py to each function that needs it
echo "Copying address_utils.py to functions..."
for func in "${FUNCTIONS_NEEDING_ADDRESS_UTILS[@]}"; do
  DEST_DIR="$ROOT_DIR/gcp/functions/$func"
  DEST_FILE="$DEST_DIR/address_utils.py"
  
  if [[ ! -d "$DEST_DIR" ]]; then
    echo "  ⚠️  Directory not found: $DEST_DIR"
    continue
  fi
  
  cp "$ADDRESS_UTILS_SOURCE" "$DEST_FILE"
  echo "  ✓ $func/address_utils.py"
done

echo ""

# Copy contact_extraction_utils.py to each function that needs it
echo "Copying contact_extraction_utils.py to functions..."
for func in "${FUNCTIONS_NEEDING_CONTACT_EXTRACTION[@]}"; do
  DEST_DIR="$ROOT_DIR/gcp/functions/$func"
  DEST_FILE="$DEST_DIR/contact_extraction_utils.py"
  
  if [[ ! -d "$DEST_DIR" ]]; then
    echo "  ⚠️  Directory not found: $DEST_DIR"
    continue
  fi
  
  cp "$CONTACT_EXTRACTION_UTILS_SOURCE" "$DEST_FILE"
  echo "  ✓ $func/contact_extraction_utils.py"
done

echo ""

# Copy domain_utils.py to each function that needs it
echo "Copying domain_utils.py to functions..."
for func in "${FUNCTIONS_NEEDING_DOMAIN_UTILS[@]}"; do
  DEST_DIR="$ROOT_DIR/gcp/functions/$func"
  DEST_FILE="$DEST_DIR/domain_utils.py"
  
  if [[ ! -d "$DEST_DIR" ]]; then
    echo "  ⚠️  Directory not found: $DEST_DIR"
    continue
  fi
  
  cp "$DOMAIN_UTILS_SOURCE" "$DEST_FILE"
  echo "  ✓ $func/domain_utils.py"
done

echo ""

# Copy report_utils.py to each function that needs it
echo "Copying report_utils.py to functions..."
for func in "${FUNCTIONS_NEEDING_REPORT_UTILS[@]}"; do
  DEST_DIR="$ROOT_DIR/gcp/functions/$func"
  DEST_FILE="$DEST_DIR/report_utils.py"
  
  if [[ ! -d "$DEST_DIR" ]]; then
    echo "  ⚠️  Directory not found: $DEST_DIR"
    continue
  fi
  
  cp "$REPORT_UTILS_SOURCE" "$DEST_FILE"
  echo "  ✓ $func/report_utils.py"
done

echo ""

# Copy chat_handler_base.py to each function that needs it
echo "Copying chat_handler_base.py to functions..."
for func in "${FUNCTIONS_NEEDING_CHAT_HANDLER_BASE[@]}"; do
  DEST_DIR="$ROOT_DIR/gcp/functions/$func"
  DEST_FILE="$DEST_DIR/chat_handler_base.py"
  
  if [[ ! -d "$DEST_DIR" ]]; then
    echo "  ⚠️  Directory not found: $DEST_DIR"
    continue
  fi
  
  cp "$CHAT_HANDLER_BASE_SOURCE" "$DEST_FILE"
  echo "  ✓ $func/chat_handler_base.py"
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

echo "Verifying address_utils.py presence..."
for func in "${FUNCTIONS_NEEDING_ADDRESS_UTILS[@]}"; do
  DEST_FILE="$ROOT_DIR/gcp/functions/$func/address_utils.py"
  if [[ -f "$DEST_FILE" ]]; then
    echo "  ✓ $func/address_utils.py"
  else
    echo "  ❌ $func/address_utils.py - MISSING!"
    ALL_OK=false
  fi
done

echo ""

echo "Verifying contact_extraction_utils.py presence..."
for func in "${FUNCTIONS_NEEDING_CONTACT_EXTRACTION[@]}"; do
  DEST_FILE="$ROOT_DIR/gcp/functions/$func/contact_extraction_utils.py"
  if [[ -f "$DEST_FILE" ]]; then
    echo "  ✓ $func/contact_extraction_utils.py"
  else
    echo "  ❌ $func/contact_extraction_utils.py - MISSING!"
    ALL_OK=false
  fi
done

echo ""

echo "Verifying domain_utils.py presence..."
for func in "${FUNCTIONS_NEEDING_DOMAIN_UTILS[@]}"; do
  DEST_FILE="$ROOT_DIR/gcp/functions/$func/domain_utils.py"
  if [[ -f "$DEST_FILE" ]]; then
    echo "  ✓ $func/domain_utils.py"
  else
    echo "  ❌ $func/domain_utils.py - MISSING!"
    ALL_OK=false
  fi
done

echo ""

echo "Verifying report_utils.py presence..."
for func in "${FUNCTIONS_NEEDING_REPORT_UTILS[@]}"; do
  DEST_FILE="$ROOT_DIR/gcp/functions/$func/report_utils.py"
  if [[ -f "$DEST_FILE" ]]; then
    echo "  ✓ $func/report_utils.py"
  else
    echo "  ❌ $func/report_utils.py - MISSING!"
    ALL_OK=false
  fi
done

echo ""

echo "Verifying chat_handler_base.py presence..."
for func in "${FUNCTIONS_NEEDING_CHAT_HANDLER_BASE[@]}"; do
  DEST_FILE="$ROOT_DIR/gcp/functions/$func/chat_handler_base.py"
  if [[ -f "$DEST_FILE" ]]; then
    echo "  ✓ $func/chat_handler_base.py"
  else
    echo "  ❌ $func/chat_handler_base.py - MISSING!"
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
