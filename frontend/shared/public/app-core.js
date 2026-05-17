// =============================================================================
// App Core - Shared Application Logic
// =============================================================================
// THIS FILE IS THE SOURCE OF TRUTH
// Copied to platform directories by scripts/prepare-frontend.sh
//
// Contains all shared investigation form logic. Platform-specific behavior
// is driven by PlatformConfig (loaded from platform.json at runtime).
// Address verification functions are in address-verification.js (origination only).
//
// Requires: platform-config.js (must be loaded first)
// =============================================================================

// ===========================
// Configuration
// ===========================
// These values are set by loadConfig() from PlatformConfig
let API_URL = null;
let FIREBASE_CONFIG = null;

const POLL_INTERVAL_MS = 3000; // Poll every 3 seconds
const THROTTLE_MS = 30000; // 30 seconds between submissions
const MAX_POLL_ATTEMPTS = 150; // Stop polling after 7.5 minutes (to handle identity function retries)
const STATUS_UPDATE_INTERVAL_MS = 1000; // Update status message every second
const HISTORY_PAGE_SIZE = 50;

// Load configuration from platform.json + firebase-config.json via PlatformConfig
async function loadConfig() {
  try {
    await PlatformConfig.load();

    // Set API URL from firebase config
    API_URL = PlatformConfig.apiUrl;

    // Set Firebase config
    FIREBASE_CONFIG = PlatformConfig.firebaseConfig;

    return true;
  } catch (error) {
    console.error("Failed to load configuration:", error);
    // PlatformConfig.load() already shows user-friendly error
    return false;
  }
}

// ===========================
// State Management
// ===========================
let currentJobId = null;
let pollAttempts = 0;
let pollTimeoutId = null;
let lastRequestTime = 0;
let statusMessageTimer = null;
let statusMessageStartTime = null;
let currentStatusMessage = "";
let historyLoaded = false;
let historyUsersLoaded = false;
let historyRows = [];
let activeFeedbackJobId = null;
let _modalTriggerEl = null;
let historyPageTokens = [null];
let historyPageIndex = 0;
let historyNextPageToken = null;
let historyAppliedFilters = null;
let historyLoading = false;
let historyRequestSequence = 0;
let historyTotalCount = null;

// ===========================
// DOM Elements
// ===========================
const elements = {
  // Tabs
  tabButtons: document.querySelectorAll(".tab-button"),
  tabContents: document.querySelectorAll(".tab-content"),

  // Form
  form: document.getElementById("investigationForm"),
  carsReferenceNumberInput: document.getElementById("carsReferenceNumber"),
  fullNameInput: document.getElementById("fullName"),
  cityInput: document.getElementById("city"),
  provinceInput: document.getElementById("province"),
  emailInput: document.getElementById("email"),
  companyNameInput: document.getElementById("companyName"),
  submitButton: document.getElementById("submitButton"),

  // Sections
  progressSection: document.getElementById("progressSection"),
  resultsSection: document.getElementById("resultsSection"),
  errorSection: document.getElementById("errorSection"),

  // Progress
  progressStatus: document.getElementById("progressStatus"),
  progressTime: document.getElementById("progressTime"),
  jobIdDisplay: document.getElementById("jobIdDisplay"),

  // Results
  inlineReportContainer: document.getElementById("inlineReportContainer"),
  newReportButton: document.getElementById("newReportButton"),
  newReportButtonBottom: document.getElementById("newReportButtonBottom"),

  // Errors
  errorMessage: document.getElementById("errorMessage"),
  retryButton: document.getElementById("retryButton"),
  carsReferenceNumberError: document.getElementById("carsReferenceNumberError"),
  fullNameError: document.getElementById("fullNameError"),
  cityError: document.getElementById("cityError"),
  provinceError: document.getElementById("provinceError"),
  emailError: document.getElementById("emailError"),
  companyNameError: document.getElementById("companyNameError"),

  // Setup
  driveUrl: document.getElementById("driveUrl"),
  saveDriveButton: document.getElementById("saveDriveButton"),
  clearDriveButton: document.getElementById("clearDriveButton"),
  driveStatus: document.getElementById("driveStatus"),
  driveWarning: document.getElementById("driveWarning"),
  setupLink: document.getElementById("setupLink"),

  // Search History (skiptrace only)
  historyStartDate: document.getElementById("historyStartDate"),
  historyEndDate: document.getElementById("historyEndDate"),
  historyUserFilter: document.getElementById("historyUserFilter"),
  historyCarsFilter: document.getElementById("historyCarsFilter"),
  historyApplyFiltersButton: document.getElementById("historyApplyFiltersButton"),
  historyClearFiltersButton: document.getElementById("historyClearFiltersButton"),
  historyExportButton: document.getElementById("historyExportButton"),
  historyStatus: document.getElementById("historyStatus"),
  historyTableBody: document.getElementById("historyTableBody"),
  historyPaginationStatus: document.getElementById("historyPaginationStatus"),
  historyPrevPageButton: document.getElementById("historyPrevPageButton"),
  historyNextPageButton: document.getElementById("historyNextPageButton"),
  feedbackModal: document.getElementById("feedbackModal"),
  feedbackModalTitle: document.getElementById("feedbackModalTitle"),
  feedbackModalSubtitle: document.getElementById("feedbackModalSubtitle"),
  feedbackModalClose: document.getElementById("feedbackModalClose"),
  feedbackEntries: document.getElementById("feedbackEntries"),
  feedbackRating: document.getElementById("feedbackRating"),
  feedbackComment: document.getElementById("feedbackComment"),
  feedbackSubmitButton: document.getElementById("feedbackSubmitButton"),
  feedbackError: document.getElementById("feedbackError"),

  // Address Verification (elements will be null if feature is not enabled)
  addressVerificationForm: document.getElementById("addressVerificationForm"),
  businessNameInput: document.getElementById("businessName"),
  streetAddressInput: document.getElementById("streetAddress"),
  suiteUnitInput: document.getElementById("suiteUnit"),
  addressCityInput: document.getElementById("addressCity"),
  addressProvinceInput: document.getElementById("addressProvince"),
  postalCodeInput: document.getElementById("postalCode"),
  addressVerificationButton: document.getElementById(
    "addressVerificationButton"
  ),
  addressVerificationLoading: document.getElementById(
    "addressVerificationLoading"
  ),
  addressVerificationResults: document.getElementById(
    "addressVerificationResults"
  ),
  addressVerificationResultsContent: document.getElementById(
    "addressVerificationResultsContent"
  ),
  addressVerificationError: document.getElementById("addressVerificationError"),
  addressVerificationErrorMessage: document.getElementById(
    "addressVerificationErrorMessage"
  ),
  addressVerificationRetryButton: document.getElementById(
    "addressVerificationRetryButton"
  ),
  addressVerificationNewSearchButton: document.getElementById(
    "addressVerificationNewSearchButton"
  ),
  addressVerificationNewSearchButtonBottom: document.getElementById(
    "addressVerificationNewSearchButtonBottom"
  ),
  businessNameError: document.getElementById("businessNameError"),
  streetAddressError: document.getElementById("streetAddressError"),
  suiteUnitError: document.getElementById("suiteUnitError"),
  addressCityError: document.getElementById("addressCityError"),
  addressProvinceError: document.getElementById("addressProvinceError"),
  postalCodeError: document.getElementById("postalCodeError"),

  // Theme
  darkModeToggle: document.getElementById("darkModeToggle"),
};

// ===========================
// Local Storage Helpers
// ===========================
const storage = {
  getDriveFolderId: () => localStorage.getItem("driveFolderId"),
  setDriveFolderId: (id) => localStorage.setItem("driveFolderId", id),
  clearDriveFolderId: () => localStorage.removeItem("driveFolderId"),

  getDarkMode: () => localStorage.getItem("darkMode") === "true",
  setDarkMode: (enabled) =>
    localStorage.setItem("darkMode", enabled.toString()),
};

// ===========================
// Firebase Authentication
// ===========================
async function initializeAuth() {
  try {
    initializeFirebase(FIREBASE_CONFIG);
    await ensureSignedIn();
  } catch (error) {
    console.error("Authentication error:", error);
    if (PlatformConfig.requireSso) {
      showSignInRequired("Please sign in with your Google account.", () => {
        window.location.reload();
      });
      throw error;
    }
    alert("Unable to initialize authentication. Please refresh the page.");
    throw error;
  }
}

// getAuthToken() is now in shared-utils.js (loaded before this file)

// ===========================
// Drive Folder ID Extraction
// ===========================
function extractDriveFolderId(url) {
  if (!url) return null;

  // Match various Drive URL formats
  const patterns = [/\/folders\/([a-zA-Z0-9_-]+)/, /id=([a-zA-Z0-9_-]+)/];

  for (const pattern of patterns) {
    const match = url.match(pattern);
    if (match && match[1]) {
      return match[1];
    }
  }

  // If it's just a folder ID (no URL), validate it looks right
  if (url.length >= 20 && url.length <= 100 && /^[a-zA-Z0-9_-]+$/.test(url)) {
    return url;
  }

  return null;
}

// ===========================
// Theme Management
// ===========================
function initTheme() {
  const darkMode = storage.getDarkMode();
  document.documentElement.setAttribute(
    "data-theme",
    darkMode ? "dark" : "light"
  );
}

function toggleTheme() {
  const currentTheme = document.documentElement.getAttribute("data-theme");
  const newTheme = currentTheme === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", newTheme);
  storage.setDarkMode(newTheme === "dark");
}

// ===========================
// Tab Management
// ===========================
function switchTab(tabName) {
  elements.tabButtons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });

  elements.tabContents.forEach((content) => {
    content.classList.toggle("active", content.id === `${tabName}-tab`);
  });

  if (tabName === "history") {
    loadHistoryUsers();
    if (!historyLoaded) {
      loadSearchHistory();
    }
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatHistoryDate(value) {
  if (!value) return "Not available";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

// ===========================
// Form Validation
// ===========================
function validateCarsReferenceNumber(carsReferenceNumber) {
  const trimmed = (carsReferenceNumber || "").trim();
  if (!trimmed) {
    return "CARS Reference Number is required";
  }

  if (!/^[A-Za-z]{5}\d+$/.test(trimmed)) {
    return "CARS Reference Number must start with 5 letters followed by numbers";
  }

  return null;
}

function validateFullName(name) {
  if (!name || name.trim().length < 2) {
    return "Full name is required";
  }

  const parts = name.trim().split(/\s+/);
  if (parts.length < 2) {
    return "Must contain first and last name";
  }

  if (name.length > 100) {
    return "Name must be less than 100 characters";
  }

  return null;
}

function validateCity(city) {
  if (!city || city.trim().length < 2) {
    return "City is required";
  }

  if (city.length > 100) {
    return "City must be less than 100 characters";
  }

  return null;
}

function validateEmail(email) {
  // Check if email is required based on platform config
  if (!email || email.trim().length === 0) {
    return PlatformConfig.get("emailRequired") ? "Email is required" : null;
  }

  const pattern = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;
  if (!pattern.test(email)) {
    return "Invalid email format";
  }

  if (email.length > 254) {
    return "Email must be less than 254 characters";
  }

  return null;
}

function validateCompanyName(companyName) {
  const trimmed = (companyName || "").trim();
  if (trimmed.length === 0) {
    return null;
  }

  if (trimmed.length > 200) {
    return "Company name must be less than 200 characters";
  }

  return null;
}

function validateProvince(province) {
  // Province is required
  if (!province || province.trim().length === 0) {
    return "Province is required";
  }

  // Must be a valid province code
  const validProvinces = ["ON", "BC", "AB", "QC", "MB", "SK", "NS", "NB", "NL", "PE", "NT", "YT", "NU"];
  if (!validProvinces.includes(province)) {
    return "Invalid province selected";
  }

  return null;
}

function generateEmailFromName(fullName) {
  if (!fullName || fullName.trim().length === 0) {
    return "";
  }

  // Split name and get first and last parts
  const parts = fullName.trim().toLowerCase().split(/\s+/);

  if (parts.length < 2) {
    return ""; // Need at least first and last name
  }

  // Get first name and last name, remove special characters
  const firstName = parts[0].replace(/[^a-z]/g, "");
  const lastName = parts[parts.length - 1].replace(/[^a-z]/g, "");

  if (!firstName || !lastName) {
    return "";
  }

  return `${firstName}.${lastName}@gmail.com`;
}

function autoGenerateEmail() {
  const fullName = elements.fullNameInput.value;
  const currentEmail = elements.emailInput.value;

  // Only auto-generate if email field is empty or was previously auto-generated
  if (!currentEmail || currentEmail.endsWith("@gmail.com")) {
    const generatedEmail = generateEmailFromName(fullName);
    if (generatedEmail) {
      elements.emailInput.value = generatedEmail;
    }
  }
}

function showFieldError(fieldError, message) {
  if (message) {
    fieldError.textContent = message;
    fieldError.previousElementSibling.classList.add("error");
  } else {
    fieldError.textContent = "";
    fieldError.previousElementSibling.classList.remove("error");
  }
}

// ===========================
// Rate Limiting / Throttling
// ===========================
function canSubmitRequest() {
  const now = Date.now();
  const timeSinceLastRequest = now - lastRequestTime;

  if (timeSinceLastRequest < THROTTLE_MS) {
    const waitSeconds = Math.ceil((THROTTLE_MS - timeSinceLastRequest) / 1000);
    alert(
      `Please wait ${waitSeconds} seconds before submitting another request.`
    );
    return false;
  }

  return true;
}

// ===========================
// Drive Configuration Check
// ===========================
function checkDriveConfiguration() {
  const driveFolderId = storage.getDriveFolderId();
  if (elements.driveWarning) {
    elements.driveWarning.style.display = driveFolderId ? "none" : "block";
  }
  return !!driveFolderId;
}

// ===========================
// API Calls
// ===========================
async function submitInvestigation(fullName, city, email, companyName = "", province = "", carsReferenceNumber = "") {
  const driveFolderId = storage.getDriveFolderId() || "";

  const requestBody = {
    full_name: fullName.trim(),
    city: city.trim(),
    email: email.trim(),
    drive_folder_id: driveFolderId,
    company_name: companyName.trim(),
    province: province.trim(),
  };

  const normalizedCarsReferenceNumber = carsReferenceNumber.trim().toUpperCase();
  if (normalizedCarsReferenceNumber) {
    requestBody.cars_reference_number = normalizedCarsReferenceNumber;
  }

  const endpoint = PlatformConfig.get("investigateEndpoint");

  const response = await fetch(`${API_URL}${endpoint}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(await authHeaders()),
    },
    body: JSON.stringify(requestBody),
  });

  if (!response.ok) {
    if (response.status === 401) {
      // Token expired or invalid - try to refresh
      try {
        // Retry once with new token
        const retryResponse = await fetch(
          `${API_URL}${endpoint}`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...(await authHeaders()),
            },
            body: JSON.stringify(requestBody),
          }
        );
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

async function pollJobStatus(jobId) {
  const response = await fetch(`${API_URL}/jobs/${jobId}`, {
    headers: await authHeaders(),
  });

  if (!response.ok) {
    if (response.status === 401) {
      // Token expired - try to refresh
      try {
        const retryResponse = await fetch(`${API_URL}/jobs/${jobId}`, {
          headers: await authHeaders(),
        });
        if (!retryResponse.ok) {
          throw new Error("Authentication failed. Please refresh the page.");
        }
        return await retryResponse.json();
      } catch (retryError) {
        throw new Error("Authentication failed. Please refresh the page.");
      }
    }
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }

  return await response.json();
}

// ===========================
// UI State Management
// ===========================
function showProgress(fullName, status = "pending") {
  elements.form.style.display = "none";
  elements.resultsSection.style.display = "none";
  elements.errorSection.style.display = "none";
  elements.progressSection.style.display = "block";

  elements.jobIdDisplay.textContent = fullName;
  updateProgressStatus(status);
  startStatusMessageTimer();
}

function updateProgressStatus(status) {
  const runningMessage = PlatformConfig.getUI("runningStatusMessage") || "Processing...";
  const statusMessages = {
    pending: "Queuing investigation...",
    running: runningMessage,
    post_processing: "Investigation complete, generating reports...",
  };

  elements.progressStatus.textContent =
    statusMessages[status] || "Processing...";
}

// ===========================
// Status Message Timer
// ===========================
const STATUS_MESSAGES = [
  { minSeconds: 0, text: "Initiated investigation" },
  { minSeconds: 10, text: "Checking domain registration history" },
  { minSeconds: 15, text: "Assessing maturity of mail infrastructure" },
  { minSeconds: 25, text: "Reviewing data breaches associated with email" },
  { minSeconds: 30, text: "Analyzing digital footprint" },
  { minSeconds: 55, text: "Searching for social media accounts" },
  { minSeconds: 75, text: "Looking deeper" },
  { minSeconds: 85, text: "Turning over rocks" },
  { minSeconds: 90, text: "Exhausting all open-web sources" },
  { minSeconds: 100, text: "Looking in every possible corner" },
  { minSeconds: 105, text: "Calling my mom for advice" },
  { minSeconds: 110, text: "Testing your patience" },
  { minSeconds: 115, text: "Activating turbo button" },
  { minSeconds: 160, text: "Diving into deeper data sources" },
  { minSeconds: 190, text: "Almost there, promise" },
  { minSeconds: 220, text: "The best things in life are worth waiting for" },
  { minSeconds: 225, text: "Patience is a virtue, but it's not a lot of fun" },
  { minSeconds: 230, text: "Searching for stoic proverbs to distract you" },
  {
    minSeconds: 235,
    text: "You have power over your mind, not outside events. Realize this, and you will find strength",
  },
  {
    minSeconds: 250,
    text: "It's not what happens to you, but how you react to it that matters",
  },
  {
    minSeconds: 275,
    text: "He who laughs at himself never runs out of things to laugh at",
  },
  {
    minSeconds: 300,
    text: "There is only one way to happiness and that is to cease worrying about things which are beyond the power of our will",
  },
  {
    minSeconds: 330,
    text: "True happiness is... to enjoy the present, without anxious dependence upon the future",
  },
  {
    minSeconds: 340,
    text: "The greatest obstacle to living is expectancy, which hangs upon tomorrow and loses today",
  },
  {
    minSeconds: 345,
    text: "The impediment to action advances action. What stands in the way becomes the way",
  },
  { minSeconds: 350, text: "The best answer to anger is silence" },
  { minSeconds: 400, text: "Confine yourself to the present" },
  { minSeconds: 415, text: "Give yourself a gift: the present moment" },
  {
    minSeconds: 450,
    text: "Still investigating... thank you for your patience",
  },
];

function getMessageColorClass(seconds) {
  if (seconds < 100) return "status-technical";
  if (seconds < 220) return "status-humor";
  return "status-philosophy";
}

function startStatusMessageTimer() {
  // Clear any existing timer
  stopStatusMessageTimer();

  // Reset state
  statusMessageStartTime = Date.now();
  currentStatusMessage = "";

  // Update immediately
  updateStatusMessage();

  // Start interval
  statusMessageTimer = setInterval(
    updateStatusMessage,
    STATUS_UPDATE_INTERVAL_MS
  );
}

function stopStatusMessageTimer() {
  if (statusMessageTimer) {
    clearInterval(statusMessageTimer);
    statusMessageTimer = null;
  }
  statusMessageStartTime = null;
  currentStatusMessage = "";
}

function updateStatusMessage() {
  if (!statusMessageStartTime || !elements.progressTime) return;

  const elapsedSeconds = Math.floor(
    (Date.now() - statusMessageStartTime) / 1000
  );

  // Find the appropriate message based on elapsed time
  let message = STATUS_MESSAGES[0].text;
  for (let i = STATUS_MESSAGES.length - 1; i >= 0; i--) {
    if (elapsedSeconds >= STATUS_MESSAGES[i].minSeconds) {
      message = STATUS_MESSAGES[i].text;
      break;
    }
  }

  // Only update if message changed
  if (message !== currentStatusMessage) {
    currentStatusMessage = message;

    // Remove old color classes
    elements.progressTime.classList.remove(
      "status-technical",
      "status-humor",
      "status-philosophy"
    );

    // Add fade transition
    elements.progressTime.classList.add("fade-transition");

    // Update text and color after brief fade
    setTimeout(() => {
      elements.progressTime.textContent = message;
      elements.progressTime.classList.add(getMessageColorClass(elapsedSeconds));

      // Remove fade transition
      setTimeout(() => {
        elements.progressTime.classList.remove("fade-transition");
      }, 150);
    }, 150);
  }
}

async function showResults(job) {
  // Stop the status message timer
  stopStatusMessageTimer();

  const workflowType = job.workflow_type || PlatformConfig.get("defaultWorkflow");

  // Keep progress visible with "Loading report..." while fetching markdown
  elements.progressStatus.textContent = "Loading report...";

  try {
    const getToken = () => authHeaders();
    const markdownReports = await window.ReportRenderer.loadMarkdownReports(
      API_URL,
      currentJobId,
      getToken
    );

    // Build jobData from poll response (API returns input, created_at/started_at/completed_at as ISO strings)
    const jobData = {
      input: job.input || {},
      full_name: job.input?.full_name,
      city: job.input?.city,
      created_at: job.created_at,
      started_at: job.started_at,
      completed_at: job.completed_at,
    };

    elements.progressSection.style.display = "none";
    elements.resultsSection.style.display = "block";

    async function submitFeedback(jobId, rating, comment) {
      const response = await fetch(`${API_URL}/jobs/${jobId}/feedback`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(await authHeaders()),
        },
        body: JSON.stringify({ rating, comment }),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || "Failed to submit feedback");
      }
      return await response.json();
    }

    window.ReportRenderer.renderReport(elements.inlineReportContainer, {
      jobId: currentJobId,
      workflowType,
      jobData,
      markdownReports,
      onSubmitFeedback: submitFeedback,
    });

    document.querySelector(".container")?.classList.add("report-expanded");
    elements.resultsSection.classList.add("report-expanded");
  } catch (error) {
    console.error("Error loading report:", error);
    elements.progressSection.style.display = "none";
    elements.resultsSection.style.display = "block";
    elements.inlineReportContainer.replaceChildren();
    const errP = document.createElement("p");
    errP.className = "error-message";
    errP.textContent = `Failed to load report: ${error.message}`;
    elements.inlineReportContainer.appendChild(errP);
  }
}

function formatUserFriendlyError(message) {
  // Map technical errors to user-friendly messages
  const errorMappings = [
    {
      pattern: /timed out|timeout/i,
      friendly:
        "The investigation is taking longer than expected. This can happen with complex searches. Please try again in a few minutes.",
    },
    {
      pattern: /workflow.*failed|Failed to start/i,
      friendly:
        "We couldn't start the investigation. Please try again. If this persists, contact support.",
    },
    {
      pattern: /network|connection|fetch/i,
      friendly:
        "Unable to connect to the server. Please check your internet connection and try again.",
    },
    {
      pattern: /drive.*folder|folder.*not.*configured/i,
      friendly:
        "Google Drive folder not configured. Please go to Setup and add your Drive folder URL.",
    },
    {
      pattern: /validation|invalid/i,
      friendly:
        "Please check your input and try again. Make sure all required fields are filled correctly.",
    },
  ];

  for (const mapping of errorMappings) {
    if (mapping.pattern.test(message)) {
      return mapping.friendly;
    }
  }

  // Default: return original message with a helpful prefix
  return `Something went wrong: ${message}. Please try again or contact support if this persists.`;
}

function showError(message) {
  // Stop the status message timer
  stopStatusMessageTimer();

  elements.progressSection.style.display = "none";
  elements.resultsSection.style.display = "none";
  elements.errorSection.style.display = "block";

  elements.errorMessage.textContent = formatUserFriendlyError(message);
}

function resetForm() {
  elements.form.style.display = "block";
  elements.progressSection.style.display = "none";
  elements.resultsSection.style.display = "none";
  elements.errorSection.style.display = "none";

  if (elements.inlineReportContainer) {
    elements.inlineReportContainer.innerHTML = "";
  }
  document.querySelector(".container")?.classList.remove("report-expanded");
  elements.resultsSection?.classList.remove("report-expanded");

  elements.form.reset();
  showFieldError(elements.fullNameError, null);
  showFieldError(elements.cityError, null);
  showFieldError(elements.provinceError, null);
  showFieldError(elements.emailError, null);
  showFieldError(elements.companyNameError, null);

  currentJobId = null;
  pollAttempts = 0;
  if (pollTimeoutId) {
    clearTimeout(pollTimeoutId);
    pollTimeoutId = null;
  }
}

// ===========================
// Job Polling
// ===========================
async function startPolling(jobId) {
  currentJobId = jobId;
  pollAttempts = 0;

  const poll = async () => {
    try {
      pollAttempts++;

      if (pollAttempts > MAX_POLL_ATTEMPTS) {
        throw new Error(
          "The investigation is taking longer than expected. Your job (ID: " +
            jobId +
            ") may still be processing. Please check back in a few minutes or contact support."
        );
      }

      const job = await pollJobStatus(jobId);

      // Update progress status
      if (job.status === "running" || job.status === "post_processing") {
        updateProgressStatus(job.status);
      }

      // Check terminal states
      if (job.status === "complete") {
        showResults(job);
        return;
      }

      if (
        job.status === "failed" ||
        job.status === "failed_report_generation"
      ) {
        throw new Error(job.error || "Investigation failed");
      }

      // Continue polling
      pollTimeoutId = setTimeout(poll, POLL_INTERVAL_MS);
    } catch (error) {
      console.error("Polling error:", error);
      showError(error.message || "An error occurred. Please try again.");
    }
  };

  // Start polling
  poll();
}

// ===========================
// Form Submission
// ===========================
async function handleSubmit(e) {
  e.preventDefault();

  // Clear previous errors
  if (elements.carsReferenceNumberError) {
    showFieldError(elements.carsReferenceNumberError, null);
  }
  showFieldError(elements.fullNameError, null);
  showFieldError(elements.cityError, null);
  showFieldError(elements.provinceError, null);
  showFieldError(elements.emailError, null);
  showFieldError(elements.companyNameError, null);

  // Get values
  const carsReferenceNumber = elements.carsReferenceNumberInput?.value || "";
  const fullName = elements.fullNameInput.value;
  const city = elements.cityInput.value;
  const province = elements.provinceInput.value;
  const email = elements.emailInput.value;
  const companyName = elements.companyNameInput.value;

  // Validate
  const carsReferenceNumberError = elements.carsReferenceNumberInput
    ? validateCarsReferenceNumber(carsReferenceNumber)
    : null;
  const fullNameError = validateFullName(fullName);
  const cityError = validateCity(city);
  const provinceError = validateProvince(province);
  const emailError = validateEmail(email);
  const companyNameError = validateCompanyName(companyName);

  let hasErrors = false;

  if (carsReferenceNumberError) {
    showFieldError(elements.carsReferenceNumberError, carsReferenceNumberError);
    hasErrors = true;
  }

  if (fullNameError) {
    showFieldError(elements.fullNameError, fullNameError);
    hasErrors = true;
  }

  if (cityError) {
    showFieldError(elements.cityError, cityError);
    hasErrors = true;
  }

  if (provinceError) {
    showFieldError(elements.provinceError, provinceError);
    hasErrors = true;
  }

  if (emailError) {
    showFieldError(elements.emailError, emailError);
    hasErrors = true;
  }

  if (companyNameError) {
    showFieldError(elements.companyNameError, companyNameError);
    hasErrors = true;
  }

  if (hasErrors) return;

  // If email is empty, generate one from the name
  const finalEmail = email.trim() || generateEmailFromName(fullName);

  // Check rate limit
  if (!canSubmitRequest()) {
    return;
  }

  // Disable submit button
  elements.submitButton.disabled = true;
  elements.submitButton.textContent = "Submitting...";

  try {
    // Submit investigation
    const result = await submitInvestigation(
      fullName,
      city,
      finalEmail,
      companyName,
      province,
      carsReferenceNumber
    );

    // Update last request time
    lastRequestTime = Date.now();

    // Show progress and start polling
    showProgress(fullName);
    await startPolling(result.job_id);
  } catch (error) {
    console.error("Submission error:", error);
    showError(
      error.message || "Failed to submit investigation. Please try again."
    );
  } finally {
    elements.submitButton.disabled = false;
    elements.submitButton.textContent = PlatformConfig.getUI("submitButtonText") || "Submit";
  }
}

// ===========================
// Drive Configuration
// ===========================
function saveDriveConfiguration() {
  const url = elements.driveUrl.value.trim();

  if (!url) {
    showDriveStatus("Please enter a Google Drive folder URL", "error");
    return;
  }

  const folderId = extractDriveFolderId(url);

  if (!folderId) {
    showDriveStatus(
      "Invalid Google Drive folder URL. Please check the format.",
      "error"
    );
    return;
  }

  storage.setDriveFolderId(folderId);
  showDriveStatus(
    `✓ Connected to folder: ${folderId.substring(0, 20)}...`,
    "success"
  );
  checkDriveConfiguration();
}

function clearDriveConfiguration() {
  storage.clearDriveFolderId();
  elements.driveUrl.value = "";
  showDriveStatus("Drive configuration cleared", "success");
  checkDriveConfiguration();
}

function showDriveStatus(message, type) {
  elements.driveStatus.textContent = message;
  elements.driveStatus.className = `status-message ${type}`;
  elements.driveStatus.style.display = "block";

  // Auto-hide after 5 seconds
  setTimeout(() => {
    elements.driveStatus.style.display = "none";
  }, 5000);
}

function loadDriveConfiguration() {
  const folderId = storage.getDriveFolderId();

  if (folderId) {
    // Show the folder ID (not the full URL since we only store ID)
    elements.driveUrl.value = folderId;
    showDriveStatus(
      `✓ Connected to folder: ${folderId.substring(0, 20)}...`,
      "success"
    );
  }
}

// ===========================
// Search History
// ===========================
function hasHistoryUi() {
  return !!elements.historyTableBody;
}

function setHistoryStatus(message, type = "info") {
  if (!elements.historyStatus) return;
  elements.historyStatus.textContent = message;
  elements.historyStatus.className = `status-message ${type}`;
  elements.historyStatus.style.display = message ? "block" : "none";
}

function historyFilterParams() {
  const params = new URLSearchParams();
  const startDate = elements.historyStartDate?.value || "";
  const endDate = elements.historyEndDate?.value || "";
  const userId = elements.historyUserFilter?.value.trim() || "";
  const carsReferenceNumber = elements.historyCarsFilter?.value.trim().toUpperCase() || "";

  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (userId) params.set("user_id", userId);
  if (carsReferenceNumber) params.set("cars_reference_number", carsReferenceNumber);
  return params;
}

function resetHistoryPagination() {
  historyPageTokens = [null];
  historyPageIndex = 0;
  historyNextPageToken = null;
}

function updateHistoryPaginationStatus(rowCount = historyRows.length) {
  if (!elements.historyPaginationStatus) return;

  if (historyLoading) {
    elements.historyPaginationStatus.textContent = `Loading page ${historyPageIndex + 1}...`;
  } else if (!rowCount) {
    elements.historyPaginationStatus.textContent = "No rows to show.";
  } else {
    const pageNum = historyPageIndex + 1;
    const total =
      typeof historyTotalCount === "number"
        ? historyTotalCount > 10000
          ? "10,000+"
          : historyTotalCount.toLocaleString()
        : null;
    elements.historyPaginationStatus.textContent = total
      ? `Page ${pageNum} · showing ${rowCount} of ${total}`
      : `Page ${pageNum}: showing ${rowCount} rows`;
  }
}

function updateHistoryPaginationControls() {
  updateHistoryPaginationStatus();
  if (elements.historyPrevPageButton) {
    elements.historyPrevPageButton.disabled = historyLoading || historyPageIndex === 0;
  }
  if (elements.historyNextPageButton) {
    elements.historyNextPageButton.disabled = historyLoading || !historyNextPageToken;
  }
}

function renderHistoryRows(rows) {
  if (!elements.historyTableBody) return;

  if (!rows.length) {
    const hasFilters = historyAppliedFilters && [...historyAppliedFilters].length > 0;
    const msg = hasFilters ? "No searches match these filters." : "No searches found yet.";
    elements.historyTableBody.innerHTML = `<tr><td colspan="6" class="history-empty">${msg}</td></tr>`;
    return;
  }

  elements.historyTableBody.innerHTML = rows
    .map((row) => {
      const feedback = row.feedback || {};
      const feedbackText = feedback.comment_summary || (row.feedback_count ? "Feedback submitted" : "No feedback yet");
      const resultsUrl = row.results_url || `results.html?job_id=${encodeURIComponent(row.job_id)}&workflow=skiptrace`;
      const userDisplay = row.user_display || row.user_email || row.user_name || row.user_id || "Not available";
      return `
        <tr>
          <td>${escapeHtml(row.cars_reference_number || "Not available")}</td>
          <td>${escapeHtml(formatHistoryDate(row.created_at))}</td>
          <td>${escapeHtml(row.full_name || "Not available")}</td>
          <td>${escapeHtml(userDisplay)}</td>
          <td>
            <div class="history-feedback-summary">${escapeHtml(feedbackText)}</div>
            <button class="history-action-link" data-feedback-job-id="${escapeHtml(row.job_id)}">View / Add Feedback</button>
          </td>
          <td><a class="history-result-link" href="${escapeHtml(resultsUrl)}" target="_blank" rel="noopener">Open Report</a></td>
        </tr>`;
    })
    .join("");
}

// ===========================
// History URL State
// ===========================
function _pushHistoryState() {
  const p = new URLSearchParams();
  const v = (id) => document.getElementById(id)?.value || "";
  if (v("historyStartDate")) p.set("start_date", v("historyStartDate"));
  if (v("historyEndDate")) p.set("end_date", v("historyEndDate"));
  if (v("historyUserFilter")) p.set("user_id", v("historyUserFilter"));
  if (v("historyCarsFilter")) p.set("cars_reference_number", v("historyCarsFilter"));
  const qs = p.toString();
  window.history.replaceState({}, "", qs ? `?${qs}` : window.location.pathname);
}

function _hydrateHistoryFromUrl() {
  const p = new URLSearchParams(window.location.search);
  const set = (id, key) => {
    if (p.has(key)) {
      const el = document.getElementById(id);
      if (el) el.value = p.get(key);
    }
  };
  set("historyStartDate", "start_date");
  set("historyEndDate", "end_date");
  set("historyUserFilter", "user_id");
  set("historyCarsFilter", "cars_reference_number");
  return p.has("start_date") || p.has("end_date") || p.has("user_id") || p.has("cars_reference_number");
}

// ===========================
// History Error Messages
// ===========================
const HISTORY_ERROR_MAP = [
  ["page token", "Your session page expired — returning to page 1."],
  ["Too many requests", "Too many requests. Please wait a moment and try again."],
  ["Rate limit", "Too many requests. Please wait a moment and try again."],
  ["Failed to load", "Unable to load search history. Please try again."],
  ["Failed to export", "Unable to export CSV. Please try again."],
];

function _friendlyHistoryError(msg) {
  for (const [key, friendly] of HISTORY_ERROR_MAP) {
    if (msg?.includes(key)) return friendly;
  }
  return "Something went wrong. Please try again.";
}

// ===========================
// History Skeleton
// ===========================
function renderHistorySkeleton() {
  if (!elements.historyTableBody) return;
  elements.historyTableBody.innerHTML = Array(5)
    .fill(0)
    .map(
      () =>
        `<tr class="history-skeleton-row">${Array(6)
          .fill('<td><span class="skeleton-cell"></span></td>')
          .join("")}</tr>`
    )
    .join("");
}

async function loadHistoryUsers() {
  if (historyUsersLoaded) return;
  try {
    const cached = sessionStorage.getItem("historyUsers");
    if (cached) {
      populateUserDatalist(JSON.parse(cached));
      historyUsersLoaded = true;
      return;
    }
    const resp = await fetch(`${API_URL}/jobs/history/users`, { headers: await authHeaders() });
    if (!resp.ok) return;
    const { users } = await resp.json();
    sessionStorage.setItem("historyUsers", JSON.stringify(users));
    populateUserDatalist(users);
    historyUsersLoaded = true;
  } catch {
    // fail silently — free-text filter still works
  }
}

function populateUserDatalist(users) {
  const dl = document.getElementById("historyUserList");
  if (!dl) return;
  dl.innerHTML = users
    .map((u) => `<option value="${escapeHtml(u.user_email)}">${escapeHtml(u.user_name || u.user_email)}</option>`)
    .join("");
}

async function loadSearchHistory({ resetPage = false } = {}) {
  if (!hasHistoryUi()) return;

  if (resetPage || !historyAppliedFilters) {
    historyAppliedFilters = historyFilterParams();
    resetHistoryPagination();
  }

  _pushHistoryState();
  setHistoryStatus("Loading search history...", "info");
  renderHistorySkeleton();
  if (elements.historyApplyFiltersButton) elements.historyApplyFiltersButton.disabled = true;
  historyLoading = true;
  updateHistoryPaginationControls();
  const requestSequence = ++historyRequestSequence;

  try {
    const params = new URLSearchParams(historyAppliedFilters.toString());
    params.set("limit", String(HISTORY_PAGE_SIZE));
    const pageToken = historyPageTokens[historyPageIndex];
    if (pageToken) params.set("page_token", pageToken);
    const response = await fetch(`${API_URL}/jobs/history?${params.toString()}`, {
      headers: await authHeaders(),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.error || "Failed to load search history");
    }
    const data = await response.json();
    if (requestSequence !== historyRequestSequence) return;
    historyRows = data.rows || [];
    historyTotalCount = data.total_count ?? null;
    historyNextPageToken = data.next_page_token || null;
    historyPageTokens = historyPageTokens.slice(0, historyPageIndex + 1);
    if (historyNextPageToken) historyPageTokens.push(historyNextPageToken);
    if (historyPageTokens.length > 50) {
      const drop = historyPageTokens.length - 50;
      historyPageTokens.splice(0, drop);
      historyPageIndex = Math.max(0, historyPageIndex - drop);
    }
    historyLoaded = true;
    renderHistoryRows(historyRows);
    setHistoryStatus("", "info");
  } catch (error) {
    if (requestSequence !== historyRequestSequence) return;
    console.error("Search history load failed:", error);
    if (error.message?.includes("page token")) {
      resetHistoryPagination();
      loadSearchHistory({ resetPage: true });
      return;
    }
    historyRows = [];
    historyTotalCount = null;
    historyNextPageToken = null;
    renderHistoryRows([]);
    setHistoryStatus(_friendlyHistoryError(error.message), "error");
  } finally {
    if (requestSequence === historyRequestSequence) {
      historyLoading = false;
      updateHistoryPaginationControls();
      if (elements.historyApplyFiltersButton) elements.historyApplyFiltersButton.disabled = false;
    }
  }
}

function clearSearchHistoryFilters() {
  if (elements.historyStartDate) elements.historyStartDate.value = "";
  if (elements.historyEndDate) elements.historyEndDate.value = "";
  if (elements.historyUserFilter) elements.historyUserFilter.value = "";
  if (elements.historyCarsFilter) elements.historyCarsFilter.value = "";
  loadSearchHistory({ resetPage: true });
}

function loadNextHistoryPage() {
  if (!historyNextPageToken || historyLoading) return;
  historyPageIndex += 1;
  loadSearchHistory();
}

function loadPreviousHistoryPage() {
  if (historyPageIndex === 0 || historyLoading) return;
  historyPageIndex -= 1;
  historyNextPageToken = null;
  loadSearchHistory();
}

async function exportSearchHistoryCsv() {
  if (!hasHistoryUi()) return;

  if (elements.historyExportButton) elements.historyExportButton.disabled = true;
  setHistoryStatus("Preparing CSV export...", "info");

  try {
    const params = historyFilterParams();
    const response = await fetch(`${API_URL}/jobs/history/export.csv?${params.toString()}`, {
      headers: await authHeaders(),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.error || "Failed to export CSV");
    }

    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `skiptrace-search-history-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
    setHistoryStatus("", "info");
  } catch (error) {
    console.error("CSV export failed:", error);
    setHistoryStatus(_friendlyHistoryError(error.message), "error");
  } finally {
    if (elements.historyExportButton) elements.historyExportButton.disabled = false;
  }
}

function _escapeModal(e) {
  if (e.key === "Escape") closeFeedbackModal();
}

function _trapModalFocus(e) {
  if (e.key !== "Tab" || !elements.feedbackModal) return;
  const focusable = [
    ...elements.feedbackModal.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    ),
  ];
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}

function closeFeedbackModal() {
  activeFeedbackJobId = null;
  elements.feedbackModal?.classList.add("is-hidden");
  elements.feedbackModal?.removeEventListener("keydown", _trapModalFocus);
  document.removeEventListener("keydown", _escapeModal);
  if (elements.feedbackEntries) elements.feedbackEntries.innerHTML = "";
  if (elements.feedbackComment) elements.feedbackComment.value = "";
  const counter = document.getElementById("feedbackCommentCounter");
  if (counter) counter.textContent = "0 / 1000";
  _modalTriggerEl?.focus();
  _modalTriggerEl = null;
}

function renderFeedbackEntries(entries) {
  if (!elements.feedbackEntries) return;
  if (!entries.length) {
    elements.feedbackEntries.innerHTML = '<p class="history-empty">No feedback has been submitted yet.</p>';
    return;
  }

  elements.feedbackEntries.innerHTML = entries
    .map(
      (entry) => `
        <div class="feedback-entry">
          <div class="feedback-entry-meta">
            ${escapeHtml(entry.rating || "feedback")} by ${escapeHtml(entry.user_email || entry.user_id || "unknown user")}
            on ${escapeHtml(formatHistoryDate(entry.submitted_at))}
          </div>
          <div>${escapeHtml(entry.comment || "No comment provided.")}</div>
        </div>`
    )
    .join("");
}

async function openFeedbackModal(jobId, triggerEl) {
  _modalTriggerEl = triggerEl || document.activeElement;
  activeFeedbackJobId = jobId;
  const row = historyRows.find((item) => item.job_id === jobId);
  if (elements.feedbackModalTitle) elements.feedbackModalTitle.textContent = "Feedback";
  if (elements.feedbackModalSubtitle) {
    elements.feedbackModalSubtitle.textContent = row
      ? `${row.cars_reference_number || "No CARS reference"} - ${row.full_name || "Unknown name"}`
      : jobId;
  }
  if (elements.feedbackModal) {
    elements.feedbackModal.classList.remove("is-hidden");
    elements.feedbackModal.addEventListener("keydown", _trapModalFocus);
    document.addEventListener("keydown", _escapeModal);
    elements.feedbackModalClose?.focus();
  }
  if (elements.feedbackError) {
    elements.feedbackError.textContent = "";
    elements.feedbackError.classList.add("is-hidden");
  }
  if (elements.feedbackEntries) elements.feedbackEntries.innerHTML = '<p class="history-empty">Loading feedback...</p>';

  try {
    const response = await fetch(`${API_URL}/jobs/${encodeURIComponent(jobId)}/feedback`, {
      headers: await authHeaders(),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.error || "Failed to load feedback");
    }
    const data = await response.json();
    renderFeedbackEntries(data.entries || []);
  } catch (error) {
    console.error("Feedback load failed:", error);
    if (elements.feedbackEntries) {
      elements.feedbackEntries.innerHTML = `<p class="history-empty">${escapeHtml(error.message || "Failed to load feedback")}</p>`;
    }
  }
}

async function submitHistoryFeedback() {
  if (!activeFeedbackJobId || !elements.feedbackSubmitButton) return;

  elements.feedbackSubmitButton.disabled = true;
  elements.feedbackSubmitButton.textContent = "Submitting...";
  try {
    const response = await fetch(`${API_URL}/jobs/${encodeURIComponent(activeFeedbackJobId)}/feedback`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(await authHeaders()),
      },
      body: JSON.stringify({
        rating: elements.feedbackRating?.value || "positive",
        comment: elements.feedbackComment?.value.trim() || "",
      }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.error || "Failed to submit feedback");
    }
    const data = await response.json();
    if (elements.feedbackComment) elements.feedbackComment.value = "";
    await openFeedbackModal(activeFeedbackJobId);
    if (data.row) {
      const idx = historyRows.findIndex((r) => r.job_id === data.row.job_id);
      if (idx !== -1) {
        historyRows[idx] = data.row;
        renderHistoryRows(historyRows);
      }
    }
  } catch (error) {
    console.error("Feedback submit failed:", error);
    if (elements.feedbackError) {
      elements.feedbackError.textContent = error.message || "Failed to submit feedback";
      elements.feedbackError.classList.remove("is-hidden");
    }
  } finally {
    elements.feedbackSubmitButton.disabled = false;
    elements.feedbackSubmitButton.textContent = "Submit Feedback";
  }
}

// ===========================
// Event Listeners
// ===========================
function initEventListeners() {
  // Theme toggle
  elements.darkModeToggle.addEventListener("click", toggleTheme);

  // Tab switching
  elements.tabButtons.forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });

  // Setup link in warning banner (if present)
  if (elements.setupLink) {
    elements.setupLink.addEventListener("click", (e) => {
      e.preventDefault();
      switchTab("setup");
    });
  }

  // Form submission
  elements.form.addEventListener("submit", handleSubmit);

  // Auto-generate email when full name changes
  elements.fullNameInput.addEventListener("input", autoGenerateEmail);
  elements.fullNameInput.addEventListener("blur", autoGenerateEmail);

  // New report buttons (top and bottom)
  elements.newReportButton.addEventListener("click", resetForm);
  if (elements.newReportButtonBottom) {
    elements.newReportButtonBottom.addEventListener("click", resetForm);
  }

  // Retry button
  elements.retryButton.addEventListener("click", resetForm);

  // Drive configuration
  elements.saveDriveButton.addEventListener("click", saveDriveConfiguration);
  elements.clearDriveButton.addEventListener("click", clearDriveConfiguration);

  if (hasHistoryUi()) {
    elements.historyApplyFiltersButton?.addEventListener("click", () => loadSearchHistory({ resetPage: true }));
    elements.historyClearFiltersButton?.addEventListener("click", clearSearchHistoryFilters);
    elements.historyExportButton?.addEventListener("click", exportSearchHistoryCsv);
    elements.historyPrevPageButton?.addEventListener("click", loadPreviousHistoryPage);
    elements.historyNextPageButton?.addEventListener("click", loadNextHistoryPage);
    elements.historyTableBody?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-feedback-job-id]");
      if (button) {
        openFeedbackModal(button.dataset.feedbackJobId, button);
      }
    });
    elements.feedbackModalClose?.addEventListener("click", closeFeedbackModal);
    elements.feedbackModal?.addEventListener("click", (event) => {
      if (event.target === elements.feedbackModal) {
        closeFeedbackModal();
      }
    });
    document.getElementById("feedbackForm")?.addEventListener("submit", (e) => {
      e.preventDefault();
      submitHistoryFeedback();
    });
    elements.feedbackComment?.addEventListener("input", () => {
      const counter = document.getElementById("feedbackCommentCounter");
      if (counter) counter.textContent = `${elements.feedbackComment.value.length} / 1000`;
    });
  }

  // Conditionally init address verification if feature is enabled and module is loaded
  if (
    PlatformConfig.hasFeature("addressVerification") &&
    typeof initAddressVerificationListeners === "function"
  ) {
    initAddressVerificationListeners();
  }
}

// ===========================
// Prefill from Chrome extension (hash #prefill=token — no PII in query string)
// ===========================
function stripHashFromUrl() {
  const url = window.location.pathname + window.location.search;
  window.history.replaceState({}, document.title, url);
}

/**
 * Redeem opaque prefill token from URL fragment (not sent to hosting server on first GET).
 * Replaces legacy ?fullName=&email= query prefill (removed for PIPEDA / audit compliance).
 */
async function populateFromPrefillHash() {
  try {
    const rawHash = window.location.hash;
    if (!rawHash || rawHash.length <= 1) {
      return;
    }

    const hash = rawHash.startsWith("#") ? rawHash.slice(1) : rawHash;
    const params = new URLSearchParams(hash);
    const token = params.get("prefill");
    if (!token) {
      return;
    }

    if (!API_URL) {
      console.warn("Prefill: API_URL not configured");
      stripHashFromUrl();
      return;
    }

    const res = await fetch(`${API_URL}/prefill-session/redeem`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: decodeURIComponent(token) }),
    });

    stripHashFromUrl();

    if (!res.ok) {
      console.warn("Prefill redeem failed:", res.status);
      return;
    }

    const data = await res.json();
    const fullNameInput = document.getElementById("fullName");
    const emailInput = document.getElementById("email");
    const cityInput = document.getElementById("city");
    const companyNameInput = document.getElementById("companyName");
    const provinceInput = document.getElementById("province");

    if (data.full_name && fullNameInput) {
      fullNameInput.value = data.full_name;
    }
    if (data.email && emailInput) {
      emailInput.value = data.email;
    }
    if (data.city && cityInput) {
      cityInput.value = data.city;
    }
    if (data.company_name && companyNameInput) {
      companyNameInput.value = data.company_name;
    }
    if (data.province && provinceInput) {
      provinceInput.value = data.province;
    }
  } catch (error) {
    console.error("Error redeeming prefill token:", error);
    stripHashFromUrl();
  }
}

// ===========================
// Service Account Email Update
// ===========================
function updateServiceAccountEmail() {
  if (!FIREBASE_CONFIG || !FIREBASE_CONFIG.projectId) {
    console.warn(
      "Project ID not available, cannot update service account email"
    );
    return;
  }

  const serviceAccountEmail = `functions-sa@${FIREBASE_CONFIG.projectId}.iam.gserviceaccount.com`;

  // Update the email display
  const emailDiv = document.getElementById("serviceAccountEmail");
  if (emailDiv) {
    emailDiv.textContent = serviceAccountEmail;
  }

  // Update the email in troubleshooting section
  const emailCode = document.getElementById("serviceAccountEmailCode");
  if (emailCode) {
    emailCode.textContent = serviceAccountEmail;
  }
}

// ===========================
// Initialization
// ===========================
async function init() {
  // Load configuration first (from platform.json + firebase-config.json)
  const configLoaded = await loadConfig();
  if (!configLoaded) {
    console.error("Failed to load configuration. Application cannot start.");
    return;
  }

  initSignOutButton();

  // Initialize Firebase authentication
  await initializeAuth();

  initTheme();
  initEventListeners();
  checkDriveConfiguration();
  loadDriveConfiguration();
  updateServiceAccountEmail();

  // Restore history filters from URL and auto-load if present (items 2.8 + 2.9)
  if (hasHistoryUi()) {
    const hadState = _hydrateHistoryFromUrl();
    if (hadState) {
      switchTab("history"); // triggers loadSearchHistory() via !historyLoaded path
    }
  }

  // Prefill from extension (#prefill=token after server-side session create)
  setTimeout(() => {
    populateFromPrefillHash();
  }, 100);
}

// Start the app when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
