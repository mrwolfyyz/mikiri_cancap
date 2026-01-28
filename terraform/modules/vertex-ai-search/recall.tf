# =============================================================================
# Vertex AI Search - Recall Search (Lifestyle and Hobby Sites)
# =============================================================================
# Creates Discovery Engine data store, target sites, and search engine for
# lifestyle and hobby site searches. Uses basic website indexing (not advanced site search)
# to index lifestyle/hobby sites (AllTrails, Chess.com, Goodreads, Flickr, etc.).
# Target sites created sequentially with depends_on to avoid API rate limits.
# =============================================================================

# -----------------------------------------------------------------------------
# Data Store
# -----------------------------------------------------------------------------
# Basic website indexing for third-party sites (lifestyle/hobby platforms)
# CRITICAL: create_advanced_site_search = false enables basic indexing

resource "google_discovery_engine_data_store" "recall" {
  project                     = var.project_id
  location                    = "global"
  data_store_id               = "recall-search"
  display_name                = "Recall Search - Lifestyle Sites"
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

resource "google_discovery_engine_target_site" "recall_federalcorporation" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "federalcorporation.ca/corporation/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_data_store.recall
  ]
}

resource "google_discovery_engine_target_site" "recall_canadacompanyregistry" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "www.canadacompanyregistry.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_federalcorporation
  ]
}

resource "google_discovery_engine_target_site" "recall_contactout" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "*.contactout.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_canadacompanyregistry
  ]
}

resource "google_discovery_engine_target_site" "recall_houzz" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "houzz.com/professionals/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_contactout
  ]
}

resource "google_discovery_engine_target_site" "recall_myhockeyrankings" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "*.myhockeyrankings.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_houzz
  ]
}

resource "google_discovery_engine_target_site" "recall_alltrails" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "alltrails.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_myhockeyrankings
  ]
}

resource "google_discovery_engine_target_site" "recall_chess" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "chess.com/member/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_alltrails
  ]
}

resource "google_discovery_engine_target_site" "recall_discogs" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "discogs.com/user/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_chess
  ]
}

resource "google_discovery_engine_target_site" "recall_fiverr" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "fiverr.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_discogs
  ]
}

resource "google_discovery_engine_target_site" "recall_flickr" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "flickr.com/people/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_fiverr
  ]
}

resource "google_discovery_engine_target_site" "recall_github" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "github.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_flickr
  ]
}

resource "google_discovery_engine_target_site" "recall_goodreads" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "goodreads.com/user/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_github
  ]
}

resource "google_discovery_engine_target_site" "recall_gravatar" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "gravatar.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_goodreads
  ]
}

resource "google_discovery_engine_target_site" "recall_inaturalist" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "inaturalist.org/people/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_gravatar
  ]
}

resource "google_discovery_engine_target_site" "recall_poshmark" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "poshmark.ca/closet/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_inaturalist
  ]
}

resource "google_discovery_engine_target_site" "recall_ravelry" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "ravelry.com/designers/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_poshmark
  ]
}

resource "google_discovery_engine_target_site" "recall_telegram" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "t.me/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_ravelry
  ]
}

resource "google_discovery_engine_target_site" "recall_theknot" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "theknot.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_telegram
  ]
}

resource "google_discovery_engine_target_site" "recall_untappd" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "untappd.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_theknot
  ]
}

resource "google_discovery_engine_target_site" "recall_upwork" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "upwork.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_untappd
  ]
}

resource "google_discovery_engine_target_site" "recall_varagesale" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "varagesale.com/store/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_upwork
  ]
}

resource "google_discovery_engine_target_site" "recall_zola" {
  project              = var.project_id
  location             = "global"
  data_store_id        = google_discovery_engine_data_store.recall.data_store_id
  provided_uri_pattern = "zola.com/*"
  type                 = "INCLUDE"
  exact_match          = false

  depends_on = [
    google_discovery_engine_target_site.recall_varagesale
  ]
}

# -----------------------------------------------------------------------------
# Search Engine
# -----------------------------------------------------------------------------
# Links the data store to a search engine for querying

resource "google_discovery_engine_search_engine" "recall" {
  project        = var.project_id
  location       = "global"
  engine_id      = "recall-search-engine"
  display_name   = "Recall Search Engine"
  collection_id  = "default_collection"
  data_store_ids = [google_discovery_engine_data_store.recall.data_store_id]

  search_engine_config {
    search_tier    = "SEARCH_TIER_ENTERPRISE" # Required for website search
    search_add_ons = []
  }

  depends_on = [
    google_discovery_engine_target_site.recall_zola
  ]
}
