// =============================================================================
// Shared Utilities - Common functions used across multiple pages
// =============================================================================
// THIS FILE IS THE SOURCE OF TRUTH
// Copied to platform directories by scripts/prepare-frontend.sh
//
// Requires: Firebase SDK (firebase-auth-compat.js must be loaded first)
// =============================================================================

/**
 * Get current valid Firebase ID token (gets fresh token on each call).
 * Firebase SDK handles caching internally, so this doesn't make unnecessary network calls.
 */
async function getAuthToken() {
  const user = firebase.auth().currentUser;
  if (!user) {
    throw new Error("User not authenticated");
  }
  return await user.getIdToken(true);
}

/**
 * Escape HTML special characters to prevent XSS.
 */
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}
