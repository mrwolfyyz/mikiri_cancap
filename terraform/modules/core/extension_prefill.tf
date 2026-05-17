# =============================================================================
# Chrome extension prefill session (shared secret for api_gateway)
# =============================================================================

resource "random_password" "extension_prefill_secret" {
  length  = 48
  special = false
}

resource "random_password" "history_token_secret" {
  length  = 48
  special = false
}
