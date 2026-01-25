# =============================================================================
# Vertex AI Search - LinkedIn Data Store
# =============================================================================
# Creates Discovery Engine data store, target site, and search engine for
# LinkedIn profile searches. Uses basic website indexing (not advanced site search)
# to index third-party sites (ca.linkedin.com/in/*).
# =============================================================================

# -----------------------------------------------------------------------------
# Data Store
# -----------------------------------------------------------------------------
# Basic website indexing for third-party sites (LinkedIn)
# CRITICAL: create_advanced_site_search = false enables basic indexing

resource "google_discovery_engine_data_store" "linkedin" {
  project                     = var.project_id
  location                    = "global"
  data_store_id               = "linkedin-search"
  display_name                = "LinkedIn Profile Search"
  industry_vertical           = "GENERIC"
  content_config              = "PUBLIC_WEBSITE"
  solution_types              = ["SOLUTION_TYPE_SEARCH"]
  create_advanced_site_search = false # CRITICAL: Basic indexing for third-party sites

  depends_on = [
    var.api_propagation
  ]
}

# -----------------------------------------------------------------------------
# Target Site
# -----------------------------------------------------------------------------
# Restricts indexing to ca.linkedin.com/in/* profile URLs

resource "google_discovery_engine_target_site" "linkedin_profiles" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.linkedin.data_store_id
  provided_uri_pattern = "ca.linkedin.com/in/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_data_store.linkedin
  ]
}

# -----------------------------------------------------------------------------
# Search Engine
# -----------------------------------------------------------------------------
# Links the data store to a search engine for querying

resource "google_discovery_engine_search_engine" "linkedin" {
  project        = var.project_id
  location       = "global"
  engine_id      = "linkedin-search-engine"
  display_name   = "LinkedIn Search Engine"
  collection_id  = "default_collection"
  data_store_ids = [google_discovery_engine_data_store.linkedin.data_store_id]

  search_engine_config {
    search_tier    = "SEARCH_TIER_ENTERPRISE" # Required for website search
    search_add_ons = []
  }

  depends_on = [
    google_discovery_engine_target_site.linkedin_profiles
  ]
}
