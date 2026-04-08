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

// ===========================
// DOM Elements
// ===========================
const elements = {
  // Tabs
  tabButtons: document.querySelectorAll(".tab-button"),
  tabContents: document.querySelectorAll(".tab-content"),

  // Form
  form: document.getElementById("investigationForm"),
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
    // Initialize Firebase
    firebase.initializeApp(FIREBASE_CONFIG);

    // Sign in anonymously
    await firebase.auth().signInAnonymously();
  } catch (error) {
    console.error("Authentication error:", error);
    // Show user-friendly error message
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
}

// ===========================
// Form Validation
// ===========================
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
async function submitInvestigation(fullName, city, email, companyName = "", province = "") {
  const driveFolderId = storage.getDriveFolderId() || "";

  const token = await getAuthToken(); // Get fresh token

  const requestBody = {
    full_name: fullName.trim(),
    city: city.trim(),
    email: email.trim(),
    drive_folder_id: driveFolderId,
    company_name: companyName.trim(),
    province: province.trim(),
  };

  const endpoint = PlatformConfig.get("investigateEndpoint");

  const response = await fetch(`${API_URL}${endpoint}`, {
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
        const retryResponse = await fetch(
          `${API_URL}${endpoint}`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${newToken}`,
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
  const token = await getAuthToken(); // Get fresh token

  const response = await fetch(`${API_URL}/jobs/${jobId}`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    if (response.status === 401) {
      // Token expired - try to refresh
      try {
        const newToken = await getAuthToken();
        const retryResponse = await fetch(`${API_URL}/jobs/${jobId}`, {
          headers: {
            Authorization: `Bearer ${newToken}`,
          },
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
    const getToken = () => getAuthToken();
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
      const token = await getAuthToken();
      const response = await fetch(`${API_URL}/jobs/${jobId}/feedback`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
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
  showFieldError(elements.fullNameError, null);
  showFieldError(elements.cityError, null);
  showFieldError(elements.provinceError, null);
  showFieldError(elements.emailError, null);
  showFieldError(elements.companyNameError, null);

  // Get values
  const fullName = elements.fullNameInput.value;
  const city = elements.cityInput.value;
  const province = elements.provinceInput.value;
  const email = elements.emailInput.value;
  const companyName = elements.companyNameInput.value;

  // Validate
  const fullNameError = validateFullName(fullName);
  const cityError = validateCity(city);
  const provinceError = validateProvince(province);
  const emailError = validateEmail(email);
  const companyNameError = validateCompanyName(companyName);

  let hasErrors = false;

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
      province
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

  // Initialize Firebase authentication
  await initializeAuth();

  initTheme();
  initEventListeners();
  checkDriveConfiguration();
  loadDriveConfiguration();
  updateServiceAccountEmail();

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
