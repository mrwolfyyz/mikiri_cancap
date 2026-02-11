# =============================================================================
# Firebase Configuration
# =============================================================================
# Configures Firebase project, anonymous authentication, web apps, and hosting.
# Requires google-beta provider for Identity Platform configuration.
# =============================================================================

# -----------------------------------------------------------------------------
# Enable Firebase for the Project
# -----------------------------------------------------------------------------

resource "google_firebase_project" "default" {
  provider = google-beta
  project  = var.project_id

  depends_on = [
    google_project_service.apis["firebase.googleapis.com"],
    time_sleep.api_propagation
  ]
}

# -----------------------------------------------------------------------------
# Enable Anonymous Authentication via Identity Platform
# -----------------------------------------------------------------------------

resource "google_identity_platform_config" "default" {
  provider = google-beta
  project  = var.project_id

  sign_in {
    anonymous {
      enabled = true
    }
    allow_duplicate_emails = false
  }

  depends_on = [
    google_project_service.apis["identitytoolkit.googleapis.com"],
    google_firebase_project.default
  ]
}

# -----------------------------------------------------------------------------
# Firebase Web Apps
# -----------------------------------------------------------------------------

resource "google_firebase_web_app" "skiptrace" {
  provider     = google-beta
  project      = var.project_id
  display_name = "Skip Trace Intelligence"

  depends_on = [google_firebase_project.default]
}

resource "google_firebase_web_app" "origination" {
  provider     = google-beta
  project      = var.project_id
  display_name = "Origination Intelligence"

  depends_on = [google_firebase_project.default]
}

# -----------------------------------------------------------------------------
# Get Firebase Web App Config
# -----------------------------------------------------------------------------

data "google_firebase_web_app_config" "skiptrace" {
  provider   = google-beta
  project    = var.project_id
  web_app_id = google_firebase_web_app.skiptrace.app_id
}

data "google_firebase_web_app_config" "origination" {
  provider   = google-beta
  project    = var.project_id
  web_app_id = google_firebase_web_app.origination.app_id
}

# -----------------------------------------------------------------------------
# Firebase Hosting Sites
# -----------------------------------------------------------------------------

resource "google_firebase_hosting_site" "skiptrace" {
  provider = google-beta
  project  = var.project_id
  site_id  = "${var.project_id}-skiptrace"

  depends_on = [google_firebase_project.default]
}

resource "google_firebase_hosting_site" "origination" {
  provider = google-beta
  project  = var.project_id
  site_id  = "${var.project_id}-origination"

  depends_on = [google_firebase_project.default]
}

# -----------------------------------------------------------------------------
# Generate Firebase Config JSON for Frontends
# -----------------------------------------------------------------------------
# These files are generated during terraform apply and should be committed
# or regenerated before frontend deployment.

resource "local_file" "firebase_config_skiptrace" {
  content = jsonencode({
    apiKey            = data.google_firebase_web_app_config.skiptrace.api_key
    authDomain        = "${var.project_id}.firebaseapp.com"
    projectId         = var.project_id
    storageBucket     = "${var.project_id}.firebasestorage.app"
    messagingSenderId = data.google_firebase_web_app_config.skiptrace.messaging_sender_id
    appId             = google_firebase_web_app.skiptrace.app_id
    apiUrl            = google_cloudfunctions2_function.api_gateway.service_config[0].uri
  })
  filename = "${path.module}/../../../frontend/skiptrace/public/firebase-config.json"

  depends_on = [
    google_firebase_web_app.skiptrace,
    google_cloudfunctions2_function.api_gateway
  ]
}

resource "local_file" "firebase_config_origination" {
  content = jsonencode({
    apiKey            = data.google_firebase_web_app_config.origination.api_key
    authDomain        = "${var.project_id}.firebaseapp.com"
    projectId         = var.project_id
    storageBucket     = "${var.project_id}.firebasestorage.app"
    messagingSenderId = data.google_firebase_web_app_config.origination.messaging_sender_id
    appId             = google_firebase_web_app.origination.app_id
    apiUrl            = google_cloudfunctions2_function.api_gateway.service_config[0].uri
  })
  filename = "${path.module}/../../../frontend/origination/public/firebase-config.json"

  depends_on = [
    google_firebase_web_app.origination,
    google_cloudfunctions2_function.api_gateway
  ]
}

# -----------------------------------------------------------------------------
# Generate Chrome Extension Config
# -----------------------------------------------------------------------------
# This file should be copied to the chrome-extension directory before loading
# the extension in Chrome.

resource "local_file" "chrome_extension_config" {
  content  = <<-EOT
// ============================================================================
// Chrome Extension Configuration
// ============================================================================
// Generated by Terraform. DO NOT edit manually.
// Generated at: ${timestamp()}
// Project: ${var.project_id}
// ============================================================================

const CONFIG = {
  // Skip Trace Intelligence Platform URL
  SKIP_TRACE_INTELLIGENCE_URL: 'https://${google_firebase_hosting_site.skiptrace.site_id}.web.app/index.html',
  
  // Version info for debugging
  VERSION: '1.0.0',
  ENVIRONMENT: 'production',
  PROJECT_ID: '${var.project_id}'
};

// Export for use in background.js
if (typeof window !== 'undefined') {
  window.CONFIG = CONFIG;
}
EOT
  filename = "${path.module}/../../../chrome-extension/config.js"

  depends_on = [
    google_firebase_hosting_site.skiptrace
  ]
}

# -----------------------------------------------------------------------------
# Optional: Firebase App Check (Recommended for Production)
# -----------------------------------------------------------------------------
# Uncomment and configure for production to prevent API key abuse.
# Requires setting up reCAPTCHA v3 in Google Cloud Console.
#
# resource "google_firebase_app_check_recaptcha_v3_config" "skiptrace" {
#   provider    = google-beta
#   project     = var.project_id
#   app_id      = google_firebase_web_app.skiptrace.app_id
#   site_secret = var.recaptcha_site_secret  # Set separately, never in code
#
#   depends_on = [google_firebase_project.default]
# }
#
# resource "google_firebase_app_check_recaptcha_v3_config" "origination" {
#   provider    = google-beta
#   project     = var.project_id
#   app_id      = google_firebase_web_app.origination.app_id
#   site_secret = var.recaptcha_site_secret
#
#   depends_on = [google_firebase_project.default]
# }
