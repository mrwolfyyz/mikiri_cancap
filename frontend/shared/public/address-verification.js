// =============================================================================
// Address Verification Module (Origination Only)
// =============================================================================
// THIS FILE IS THE SOURCE OF TRUTH
// Copied to origination/public/ by scripts/prepare-frontend.sh
// NOT copied to skiptrace (feature flag: addressVerification)
//
// Requires: app-core.js (provides elements, API_URL, getAuthToken,
//           formatUserFriendlyError, showAddressVerificationFieldError)
// =============================================================================

// ===========================
// Address Verification Validation
// ===========================
function validateBusinessName(businessName) {
  if (!businessName || businessName.trim().length === 0) {
    return "Business name is required";
  }

  if (businessName.length < 2) {
    return "Business name must be at least 2 characters";
  }

  if (businessName.length > 200) {
    return "Business name must be less than 200 characters";
  }

  return null;
}

function validateStreetAddress(streetAddress) {
  if (!streetAddress || streetAddress.trim().length === 0) {
    return "Street address is required";
  }

  if (streetAddress.length < 5) {
    return "Street address must be at least 5 characters";
  }

  if (streetAddress.length > 200) {
    return "Street address must be less than 200 characters";
  }

  return null;
}

function validateAddressCity(city) {
  if (!city || city.trim().length === 0) {
    return "City is required";
  }

  if (city.length < 2) {
    return "City must be at least 2 characters";
  }

  if (city.length > 100) {
    return "City must be less than 100 characters";
  }

  return null;
}

function validateAddressProvince(province) {
  if (!province || province.trim().length === 0) {
    return "Province is required";
  }

  return null;
}

function validatePostalCode(postalCode) {
  if (!postalCode || postalCode.trim().length === 0) {
    return "Postal code is required";
  }

  // Canadian postal code format: A1A 1A1
  const postalCodePattern = /^[A-Za-z][0-9][A-Za-z] [0-9][A-Za-z][0-9]$/;
  const cleaned = postalCode.trim().toUpperCase();

  if (!postalCodePattern.test(cleaned)) {
    return "Postal code must be in format A1A 1A1 (e.g., M5H 2N2)";
  }

  return null;
}

function formatPostalCode(value) {
  // Auto-format postal code as user types: A1A1A1 -> A1A 1A1
  const cleaned = value.replace(/\s/g, "").toUpperCase().slice(0, 6);
  if (cleaned.length > 3) {
    return cleaned.slice(0, 3) + " " + cleaned.slice(3);
  }
  return cleaned;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// ===========================
// Address Verification API
// ===========================
async function submitAddressVerification(
  streetAddress,
  suiteUnit,
  city,
  province,
  postalCode,
  businessName
) {
  const token = await getAuthToken(); // Get fresh token

  const requestBody = {
    street_address: streetAddress.trim(),
    suite_unit: suiteUnit ? suiteUnit.trim() : "",
    city: city.trim(),
    province: province.trim(),
    postal_code: postalCode.trim(),
    business_name: businessName.trim(),
  };

  const response = await fetch(`${API_URL}/address-verification`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(requestBody),
  });

  if (!response.ok) {
    if (response.status === 401) {
      // Token expired or invalid - try to refresh
      try {
        const newToken = await getAuthToken();
        // Retry once with new token
        const retryResponse = await fetch(`${API_URL}/address-verification`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${newToken}`,
          },
          body: JSON.stringify(requestBody),
        });
        if (!retryResponse.ok) {
          const errorData = await retryResponse.json().catch(() => ({}));
          throw new Error(
            errorData.error ||
              `HTTP ${retryResponse.status}: ${retryResponse.statusText}`
          );
        }
        return await retryResponse.json();
      } catch (retryError) {
        throw new Error("Authentication failed. Please refresh the page.");
      }
    }
    const errorData = await response.json().catch(() => ({}));
    throw new Error(
      errorData.error || `HTTP ${response.status}: ${response.statusText}`
    );
  }

  return await response.json();
}

// ===========================
// Address Verification UI Functions
// ===========================
function showAddressVerificationLoading() {
  elements.addressVerificationForm.style.display = "none";
  elements.addressVerificationResults.style.display = "none";
  elements.addressVerificationError.style.display = "none";
  elements.addressVerificationLoading.style.display = "block";
}

function showAddressVerificationResults(data) {
  elements.addressVerificationForm.style.display = "none";
  elements.addressVerificationLoading.style.display = "none";
  elements.addressVerificationError.style.display = "none";
  elements.addressVerificationResults.style.display = "block";

  const analysis = data.analysis || {};
  let html = "";

  // Combined status + risk card
  const businessAtAddress = analysis.business_at_address;
  const businessStatus = businessAtAddress ? "Verified" : "Not Found";
  const businessStatusClass = businessAtAddress ? "verification-verified" : "verification-not-found";
  const riskLevel = analysis.fraud_risk_level || "unknown";
  const riskClass = `risk-${riskLevel}`;

  html += `<div class="verification-status-card ${riskClass}">`;
  html += `<h3>Business: <span class="${businessStatusClass}">${businessAtAddress ? "✓" : "✗"} ${escapeHtml(businessStatus)}</span>`;
  html += ` · Risk Level: <span class="verification-badge">${escapeHtml(riskLevel.toUpperCase())}</span></h3>`;
  html += `<p>${escapeHtml(analysis.reasoning || "No reasoning provided")}</p>`;
  html += `</div>`;

  // Street View link
  const geocoding = data.geocoding || {};
  const streetViewUrl = geocoding.street_view_url;
  if (streetViewUrl) {
    html += `<div class="verification-street-view">`;
    html += `<a href="${escapeHtml(streetViewUrl)}" target="_blank" rel="noopener noreferrer" class="button-secondary">`;
    html += `<svg class="button-icon" width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 1C5.24 1 3 3.24 3 6c0 3.75 5 9 5 9s5-5.25 5-9c0-2.76-2.24-5-5-5zm0 7a2 2 0 1 1 0-4 2 2 0 0 1 0 4z" stroke="currentColor" stroke-width="1.5"/></svg>`;
    html += `View on Google Street View`;
    html += `</a>`;
    html += `</div>`;
  }

  // Fraud indicators
  const fraudIndicators = analysis.fraud_indicators || [];
  if (fraudIndicators.length > 0) {
    html += `<div class="verification-section">`;
    html += `<h4>Verification Concerns</h4>`;
    html += `<ul>`;
    fraudIndicators.forEach((indicator) => {
      html += `<li class="verification-flag-item">⚠️ ${escapeHtml(indicator)}</li>`;
    });
    html += `</ul>`;
    html += `</div>`;
  }

  // Address type classifications
  html += `<div class="verification-section">`;
  html += `<h4>Address Classification</h4>`;
  html += `<ul>`;
  if (analysis.is_virtual_workspace) {
    html += `<li class="verification-flag-item">⚠️ Virtual workspace/co-working space</li>`;
  }
  if (analysis.is_shipping_location) {
    html += `<li class="verification-flag-item">⚠️ Shipping/mailbox location (UPS/FedEx/PO Box)</li>`;
  }
  if (analysis.is_residential) {
    html += `<li class="verification-flag-item">⚠️ Residential address (red flag for business)</li>`;
  }
  if (
    !analysis.is_virtual_workspace &&
    !analysis.is_shipping_location &&
    !analysis.is_residential
  ) {
    html += `<li>✓ Appears to be a legitimate business address</li>`;
  }
  html += `</ul>`;
  html += `</div>`;

  // Key findings
  const keyFindings = analysis.key_findings || [];
  if (keyFindings.length > 0) {
    html += `<div class="verification-section">`;
    html += `<h4>Key Findings</h4>`;
    html += `<ul>`;
    keyFindings.forEach((finding) => {
      html += `<li>${escapeHtml(finding)}</li>`;
    });
    html += `</ul>`;
    html += `</div>`;
  }

  // Confidence level
  html += `<div class="verification-confidence">`;
  html += `Confidence: <strong>${escapeHtml(analysis.confidence || "unknown")}</strong>`;
  html += `</div>`;

  // Search Results Section (collapsible)
  const searchResults = data.search_results?.queries || [];
  if (searchResults.length > 0) {
    const queryLabels = {
      business_name_and_address: "Business Name + Address (Exact Match)",
      business_name_and_address_flexible: "Business Name + Address (Flexible)",
      address_only: "Address Only",
      business_name_and_location: "Business Name + Location",
      business_reviews_ratings: "Reviews & Ratings",
      business_complaints_fraud: "Complaints & Fraud Reports",
    };

    const typeClassMap = {
      high_precision: "type-high-precision",
      context: "type-context",
      high_recall: "type-high-recall",
    };

    const typeLabelMap = {
      high_precision: "High Precision",
      context: "Context",
      high_recall: "High Recall",
    };

    const totalResults = searchResults.reduce(
      (sum, query) => sum + (query.hits?.length || 0),
      0
    );

    html += `<div class="verification-search-section">`;
    html += `<details class="verification-search-collapsible">`;
    html += `<summary>`;
    html += `<span class="search-toggle-icon" aria-hidden="true"></span>`;
    html += `<span>Search Results</span>`;
    html += `<span class="search-toggle-label">Show evidence</span>`;
    html += `<span class="search-count-badge">${totalResults} results</span>`;
    html += `</summary>`;
    html += `<div class="verification-search-content">`;
    const actualQueryCount = data.search_results?.grounding_metadata?.search_queries?.length || searchResults.length;
    html += `<p class="verification-search-summary">Found ${totalResults} total results across ${
      actualQueryCount
    } search ${actualQueryCount === 1 ? "query" : "queries"}.</p>`;

    searchResults.forEach((query, queryIndex) => {
      const queryId = query.id || `query_${queryIndex}`;
      const queryLabel =
        queryLabels[queryId] ||
        queryId.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase());
      const queryType = query.type || "unknown";
      const typeClass = typeClassMap[queryType] || "";
      const typeLabel = typeLabelMap[queryType] || queryType;
      const hits = query.hits || [];
      const queryString = query.query || "";

      html += `<div class="verification-query-card ${typeClass}">`;

      // Query header
      html += `<div class="verification-query-header">`;
      html += `<div class="verification-query-header-row">`;
      html += `<div>`;
      html += `<h5>${escapeHtml(queryLabel)}</h5>`;
      html += `<span class="verification-query-badge">${escapeHtml(typeLabel)}</span>`;
      html += `</div>`;
      html += `<div class="verification-query-count">${hits.length} result${hits.length === 1 ? "" : "s"}</div>`;
      html += `</div>`;
      const queriesList = query.search_queries_list || (queryString ? queryString.split(", ") : []);
      if (queriesList.length > 1) {
        html += `<div class="verification-query-string"><strong>Queries:</strong>`;
        html += `<ul class="verification-queries-list">`;
        queriesList.forEach((q) => {
          const googleSearchUrl = `https://www.google.com/search?q=${encodeURIComponent(q)}`;
          html += `<li><a href="${googleSearchUrl}" target="_blank" rel="noopener noreferrer">${escapeHtml(q)}</a></li>`;
        });
        html += `</ul></div>`;
      } else {
        html += `<div class="verification-query-string"><strong>Query:</strong> ${escapeHtml(queryString)}</div>`;
      }
      html += `</div>`;

      // Query results
      if (hits.length > 0) {
        html += `<div class="verification-hits-body">`;
        hits.forEach((hit, hitIndex) => {
          const title = hit.title || "Untitled";
          const url = hit.url || "#";
          const snippet = hit.snippet || "";

          html += `<div class="verification-hit">`;
          html += `<div><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="verification-hit-link">${hitIndex + 1}. ${escapeHtml(title)}</a></div>`;
          if (url && !url.includes("vertexaisearch.cloud.google.com")) {
            html += `<div class="verification-hit-url">${escapeHtml(url)}</div>`;
          }
          if (snippet) {
            html += `<div class="verification-hit-snippet">${escapeHtml(snippet)}</div>`;
          }
          html += `</div>`;
        });
        html += `</div>`;
      } else {
        html += `<div class="verification-no-results">No results found for this query.</div>`;
      }

      html += `</div>`;
    });

    html += `</div>`;
    html += `</details>`;
    html += `</div>`;
  }

  elements.addressVerificationResultsContent.innerHTML = html;
}

function showAddressVerificationError(message) {
  elements.addressVerificationForm.style.display = "none";
  elements.addressVerificationLoading.style.display = "none";
  elements.addressVerificationResults.style.display = "none";
  elements.addressVerificationError.style.display = "block";

  elements.addressVerificationErrorMessage.textContent =
    formatUserFriendlyError(message);
}

function showAddressVerificationFieldError(fieldError, message) {
  if (!fieldError) return;

  if (message) {
    fieldError.textContent = message;
    if (fieldError.previousElementSibling) {
      fieldError.previousElementSibling.classList.add("error");
    }
  } else {
    fieldError.textContent = "";
    if (fieldError.previousElementSibling) {
      fieldError.previousElementSibling.classList.remove("error");
    }
  }
}

function resetAddressVerificationForm() {
  if (!elements.addressVerificationForm) return;

  elements.addressVerificationForm.style.display = "block";
  if (elements.addressVerificationLoading)
    elements.addressVerificationLoading.style.display = "none";
  if (elements.addressVerificationResults)
    elements.addressVerificationResults.style.display = "none";
  if (elements.addressVerificationError)
    elements.addressVerificationError.style.display = "none";

  elements.addressVerificationForm.reset();
  if (elements.businessNameError)
    showAddressVerificationFieldError(elements.businessNameError, null);
  if (elements.streetAddressError)
    showAddressVerificationFieldError(elements.streetAddressError, null);
  if (elements.suiteUnitError)
    showAddressVerificationFieldError(elements.suiteUnitError, null);
  if (elements.addressCityError)
    showAddressVerificationFieldError(elements.addressCityError, null);
  if (elements.addressProvinceError)
    showAddressVerificationFieldError(elements.addressProvinceError, null);
  if (elements.postalCodeError)
    showAddressVerificationFieldError(elements.postalCodeError, null);
}

async function handleAddressVerificationSubmit(e) {
  console.log("Address verification form submitted");
  e.preventDefault();
  e.stopPropagation();

  // Debug: Check if elements exist
  if (!elements.businessNameInput) {
    console.error("businessNameInput not found");
    return false;
  }
  if (!elements.streetAddressInput) {
    console.error("streetAddressInput not found");
    return false;
  }
  if (!elements.suiteUnitInput) {
    console.error("suiteUnitInput not found");
    return false;
  }
  if (!elements.addressCityInput) {
    console.error("addressCityInput not found");
    return false;
  }
  if (!elements.addressProvinceInput) {
    console.error("addressProvinceInput not found");
    return false;
  }
  if (!elements.postalCodeInput) {
    console.error("postalCodeInput not found");
    return false;
  }

  // Clear previous errors
  if (elements.businessNameError)
    showAddressVerificationFieldError(elements.businessNameError, null);
  if (elements.streetAddressError)
    showAddressVerificationFieldError(elements.streetAddressError, null);
  if (elements.suiteUnitError)
    showAddressVerificationFieldError(elements.suiteUnitError, null);
  if (elements.addressCityError)
    showAddressVerificationFieldError(elements.addressCityError, null);
  if (elements.addressProvinceError)
    showAddressVerificationFieldError(elements.addressProvinceError, null);
  if (elements.postalCodeError)
    showAddressVerificationFieldError(elements.postalCodeError, null);

  // Get values
  const businessName = elements.businessNameInput.value;
  const streetAddress = elements.streetAddressInput.value;
  const suiteUnit = elements.suiteUnitInput.value;
  const city = elements.addressCityInput.value;
  const province = elements.addressProvinceInput.value;
  const postalCode = elements.postalCodeInput.value.trim().toUpperCase();

  // Validate
  const businessNameError = validateBusinessName(businessName);
  const streetAddressError = validateStreetAddress(streetAddress);
  const cityError = validateAddressCity(city);
  const provinceError = validateAddressProvince(province);
  const postalCodeError = validatePostalCode(postalCode);

  let hasErrors = false;

  if (businessNameError && elements.businessNameError) {
    showAddressVerificationFieldError(
      elements.businessNameError,
      businessNameError
    );
    hasErrors = true;
  }

  if (streetAddressError && elements.streetAddressError) {
    showAddressVerificationFieldError(
      elements.streetAddressError,
      streetAddressError
    );
    hasErrors = true;
  }

  if (cityError && elements.addressCityError) {
    showAddressVerificationFieldError(elements.addressCityError, cityError);
    hasErrors = true;
  }

  if (provinceError && elements.addressProvinceError) {
    showAddressVerificationFieldError(
      elements.addressProvinceError,
      provinceError
    );
    hasErrors = true;
  }

  if (postalCodeError && elements.postalCodeError) {
    showAddressVerificationFieldError(
      elements.postalCodeError,
      postalCodeError
    );
    hasErrors = true;
  }

  if (hasErrors) return;

  // Disable submit button
  elements.addressVerificationButton.disabled = true;
  elements.addressVerificationButton.textContent = "Verifying...";

  try {
    // Show loading
    showAddressVerificationLoading();

    // Submit verification with separate fields
    const result = await submitAddressVerification(
      streetAddress,
      suiteUnit,
      city,
      province,
      postalCode,
      businessName
    );

    // Show results
    showAddressVerificationResults(result);
  } catch (error) {
    console.error("Address verification error:", error);
    showAddressVerificationError(
      error.message || "Failed to verify address. Please try again."
    );
  } finally {
    elements.addressVerificationButton.disabled = false;
    elements.addressVerificationButton.textContent = "Verify Address";
  }

  return false;
}

// ===========================
// Address Verification Event Listeners
// ===========================
// Called from app-core.js initEventListeners() when addressVerification feature is enabled
function initAddressVerificationListeners() {
  // Address verification form submission
  if (elements.addressVerificationForm) {
    elements.addressVerificationForm.addEventListener(
      "submit",
      handleAddressVerificationSubmit
    );
    console.log("Address verification form event listener attached");
  } else {
    console.error("Address verification form not found!");
  }

  // Postal code auto-formatting
  if (elements.postalCodeInput) {
    elements.postalCodeInput.addEventListener("input", (e) => {
      const formatted = formatPostalCode(e.target.value);
      e.target.value = formatted;
    });
  }

  // Address verification retry button
  if (elements.addressVerificationRetryButton) {
    elements.addressVerificationRetryButton.addEventListener(
      "click",
      resetAddressVerificationForm
    );
  }

  // Address verification new search buttons (top and bottom)
  if (elements.addressVerificationNewSearchButton) {
    elements.addressVerificationNewSearchButton.addEventListener(
      "click",
      resetAddressVerificationForm
    );
  }
  if (elements.addressVerificationNewSearchButtonBottom) {
    elements.addressVerificationNewSearchButtonBottom.addEventListener(
      "click",
      resetAddressVerificationForm
    );
  }
}
