# =============================================================================
# Vertex AI Search - Precision Search (Social Platforms)
# =============================================================================
# Creates Discovery Engine data store, target sites, and search engine for
# social platform searches. Uses basic website indexing (not advanced site search)
# to index social platforms (Instagram, Twitter, Facebook, GitHub, etc.).
# Target sites created sequentially with depends_on to avoid API rate limits.
# =============================================================================

# -----------------------------------------------------------------------------
# Data Store
# -----------------------------------------------------------------------------
# Basic website indexing for third-party sites (precision social platforms)
# CRITICAL: create_advanced_site_search = false enables basic indexing

resource "google_discovery_engine_data_store" "precision" {
  project                     = var.project_id
  location                    = "global"
  data_store_id               = "precision-search"
  display_name                = "Precision Search - Social Platforms"
  industry_vertical           = "GENERIC"
  content_config              = "PUBLIC_WEBSITE"
  solution_types              = ["SOLUTION_TYPE_SEARCH"]
  create_advanced_site_search = false # CRITICAL: Basic indexing for third-party sites

  depends_on = [
    var.api_propagation
  ]
}

# -----------------------------------------------------------------------------
# Target Sites (sequential depends_on to avoid rate limits)
# -----------------------------------------------------------------------------

resource "google_discovery_engine_target_site" "precision_facebook" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.precision.data_store_id
  provided_uri_pattern = "facebook.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_data_store.precision
  ]
}

resource "google_discovery_engine_target_site" "precision_instagram" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.precision.data_store_id
  provided_uri_pattern = "instagram.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.precision_facebook
  ]
}

resource "google_discovery_engine_target_site" "precision_twitter" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.precision.data_store_id
  provided_uri_pattern = "twitter.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.precision_instagram
  ]
}

resource "google_discovery_engine_target_site" "precision_x" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.precision.data_store_id
  provided_uri_pattern = "x.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.precision_twitter
  ]
}

resource "google_discovery_engine_target_site" "precision_github" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.precision.data_store_id
  provided_uri_pattern = "github.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.precision_x
  ]
}

resource "google_discovery_engine_target_site" "precision_tiktok" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.precision.data_store_id
  provided_uri_pattern = "tiktok.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.precision_github
  ]
}

resource "google_discovery_engine_target_site" "precision_linkedin" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.precision.data_store_id
  provided_uri_pattern = "linkedin.com/in/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.precision_tiktok
  ]
}

resource "google_discovery_engine_target_site" "precision_gravatar" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.precision.data_store_id
  provided_uri_pattern = "gravatar.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.precision_linkedin
  ]
}

resource "google_discovery_engine_target_site" "precision_youtube" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.precision.data_store_id
  provided_uri_pattern = "www.youtube.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.precision_gravatar
  ]
}

resource "google_discovery_engine_target_site" "precision_pressreader" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.precision.data_store_id
  provided_uri_pattern = "www.pressreader.com/canada/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.precision_youtube
  ]
}

resource "google_discovery_engine_target_site" "precision_legacy" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.precision.data_store_id
  provided_uri_pattern = "legacy.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.precision_pressreader
  ]
}

resource "google_discovery_engine_target_site" "precision_nationalpost" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.precision.data_store_id
  provided_uri_pattern = "nationalpost.remembering.ca/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.precision_legacy
  ]
}

# -----------------------------------------------------------------------------
# Search Engine
# -----------------------------------------------------------------------------
# Links the data store to a search engine for querying

resource "google_discovery_engine_search_engine" "precision" {
  project        = var.project_id
  location       = "global"
  engine_id      = "precision-search-engine"
  display_name   = "Precision Search Engine"
  collection_id  = "default_collection"
  data_store_ids = [google_discovery_engine_data_store.precision.data_store_id]

  search_engine_config {
    search_tier    = "SEARCH_TIER_ENTERPRISE" # Required for website search
    search_add_ons = []
  }

  depends_on = [
    google_discovery_engine_target_site.precision_nationalpost
  ]
}
