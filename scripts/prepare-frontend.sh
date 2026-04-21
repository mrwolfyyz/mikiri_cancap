#!/bin/bash
# =============================================================================
# Prepare Frontend Script
# =============================================================================
# Prepares frontend source files for deployment by:
# 1. Copying shared JS/CSS files to platform directories
# 2. Processing HTML templates with platform-specific configuration
#
# Run this script before deploying frontend (firebase deploy).
# Mirrors the backend pattern in scripts/prepare-functions.sh.
#
# Requires: jq (brew install jq)
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

SHARED_PUBLIC="$ROOT_DIR/frontend/shared/public"
SHARED_TEMPLATES="$ROOT_DIR/frontend/shared/templates"

PLATFORMS=("origination" "skiptrace")

echo "=========================================="
echo "Preparing frontend source files"
echo "=========================================="
echo ""

# =============================================================================
# Prerequisites Check
# =============================================================================
if ! command -v jq &> /dev/null; then
  echo "❌ ERROR: jq is required but not installed."
  echo "   Install with: brew install jq (macOS) or sudo apt install -y jq (Ubuntu/WSL)"
  exit 1
fi

# Verify shared source directories exist
if [[ ! -d "$SHARED_PUBLIC" ]]; then
  echo "❌ ERROR: Shared public directory not found: $SHARED_PUBLIC"
  exit 1
fi

if [[ ! -d "$SHARED_TEMPLATES" ]]; then
  echo "❌ ERROR: Shared templates directory not found: $SHARED_TEMPLATES"
  exit 1
fi

# =============================================================================
# Shared JavaScript files to copy to ALL platforms
# =============================================================================
SHARED_JS_ALL=(
  "platform-config.js"
  "auth.js"
  "shared-utils.js"
  "app-core.js"
  "report-renderer.js"
  "results.js"
  "chat-core.js"
)

# Shared JavaScript files to copy ONLY to platforms with addressVerification feature
SHARED_JS_ADDRESS_VERIFICATION=(
  "address-verification.js"
)

# Shared CSS files to copy to ALL platforms
SHARED_CSS=(
  "styles.css"
  "shared.css"
  "chat.css"
  "results.css"
)

# HTML templates to process
TEMPLATES=(
  "index.html"
  "chat.html"
  "results.html"
)

# =============================================================================
# Step 1: Copy shared JS files
# =============================================================================
echo "Step 1: Copying shared JavaScript files..."
echo ""

for platform in "${PLATFORMS[@]}"; do
  DEST_DIR="$ROOT_DIR/frontend/$platform/public"

  if [[ ! -d "$DEST_DIR" ]]; then
    echo "  ⚠️  Directory not found: $DEST_DIR"
    continue
  fi

  # Get platform config
  PLATFORM_CONFIG="$DEST_DIR/platform.json"
  if [[ ! -f "$PLATFORM_CONFIG" ]]; then
    echo "  ❌ ERROR: platform.json not found: $PLATFORM_CONFIG"
    exit 1
  fi

  echo "  [$platform]"

  # Copy JS files needed by all platforms
  for js_file in "${SHARED_JS_ALL[@]}"; do
    SOURCE="$SHARED_PUBLIC/$js_file"
    if [[ ! -f "$SOURCE" ]]; then
      echo "    ❌ Source not found: $SOURCE"
      exit 1
    fi
    cp "$SOURCE" "$DEST_DIR/$js_file"
    echo "    ✓ $js_file"
  done

  # Copy address verification JS only if feature is enabled
  HAS_ADDRESS_VERIFICATION=$(jq -r '.features.addressVerification // false' "$PLATFORM_CONFIG")
  if [[ "$HAS_ADDRESS_VERIFICATION" == "true" ]]; then
    for js_file in "${SHARED_JS_ADDRESS_VERIFICATION[@]}"; do
      SOURCE="$SHARED_PUBLIC/$js_file"
      if [[ ! -f "$SOURCE" ]]; then
        echo "    ❌ Source not found: $SOURCE"
        exit 1
      fi
      cp "$SOURCE" "$DEST_DIR/$js_file"
      echo "    ✓ $js_file (feature: addressVerification)"
    done
  fi

  echo ""
done

# =============================================================================
# Step 2: Copy shared CSS files
# =============================================================================
echo "Step 2: Copying shared CSS files..."
echo ""

for platform in "${PLATFORMS[@]}"; do
  DEST_DIR="$ROOT_DIR/frontend/$platform/public"

  echo "  [$platform]"
  for css_file in "${SHARED_CSS[@]}"; do
    SOURCE="$SHARED_PUBLIC/$css_file"
    if [[ ! -f "$SOURCE" ]]; then
      echo "    ❌ Source not found: $SOURCE"
      exit 1
    fi
    cp "$SOURCE" "$DEST_DIR/$css_file"
    echo "    ✓ $css_file"
  done
  echo ""
done

# =============================================================================
# Step 3: Process HTML templates
# =============================================================================
echo "Step 3: Processing HTML templates..."
echo ""

process_template() {
  local template="$1"
  local platform_config="$2"
  local output="$3"
  local platform=$(jq -r '.platform' "$platform_config")

  # Cross-platform in-place edit helper (works on BSD/macOS and GNU/Linux sed)
  sed_in_place() {
    local expression="$1"
    local target_file="$2"
    local temp_file
    temp_file="$(mktemp "${target_file}.tmp.XXXXXX")"
    sed "$expression" "$target_file" > "$temp_file"
    mv "$temp_file" "$target_file"
  }

  # Start with a copy of the template
  cp "$template" "$output"

  # ---- Simple {{token}} replacements ----
  # Read values from platform.json using jq
  local pageTitle=$(jq -r '.ui.pageTitle' "$platform_config")
  local headerTitle=$(jq -r '.ui.headerTitle' "$platform_config")
  local headerSubtitle=$(jq -r '.ui.headerSubtitle' "$platform_config")
  local heroText=$(jq -r '.ui.heroText' "$platform_config")
  local submitButtonText=$(jq -r '.ui.submitButtonText' "$platform_config")
  local successMessage=$(jq -r '.ui.successMessage' "$platform_config")
  local chatPageTitle=$(jq -r '.ui.chatPageTitle' "$platform_config")
  local chatHeaderTitle=$(jq -r '.ui.chatHeaderTitle' "$platform_config")
  local chatHeaderSubtitle=$(jq -r '.ui.chatHeaderSubtitle' "$platform_config")
  local chatAssistantTitle=$(jq -r '.ui.chatAssistantTitle' "$platform_config")
  local folderName=$(jq -r '.content.folderName' "$platform_config")
  local folderAltName=$(jq -r '.content.folderAltName' "$platform_config")
  local viewReportText=$(jq -r '.content.viewReportText' "$platform_config")

  # Use sed to replace tokens (using | as delimiter to avoid issues with /)
  sed_in_place "s|{{pageTitle}}|${pageTitle}|g" "$output"
  sed_in_place "s|{{headerTitle}}|${headerTitle}|g" "$output"
  sed_in_place "s|{{headerSubtitle}}|${headerSubtitle}|g" "$output"
  sed_in_place "s|{{heroText}}|${heroText}|g" "$output"
  sed_in_place "s|{{submitButtonText}}|${submitButtonText}|g" "$output"
  sed_in_place "s|{{successMessage}}|${successMessage}|g" "$output"
  sed_in_place "s|{{chatPageTitle}}|${chatPageTitle}|g" "$output"
  sed_in_place "s|{{chatHeaderTitle}}|${chatHeaderTitle}|g" "$output"
  sed_in_place "s|{{chatHeaderSubtitle}}|${chatHeaderSubtitle}|g" "$output"
  sed_in_place "s|{{chatAssistantTitle}}|${chatAssistantTitle}|g" "$output"
  sed_in_place "s|{{folderName}}|${folderName}|g" "$output"
  sed_in_place "s|{{folderAltName}}|${folderAltName}|g" "$output"
  sed_in_place "s|{{viewReportText}}|${viewReportText}|g" "$output"

  # ---- Conditional blocks: {{#if feature}}...{{/if feature}} ----

  # Handle addressVerification feature
  local hasAddrVerif=$(jq -r '.features.addressVerification // false' "$platform_config")
  if [[ "$hasAddrVerif" == "true" ]]; then
    # Keep content, remove markers
    sed_in_place '/{{#if addressVerification}}/d' "$output"
    sed_in_place '/{{\/if addressVerification}}/d' "$output"
  else
    # Remove entire block including markers
    sed_in_place '/{{#if addressVerification}}/,/{{\/if addressVerification}}/d' "$output"
  fi

  # Handle platform-specific blocks
  if [[ "$platform" == "origination" ]]; then
    # Keep origination blocks, remove markers
    sed_in_place '/{{#if origination}}/d' "$output"
    sed_in_place '/{{\/if origination}}/d' "$output"
    # Remove entire skiptrace blocks
    sed_in_place '/{{#if skiptrace}}/,/{{\/if skiptrace}}/d' "$output"
  elif [[ "$platform" == "skiptrace" ]]; then
    # Keep skiptrace blocks, remove markers
    sed_in_place '/{{#if skiptrace}}/d' "$output"
    sed_in_place '/{{\/if skiptrace}}/d' "$output"
    # Remove entire origination blocks
    sed_in_place '/{{#if origination}}/,/{{\/if origination}}/d' "$output"
  fi

  # Remove the template comment header (first 4 lines if they match)
  sed_in_place '1{/<!-- =====/d;}' "$output"
  sed_in_place '1{/THIS IS A TEMPLATE/d;}' "$output"
  sed_in_place '1{/Processed by scripts/d;}' "$output"
  sed_in_place '1{/====== -->/d;}' "$output"
}

for platform in "${PLATFORMS[@]}"; do
  DEST_DIR="$ROOT_DIR/frontend/$platform/public"
  PLATFORM_CONFIG="$DEST_DIR/platform.json"

  echo "  [$platform]"
  for template_file in "${TEMPLATES[@]}"; do
    TEMPLATE="$SHARED_TEMPLATES/$template_file"
    OUTPUT="$DEST_DIR/$template_file"

    if [[ ! -f "$TEMPLATE" ]]; then
      echo "    ⚠️  Template not found: $TEMPLATE"
      continue
    fi

    process_template "$TEMPLATE" "$PLATFORM_CONFIG" "$OUTPUT"
    echo "    ✓ $template_file"
  done
  echo ""
done

# =============================================================================
# Verification
# =============================================================================
echo "=========================================="
echo "Verification"
echo "=========================================="
echo ""

ALL_OK=true

for platform in "${PLATFORMS[@]}"; do
  DEST_DIR="$ROOT_DIR/frontend/$platform/public"
  PLATFORM_CONFIG="$DEST_DIR/platform.json"

  echo "  [$platform]"

  # Verify shared JS files
  for js_file in "${SHARED_JS_ALL[@]}"; do
    if [[ -f "$DEST_DIR/$js_file" ]]; then
      echo "    ✓ $js_file"
    else
      echo "    ❌ $js_file - MISSING!"
      ALL_OK=false
    fi
  done

  # Verify address verification JS (only for platforms that need it)
  HAS_ADDRESS_VERIFICATION=$(jq -r '.features.addressVerification // false' "$PLATFORM_CONFIG")
  if [[ "$HAS_ADDRESS_VERIFICATION" == "true" ]]; then
    for js_file in "${SHARED_JS_ADDRESS_VERIFICATION[@]}"; do
      if [[ -f "$DEST_DIR/$js_file" ]]; then
        echo "    ✓ $js_file (feature: addressVerification)"
      else
        echo "    ❌ $js_file - MISSING!"
        ALL_OK=false
      fi
    done
  fi

  # Verify shared CSS files
  for css_file in "${SHARED_CSS[@]}"; do
    if [[ -f "$DEST_DIR/$css_file" ]]; then
      echo "    ✓ $css_file"
    else
      echo "    ❌ $css_file - MISSING!"
      ALL_OK=false
    fi
  done

  # Verify HTML files were generated
  for template_file in "${TEMPLATES[@]}"; do
    if [[ -f "$DEST_DIR/$template_file" ]]; then
      echo "    ✓ $template_file"
    else
      echo "    ❌ $template_file - MISSING!"
      ALL_OK=false
    fi
  done

  # Verify no template markers remain in generated HTML
  for template_file in "${TEMPLATES[@]}"; do
    OUTPUT="$DEST_DIR/$template_file"
    if [[ -f "$OUTPUT" ]]; then
      if grep -q '{{#if\|{{/if\|{{[a-zA-Z]' "$OUTPUT" 2>/dev/null; then
        echo "    ❌ $template_file contains unprocessed template markers!"
        grep -n '{{' "$OUTPUT" | head -5
        ALL_OK=false
      fi
    fi
  done

  # Verify platform.json exists
  if [[ -f "$PLATFORM_CONFIG" ]]; then
    echo "    ✓ platform.json"
  else
    echo "    ❌ platform.json - MISSING!"
    ALL_OK=false
  fi

  # Verify static assets exist (favicons, etc.)
  for asset in "favicon.ico" "favicon-32x32.png" "favicon-16x16.png" "apple-touch-icon.png"; do
    if [[ -f "$DEST_DIR/$asset" ]]; then
      echo "    ✓ $asset"
    else
      echo "    ⚠️  $asset not found (static asset)"
    fi
  done

  echo ""
done

echo "=========================================="
if [[ "$ALL_OK" == "true" ]]; then
  echo "✓ All frontend preparations complete!"
  exit 0
else
  echo "❌ Some files have issues. Please check the errors above."
  exit 1
fi
