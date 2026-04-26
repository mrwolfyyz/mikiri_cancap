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
# Identity Platform Configuration
# -----------------------------------------------------------------------------

resource "google_identity_platform_config" "default" {
  provider = google-beta
  count    = var.enable_sso ? 1 : 0
  project  = var.project_id

  sign_in {
    allow_duplicate_emails = false
    anonymous {
      enabled = false
    }
    email {
      enabled           = false
      password_required = false
    }
  }

  authorized_domains = [
    "localhost",
    "${var.project_id}.firebaseapp.com",
    "${google_firebase_hosting_site.skiptrace.site_id}.web.app",
    "${google_firebase_hosting_site.skiptrace.site_id}.firebaseapp.com",
    "${google_firebase_hosting_site.origination.site_id}.web.app",
    "${google_firebase_hosting_site.origination.site_id}.firebaseapp.com",
  ]

  depends_on = [
    google_project_service.apis["identitytoolkit.googleapis.com"],
    google_firebase_project.default
  ]
}

data "google_secret_manager_secret_version" "workspace_oauth" {
  provider = google-beta
  count    = var.enable_sso ? 1 : 0
  project  = var.project_id
  secret   = var.google_workspace_oauth_client_secret_id
}

resource "google_identity_platform_default_supported_idp_config" "google" {
  provider      = google-beta
  count         = var.enable_sso ? 1 : 0
  project       = var.project_id
  idp_id        = "google.com"
  enabled       = true
  client_id     = var.google_workspace_oauth_client_id
  client_secret = data.google_secret_manager_secret_version.workspace_oauth[0].secret_data

  depends_on = [google_identity_platform_config.default]
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
    requireSso        = var.enable_sso
    workspaceDomain   = var.workspace_domain
    recaptchaSiteKey  = try(google_recaptcha_enterprise_key.web[0].name, "")
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
    requireSso        = var.enable_sso
    workspaceDomain   = var.workspace_domain
    recaptchaSiteKey  = try(google_recaptcha_enterprise_key.web[0].name, "")
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

  // API Gateway (prefill session — same as firebase-config apiUrl)
  API_GATEWAY_URL: '${google_cloudfunctions2_function.api_gateway.service_config[0].uri}',

  // Shared secret for POST /extension/prefill-session (do not commit this file)
  EXTENSION_PREFILL_SECRET: '${random_password.extension_prefill_secret.result}',
  
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
    google_firebase_hosting_site.skiptrace,
    google_cloudfunctions2_function.api_gateway,
    random_password.extension_prefill_secret,
  ]
}

# -----------------------------------------------------------------------------
# Firebase App Check (reCAPTCHA Enterprise)
# -----------------------------------------------------------------------------

resource "google_recaptcha_enterprise_key" "web" {
  provider     = google-beta
  count        = var.enable_sso ? 1 : 0
  display_name = "mikiri-app-check-web"
  project      = var.project_id

  web_settings {
    integration_type  = "SCORE"
    allow_all_domains = false
    allowed_domains = [
      "${google_firebase_hosting_site.skiptrace.site_id}.web.app",
      "${google_firebase_hosting_site.skiptrace.site_id}.firebaseapp.com",
      "${google_firebase_hosting_site.origination.site_id}.web.app",
      "${google_firebase_hosting_site.origination.site_id}.firebaseapp.com",
    ]
  }

  depends_on = [google_project_service.apis["recaptchaenterprise.googleapis.com"]]
}

resource "google_firebase_app_check_recaptcha_enterprise_config" "skiptrace" {
  provider  = google-beta
  count     = var.enable_sso ? 1 : 0
  project   = var.project_id
  app_id    = google_firebase_web_app.skiptrace.app_id
  site_key  = google_recaptcha_enterprise_key.web[0].name
  token_ttl = "3600s"

  depends_on = [google_firebase_project.default]
}

resource "google_firebase_app_check_recaptcha_enterprise_config" "origination" {
  provider  = google-beta
  count     = var.enable_sso ? 1 : 0
  project   = var.project_id
  app_id    = google_firebase_web_app.origination.app_id
  site_key  = google_recaptcha_enterprise_key.web[0].name
  token_ttl = "3600s"

  depends_on = [google_firebase_project.default]
}

# -----------------------------------------------------------------------------
# Firebase App Check Enforcement on Firestore
# -----------------------------------------------------------------------------
# Frontends read job/chat documents directly from Firestore, so the strict
# baseline must enforce App Check on Firestore too - not only on the API
# gateway. Without this, a holder of a valid Firebase ID token could read
# their Firestore documents from any origin / tool, bypassing the
# reCAPTCHA-Enterprise origin binding that protects the API path.
#
# Gated on app_check_enforced so enforcement and the reCAPTCHA key/config
# resources above can be toggled independently from SSO in future phases.

resource "google_firebase_app_check_service_config" "firestore" {
  provider         = google-beta
  count            = var.app_check_enforced ? 1 : 0
  project          = var.project_id
  service_id       = "firestore.googleapis.com"
  enforcement_mode = "ENFORCED"

  depends_on = [
    google_project_service.apis["firebaseappcheck.googleapis.com"],
    google_firebase_app_check_recaptcha_enterprise_config.skiptrace,
    google_firebase_app_check_recaptcha_enterprise_config.origination,
  ]
}

