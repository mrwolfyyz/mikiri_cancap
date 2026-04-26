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
  const user = await ensureSignedIn();
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

/**
 * Escape a string for use in a double-quoted HTML attribute.
 */
function escapeHtmlAttr(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/'/g, "&#39;");
}

/**
 * True if href is safe for use in <a href> (http/https only).
 */
function isSafeHttpUrl(href) {
  if (!href || typeof href !== "string") return false;
  const t = href.trim();
  if (!t) return false;
  try {
    const u = new URL(t, document.baseURI);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

/**
 * Sanitize markdown [text](url) targets for assistant chat: only http(s) URLs
 * (plus same-document fragments resolved to https by URL()).
 */
function sanitizeMarkdownLinkUrl(url) {
  if (!url || typeof url !== "string") return "#";
  const t = url.trim();
  if (!t) return "#";
  const lower = t.toLowerCase();
  if (
    lower.startsWith("javascript:") ||
    lower.startsWith("data:") ||
    lower.startsWith("vbscript:")
  ) {
    return "#";
  }
  try {
    const u = new URL(t, document.baseURI);
    if (u.protocol === "http:" || u.protocol === "https:") {
      return t;
    }
    return "#";
  } catch {
    return "#";
  }
}
