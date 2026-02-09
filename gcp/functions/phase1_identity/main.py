"""
Phase 1: Identity Resolution Cloud Function

Performs:
- Vertex AI Search queries (precision, recall, LinkedIn)
- Vertex AI Gemini LLM identity resolution
- Location-based name search rerun (if LLM finds different city)
- HIBP breach lookup
- Contactability scoring

Returns identity_bundle for use by Phase 2 functions.
"""

import functions_framework
import os
import json
import re
import traceback
import requests
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Tuple, Optional
from urllib.parse import urlparse
from retry_utils import retry_with_backoff, RetryConfig, EmptyLLMResponseError, RateLimitExhaustedError

# Google Gen AI SDK imports (for Gemini with Google Search grounding support)
from google import genai
from google.genai.types import GenerateContentConfig, Tool, GoogleSearch

# Vertex AI Search import (module-level for faster warm invocations)
from google.cloud import discoveryengine_v1 as discoveryengine

# -------------------------
# Config
# -------------------------
GCP_PROJECT = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
# Use 'global' endpoint for Gemini models - routes to any supported region
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")
HIBP_API_KEY = os.environ.get("HIBP_API_KEY", "")

# Province code to full name mapping
PROVINCE_NAMES = {
    "ON": "Ontario",
    "BC": "British Columbia",
    "AB": "Alberta",
    "QC": "Quebec",
    "MB": "Manitoba",
    "SK": "Saskatchewan",
    "NS": "Nova Scotia",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "PE": "Prince Edward Island",
    "NT": "Northwest Territories",
    "YT": "Yukon",
    "NU": "Nunavut",
}


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str
    source: str
    query_id: str
    query_type: str
    relevance_score: float = 0.0  # 0.0-1.0, relevance score from search results


# -------------------------
# Helper functions
# -------------------------
def email_prefix(email: str) -> str:
    """Extract the local part of an email address."""
    try:
        return email.split("@")[0].strip()
    except Exception:
        return email.strip()


def extract_domain(url: str) -> str:
    """Extract domain from URL, stripping www prefix."""
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def generate_name_variations(full_name: str) -> Tuple[str, Optional[str]]:
    """
    Generate name variations for search queries.
    
    If the name has 3+ parts (indicating a middle name), returns both:
    - The full name
    - A variation with middle and last name only
    
    Args:
        full_name: The full name string
        
    Returns:
        Tuple of (full_name, variation) where variation is None if <3 parts
    """
    if not full_name:
        return full_name, None
    
    parts = [p.strip() for p in full_name.split() if p.strip()]
    
    # If less than 3 parts, no variation needed
    if len(parts) < 3:
        return full_name, None
    
    # For 3+ parts, create middle+last variation
    # Use second-to-last and last parts (middle and last name)
    middle_last = f"{parts[-2]} {parts[-1]}"
    return full_name, middle_last


# -------------------------
# Email domain detection
# -------------------------

COMMON_CANADIAN_EMAIL_DOMAINS = [
    # Global free providers (extremely common in Canada)
    "gmail.com",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "yahoo.com",
    "icloud.com",

    # Canada-specific ISP / telco domains
    "bell.net",
    "sympatico.ca",
    "rogers.com",
    "rogers.ca",
    "shaw.ca",
    "telus.net",
    "videotron.ca",
    "mts.net",        # Manitoba
    "eastlink.ca",    # Atlantic provinces
    "nb.sympatico.ca",  # Older regional Sympatico domains
    "ns.sympatico.ca",
    "qc.sympatico.ca",
    "on.sympatico.ca",

    # Smaller / legacy Canadian consumer ISPs
    "primus.ca",
    "ciaccess.com",
    "execulink.com",
    "persona.ca",
    "nbnet.nb.ca",

    # French-Canada / Québec usage
    "hotmail.ca",
    "live.ca",
    "videotron.qc.ca",

    # Apple localized
    "me.com",
    "mac.com",
    
    # Privacy-oriented (common among tech users)
    "proton.me",
    "protonmail.com",
    "tutanota.com",
    "pm.me",
]


def extract_email_domain(email: str) -> str:
    """Extract domain from email address."""
    try:
        if "@" in email:
            return email.split("@")[1].lower().strip()
        return ""
    except Exception:
        return ""


def is_personal_email_domain(domain: str) -> bool:
    """Check if domain is in the personal email domains list."""
    if not domain:
        return False
    return domain.lower().strip() in COMMON_CANADIAN_EMAIL_DOMAINS


def is_business_email(email: str) -> bool:
    """Check if email is a business email (not a personal email domain)."""
    domain = extract_email_domain(email)
    if not domain:
        return False
    return not is_personal_email_domain(domain)


# -------------------------
# External API calls
# -------------------------

# Cached Vertex AI Search client (lazy singleton)
# Initialized once per Cloud Function instance, reused across invocations.
# Thread-safe for concurrent usage once created (gRPC clients are thread-safe).
# Eager-init in main() before ThreadPoolExecutor to avoid initialization races.
_search_client = None


def _get_search_client():
    """Get or create the cached Vertex AI Search client."""
    global _search_client
    if _search_client is None:
        _search_client = discoveryengine.SearchServiceClient()
    return _search_client


def _vertex_ai_search(engine_id: str, query: str, num: int = 5, label: str = "Search") -> List[Dict[str, str]]:
    """
    Core Vertex AI Search implementation shared by precision, recall, and LinkedIn searches.

    Args:
        engine_id: The Discovery Engine ID to search against
        query: Natural language search query (operators should be stripped before calling)
        num: Maximum number of results (default 5, max 25 for basic indexing)
        label: Label for log messages (e.g., "Precision", "Recall", "LinkedIn")

    Returns:
        List of dicts with keys: url, title, snippet, relevance_score
    """
    num = min(num, 25)

    try:
        client = _get_search_client()

        serving_config = (
            f"projects/{GCP_PROJECT}/locations/global/collections/default_collection"
            f"/engines/{engine_id}/servingConfigs/default_search"
        )

        content_search_spec = discoveryengine.SearchRequest.ContentSearchSpec(
            snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                return_snippet=True
            )
        )

        relevance_score_spec = discoveryengine.SearchRequest.RelevanceScoreSpec(
            return_relevance_score=True
        )

        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=query,
            page_size=num,
            content_search_spec=content_search_spec,
            relevance_score_spec=relevance_score_spec,
        )

        print(f"[Vertex AI Search {label}] Executing search: query={query[:80]}..., serving_config={serving_config}")
        response = client.search(request)

        results = []
        for result in response.results:
            doc = result.document
            derived = doc.derived_struct_data
            url = derived.get("link", "")
            title = derived.get("title", "")

            snippet = ""
            snippets = derived.get("snippets", [])
            if snippets and len(snippets) > 0:
                snippet = snippets[0].get("snippet", "")

            # Note: relevance_score is always 0.0 for basic website indexing
            relevance_score = 0.0

            results.append({
                "url": url,
                "title": title,
                "snippet": snippet,
                "relevance_score": relevance_score,
            })

        print(f"[Vertex AI Search {label}] Returning {len(results)} results")
        return results

    except Exception as e:
        print(f"[Vertex AI Search {label}] Error: {e}")
        print(f"[Vertex AI Search {label}] Traceback: {traceback.format_exc()}")
        return []


def vertex_ai_search_linkedin(query: str, num: int = 5) -> List[Dict[str, str]]:
    """Search LinkedIn profiles using Vertex AI Search."""
    engine_id = os.environ.get("LINKEDIN_ENGINE_ID", "linkedin-search-engine")
    return _vertex_ai_search(engine_id, query, num, label="LinkedIn")


def transform_pse_query_to_natural_language(pse_query: str) -> str:
    """
    Convert search query operators to natural language for Vertex AI Search.

    Vertex AI Search doesn't support query operators like intitle:, intext:, etc.
    This function removes those operators and converts to natural language.

    Handles edge cases:
    - Quoted and unquoted content after operators
    - Nested quotes
    - Escape characters
    - Multiple operators in same query

    Examples:
    - 'intitle:"John Smith" OR intitle:"Smith" Toronto' -> 'John Smith OR Smith Toronto'
    - 'intext:prefix OR "John Smith"' -> 'prefix OR John Smith'
    - 'intitle:John intitle:"Michael Smith"' -> 'John Michael Smith'

    Args:
        pse_query: Query with operators (intitle:, intext:, etc.)

    Returns:
        Natural language query suitable for Vertex AI Search
    """
    # Remove intitle: and intext: operators, keep content
    # Handle quoted content: intitle:"content" -> content
    query = re.sub(r'intitle:"([^"]+)"', r'\1', pse_query)
    # Handle unquoted content: intitle:word -> word
    query = re.sub(r'intitle:(\S+)', r'\1', query)
    # Handle intext: with quotes: intext:"content" -> content
    query = re.sub(r'intext:"([^"]+)"', r'\1', query)
    # Handle intext: without quotes: intext:word -> word
    query = re.sub(r'intext:(\S+)', r'\1', query)
    # Remove any remaining quotes (standalone quoted phrases)
    query = re.sub(r'"([^"]+)"', r'\1', query)
    # Clean up extra whitespace (multiple spaces, tabs, etc.)
    query = ' '.join(query.split())
    return query


def vertex_ai_search_precision(query: str, num: int = 5) -> List[Dict[str, str]]:
    """Search social platforms using Vertex AI Search.
    Transforms search operators (intitle:, intext:) to natural language if present."""
    # Only transform if query contains search operators
    if 'intitle:' in query or 'intext:' in query:
        query = transform_pse_query_to_natural_language(query)
    engine_id = os.environ.get("PRECISION_ENGINE_ID", "precision-search-engine")
    return _vertex_ai_search(engine_id, query, num, label="Precision")


def vertex_ai_search_recall(query: str, num: int = 5) -> List[Dict[str, str]]:
    """Search lifestyle/hobby sites using Vertex AI Search.
    Transforms search operators (intitle:, intext:) to natural language if present."""
    if 'intitle:' in query or 'intext:' in query:
        query = transform_pse_query_to_natural_language(query)
    engine_id = os.environ.get("RECALL_ENGINE_ID", "recall-search-engine")
    return _vertex_ai_search(engine_id, query, num, label="Recall")


def extract_grounding_metadata(response) -> Dict[str, Any]:
    """
    Extract grounding metadata from Google Gen AI SDK response for audit trail.
    """
    metadata = {
        "grounding_sources": [],
        "search_queries": [],
        "search_entry_point": ""
    }

    try:
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]

            if hasattr(candidate, 'grounding_metadata'):
                grounding = candidate.grounding_metadata

                if hasattr(grounding, 'web_search_queries') and grounding.web_search_queries is not None:
                    metadata["search_queries"] = list(grounding.web_search_queries)

                if hasattr(grounding, 'grounding_chunks') and grounding.grounding_chunks is not None:
                    for chunk in grounding.grounding_chunks:
                        if hasattr(chunk, 'web'):
                            metadata["grounding_sources"].append({
                                "url": getattr(chunk.web, 'uri', ''),
                                "title": getattr(chunk.web, 'title', ''),
                                "snippet": ""
                            })

                if hasattr(grounding, 'search_entry_point'):
                    entry_point = grounding.search_entry_point
                    if hasattr(entry_point, 'rendered_content'):
                        metadata["search_entry_point"] = entry_point.rendered_content
                    elif hasattr(entry_point, 'html'):
                        metadata["search_entry_point"] = entry_point.html

    except Exception as e:
        print(f"[Grounding Metadata] Error extracting metadata: {e}")
        import traceback
        traceback.print_exc()

    return metadata


def vertex_ai_score(seed: Dict[str, Any], queries_payload: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Phase 1: Identity resolver LLM call using Gemini with Google Search grounding."""
    if not GCP_PROJECT:
        return {"error": "GCP_PROJECT not set"}

    system_prompt = (
        "You are an evidence-driven resolver. You help skip tracers find and contact debtors.\n"
        "- You also have access to Google Search. Use it to verify ambiguous matches and discover additional information about the person not found in the provided results.\n"
        "- The provided search results may be inaccurate or out of date.\n"
        "- You receive a seed (full_name, email, city, province, company_name) and a list of search queries.\n"
        "- Each query has a type: 'high_precision' or 'high_recall'.\n"
        "- 'high_precision' hits are more likely to be real social profiles and accurate professional profiles.\n"
        "- 'high_recall' hits are broader and noisier; only trust them if they clearly match the seed.\n"
        "Your goals:\n"
        "1) Identify social handles that plausibly belong to the same person as the seed.\n"
        "2) For each handle, output platform (if inferable from URL), handle, url, optional city, and confidence (high/medium/low).\n"
        "3) Optionally infer an aggregated city + confidence. Use all hits to infer the city and confidence.\n"
        "4) Be conservative (especially common name/city matches - without addional corroborating evidence these should not be higher than medium confidence): prefer precision hits; use recall hits only when evidence matches strongly.\n"
        "Also select up to 5 unique, high-quality identity clues (from context or name queries) that clearly refer to the same person as the seed and are not already represented in your top_handles or other parts of the output, and return them in the `identity_clues` array with title, url, snippet, and source_query_id.\n"
        "People sometimes use variations of their names. For example, their middle and last name. Include these when they match other seed information, like city or same/similiar company."
        "Alway use the Google Search tool to search for: <full name> and <city>. When referencing the search results from this search in your rationale, include the source."
        "Return STRICT JSON only.\n"
        "\nReturn JSON with this exact structure:\n"
        '{"top_handles": [{"platform": str, "handle": str, "url": str, "city": str (optional), "confidence": "high"|"medium"|"low"}], '
        '"identity_clues": [{"title": str, "url": str, "snippet": str (optional), "source_query_id": str (optional)}], '
        '"location": {"city": str, "confidence": "high"|"medium"|"low"}, '
        '"rationale": str}\n'
    )

    user_prompt = f"""Analyze the following identity resolution request:

Seed Information:
{json.dumps(seed, indent=2)}

Search Results from {len(queries_payload)} queries:
{json.dumps(queries_payload, indent=2)}

Return valid JSON with all required fields."""
    
    # Initialize Google Gen AI client once, outside retry closure.
    # This avoids re-creating the client (and its gRPC channel) on every retry attempt.
    gemini_client = genai.Client(
        vertexai=True,
        project=GCP_PROJECT,
        location=GCP_LOCATION
    )

    def _call_vertex_ai():
        try:
            # Configure Google Search grounding tool
            google_search_tool = Tool(google_search=GoogleSearch())

            print(f"[Vertex AI] Calling Gemini 2.5 Flash with Google Search grounding...")

            # Generate response with grounding
            # NOTE: Structured output (response_schema) is not compatible with grounding tools.
            # We parse JSON manually from text response, following the same pattern as
            # company_domain_lookup and address_verification.
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_prompt,
                config=GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=[google_search_tool],
                    temperature=0.1,
                )
            )

            # Check for empty/blocked response
            if not response:
                raise EmptyLLMResponseError("Empty response from Vertex AI")

            # Check for blocked content or missing candidates
            if hasattr(response, 'candidates') and not response.candidates:
                block_reason = ""
                if hasattr(response, 'prompt_feedback'):
                    block_reason = getattr(response.prompt_feedback, 'block_reason', '') or ''
                raise EmptyLLMResponseError(
                    f"No candidates returned from Vertex AI (block_reason={block_reason})"
                )

            # Access response.text safely - SDK raises ValueError when
            # candidates are blocked by safety filters
            try:
                response_text = response.text
            except ValueError as e:
                raise EmptyLLMResponseError(f"Could not extract text from response: {e}")

            if not response_text:
                raise EmptyLLMResponseError("Empty response text from Vertex AI")

            # Parse JSON response
            content = response_text.strip()

            # Strip markdown code blocks if present
            if content.startswith("```"):
                lines = content.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                content = "\n".join(lines)

            # Check if content is empty after stripping
            if not content.strip():
                raise EmptyLLMResponseError("Empty content after stripping markdown")

            # Parse JSON - if it fails due to empty/invalid content, treat as retryable
            try:
                result = json.loads(content)
            except json.JSONDecodeError as e:
                # JSON decode errors often indicate empty or malformed responses
                # that should be retried (similar to EmptyLLMResponseError)
                # Check if the error suggests an empty response
                error_msg = str(e).lower()
                if "expecting value" in error_msg or "empty" in error_msg or len(content.strip()) == 0:
                    raise EmptyLLMResponseError(f"JSON decode error (likely empty response): {e}")
                # For other JSON decode errors (malformed JSON), still retry as it might be transient
                raise EmptyLLMResponseError(f"JSON decode error (malformed response): {e}")

            # Validate the response is structurally meaningful (not just {})
            # An empty result across all fields likely means the LLM
            # returned a degenerate response worth retrying
            if (not result.get("top_handles") and
                not result.get("rationale") and
                not result.get("identity_clues") and
                not result.get("location")):
                raise EmptyLLMResponseError(
                    "LLM returned structurally empty JSON (no handles, rationale, clues, or location)"
                )

            # Extract grounding metadata for audit trail
            grounding_metadata = extract_grounding_metadata(response)
            result["_grounding_metadata"] = grounding_metadata

            # Validate required fields
            if "top_handles" not in result:
                result["top_handles"] = []
            if "rationale" not in result:
                result["rationale"] = "Analysis completed but rationale was missing from response."

            # Ensure top_handles is a list
            if not isinstance(result.get("top_handles"), list):
                result["top_handles"] = []

            # Validate confidence enum values for each handle
            for handle in result.get("top_handles", []):
                if "confidence" in handle and handle["confidence"] not in ["high", "medium", "low"]:
                    handle["confidence"] = "medium"
                    print(f"[Vertex AI] Warning: Invalid confidence value, defaulting to 'medium'")

            # Validate identity_clues and location (no schema enforcement with grounding)
            if not isinstance(result.get("identity_clues"), list):
                result["identity_clues"] = []
            if not isinstance(result.get("location"), dict):
                result["location"] = {}

            print(f"[Vertex AI] Successfully analyzed identity results with grounding")
            print(f"[Vertex AI] Grounding sources: {len(grounding_metadata.get('grounding_sources', []))}")
            return result

        except Exception as e:
            print(f"[Vertex AI] Error: {e}")
            raise
    
    try:
        return retry_with_backoff(
            _call_vertex_ai,
            RetryConfig(max_attempts=8, base_delay_seconds=3.0, max_delay_seconds=60.0),
            operation_name="Vertex AI identity scoring"
        )
    except json.JSONDecodeError as e:
        # Try to get content if available
        try:
            return {"raw": str(e)}
        except:
            return {"error": "JSON decode error"}
    except Exception as e:
        # Detect rate-limiting exhaustion and raise specific error so caller
        # can return 429 to the workflow (enabling workflow-level retries)
        error_str = str(e).lower()
        if "resource_exhausted" in error_str or "429" in error_str or "too many requests" in error_str:
            raise RateLimitExhaustedError(f"Vertex AI rate limit exhausted after retries: {e}") from e
        return {"error": str(e)}


def hibp_breaches(email: str) -> List[Dict[str, str]]:
    """Look up data breaches for an email via HIBP API."""
    print(f"[HIBP] Starting lookup for: {email!r}")
    if not HIBP_API_KEY:
        print("[HIBP] No API key set")
        return []

    if not email:
        print("[HIBP] Empty email")
        return []

    url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
    headers = {
        "hibp-api-key": HIBP_API_KEY,
        "user-agent": "BounceBack-GCP/1.0",
    }
    params = {"truncateResponse": "false"}

    def _call_hibp():
        r = requests.get(url, headers=headers, params=params, timeout=8)
        print(f"[HIBP] Status: {r.status_code}")
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return r.json() or []
    
    try:
        data = retry_with_backoff(
            _call_hibp,
            RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=30.0),
            operation_name=f"HIBP breach lookup: {email}"
        )
    except Exception as e:
        print(f"[HIBP] Error after retries: {e}")
        return []

    breaches = []
    for b in data:
        name = b.get("Title") or b.get("Name")
        date = b.get("BreachDate")
        if name:
            breaches.append({"name": name, "date": date})

    print(f"[HIBP] Found {len(breaches)} breaches")
    return breaches


def classify_contactability(
    top_handles: List[Dict[str, Any]],
    breaches: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Deterministically classify contactability based on footprint and breaches."""
    num_social = 0
    for h in top_handles or []:
        if not isinstance(h, dict):
            continue
        conf = (h.get("confidence") or "").lower()
        if conf in ("high", "medium"):
            num_social += 1

    num_breaches = len(breaches or [])

    # Determine buckets
    if num_social <= 1:
        footprint_bucket = "LOW"
    elif 2 <= num_social <= 3:
        footprint_bucket = "MED"
    else:
        footprint_bucket = "HIGH"

    if num_breaches == 0:
        breach_bucket = "NO"
    elif 1 <= num_breaches <= 3:
        breach_bucket = "FEW"
    else:
        breach_bucket = "MANY"

    # Contactability matrix
    matrix = {
        "LOW": {
            "NO": {
                "contactability": "Low",
                "interpretation": "This email has almost no visible online activity and no known breaches, suggesting very limited or infrequent use.",
            },
            "FEW": {
                "contactability": "Low/Unstable",
                "interpretation": "This email appears in a small number of breaches but has little current footprint, suggesting sporadic or historical use rather than an actively monitored address.",
            },
            "MANY": {
                "contactability": "Very low",
                "interpretation": "This email shows many historical breaches but little current footprint, suggesting it was used heavily in the past and may now be abandoned or checked infrequently.",
            },
        },
        "MED": {
            "NO": {
                "contactability": "Medium/Good",
                "interpretation": "This email has a moderate online footprint with no known breaches, consistent with steady but relatively low-volume use.",
            },
            "FEW": {
                "contactability": "Good",
                "interpretation": "This email has a moderate footprint and a small number of breaches, consistent with a typical, actively used address.",
            },
            "MANY": {
                "contactability": "Good, slightly chaotic",
                "interpretation": "This email has a moderate footprint and many breaches, indicating long-term use across a range of services.",
            },
        },
        "HIGH": {
            "NO": {
                "contactability": "Excellent",
                "interpretation": "This email has a large, consistent footprint across accounts with no known breaches, indicating a strongly anchored, actively used address.",
            },
            "FEW": {
                "contactability": "Excellent",
                "interpretation": "This email has a large footprint and a few breaches, characteristic of a long-term address used widely online.",
            },
            "MANY": {
                "contactability": "Extremely high",
                "interpretation": "This email has a large footprint and many breaches, indicating a long-term primary address used extensively across many services.",
            },
        },
    }

    out = matrix[footprint_bucket][breach_bucket].copy()
    out.update({
        "num_social": num_social,
        "num_breaches": num_breaches,
        "footprint_bucket": footprint_bucket,
        "breach_bucket": breach_bucket,
    })
    return out


# -------------------------
# Main function
# -------------------------
@functions_framework.http
def main(request):
    """
    HTTP Cloud Function entry point.
    
    Expects JSON body:
    {
        "job_id": "abc123",
        "email": "john@example.com",
        "full_name": "John Doe",
        "city": "Toronto, ON",  // optional
        "company_name": "Acme Corp"  // optional
    }
    
    Returns identity_bundle JSON.
    """
    # Parse request
    try:
        req_data = request.get_json(silent=True) or {}
    except Exception:
        return {"error": "Invalid JSON"}, 400

    job_id = req_data.get("job_id", "")
    email = req_data.get("email", "").strip()
    full_name = req_data.get("full_name", "").strip()
    city = req_data.get("city", "").strip()
    company_name = req_data.get("company_name", "").strip()
    precision_query = req_data.get("precision_query", "").strip()
    province = req_data.get("province", "").strip()
    generated_names = req_data.get("generated_names", [])

    if not email or not full_name:
        return {"error": "email and full_name are required"}, 400

    prefix = email_prefix(email)

    seed = {
        "email": email,
        "email_prefix": prefix,
        "last_known_city": city,
        "full_name": full_name,
    }

    # Add company_name to seed if provided
    if company_name:
        seed["company_name"] = company_name

    # Add province to seed if provided
    if province:
        province_full = PROVINCE_NAMES.get(province, province)
        seed["province"] = province_full

    print(f"[Phase1] Starting identity resolution for job {job_id}: {full_name} <{email}>")

    try:
        return _run_identity_resolution(job_id, email, full_name, city, province, company_name, seed, prefix,
                                        precision_query, generated_names)
    except RateLimitExhaustedError as e:
        print(f"[Phase1] Returning 429 to workflow for retry: {e}")
        return {"error": str(e), "retryable": True}, 429


def _run_identity_resolution(job_id, email, full_name, city, province, company_name, seed, prefix,
                             precision_query, generated_names):
    """Core identity resolution logic, separated to allow main() to catch rate limit errors."""

    # Eager-init the shared search client before spawning threads
    _get_search_client()

    # -------------------------
    # Build search queries (string construction only, no API calls)
    # -------------------------

    # Initialize name variables (used later for precision query construction)
    name_full, name_variation = generate_name_variations(full_name)

    # 1A. Precision query - social platforms with full name
    if name_variation:
        precision_query_base = f'"{name_full}" OR "{name_variation}"'
    else:
        precision_query_base = f'"{name_full}"'

    # Add location to query (no quotes on city - original behavior)
    if city:
        precision_query_base += f' {city.split(",")[0]}'

    # Provincial LinkedIn query
    provincial_linkedin_query = ""
    if generated_names and province:
        province_full = PROVINCE_NAMES.get(province, province)
        all_names = [full_name] + [n for n in generated_names if n.lower() != full_name.lower()]
        name_parts = ' OR '.join(f'"{name}"' for name in all_names)
        provincial_linkedin_query = f'{name_parts} {province_full}'

    # Company name LinkedIn query
    company_name_linkedin_query = ""
    if company_name:
        company_name_linkedin_query = f'"{full_name}" {company_name}'

    # Recall queries
    recall_query = ""
    recall_2_query = ""
    if len(prefix) >= 4:
        recall_query = f'"{full_name}"'
        recall_2_query = f'{prefix} OR "{full_name}"'

    # -------------------------
    # Execute search queries in parallel
    # -------------------------
    print(f"[Phase1] Running all searches in parallel...")

    # Raw results containers (populated by parallel futures)
    precision_raw = []
    llm_precision_raw = []
    provincial_linkedin_raw = []
    company_name_linkedin_raw = []
    recall_raw = []
    recall_2_raw = []

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {}

        # Always run precision search
        futures["precision"] = executor.submit(vertex_ai_search_precision, precision_query_base, 10)

        # LLM-generated precision query (if provided from query_constructor)
        if precision_query:
            futures["precision_llm"] = executor.submit(vertex_ai_search_precision, precision_query, 10)

        # Provincial LinkedIn search
        if provincial_linkedin_query:
            futures["provincial_linkedin"] = executor.submit(vertex_ai_search_linkedin, provincial_linkedin_query, 10)

        # Company name LinkedIn search
        if company_name_linkedin_query:
            futures["company_linkedin"] = executor.submit(vertex_ai_search_linkedin, company_name_linkedin_query, 10)

        # Recall searches
        if recall_query:
            futures["recall"] = executor.submit(vertex_ai_search_recall, recall_query, 10)
        if recall_2_query:
            futures["recall_2"] = executor.submit(vertex_ai_search_precision, recall_2_query, 10)

        # Collect results (timeout per search to avoid hanging the whole batch)
        for key, future in futures.items():
            try:
                result = future.result(timeout=30)
                if key == "precision":
                    precision_raw = result
                elif key == "precision_llm":
                    llm_precision_raw = result
                elif key == "provincial_linkedin":
                    provincial_linkedin_raw = result
                elif key == "company_linkedin":
                    company_name_linkedin_raw = result
                elif key == "recall":
                    recall_raw = result
                elif key == "recall_2":
                    recall_2_raw = result
            except Exception as e:
                print(f"[Phase1] Search '{key}' failed: {e}")

    print(f"[Phase1] All parallel searches complete")

    # Company LinkedIn fallback: if company search returned 0 results and city is provided,
    # try a city-based LinkedIn search (this depends on the company result, so runs sequentially)
    city_linkedin_raw = []
    if company_name and len(company_name_linkedin_raw) == 0 and city:
        print(f"[Phase1] Company name LinkedIn search returned 0 results - trying city search: {city}")
        city_linkedin_query = f'"{full_name}" {city.split(",")[0]}'
        city_linkedin_raw = vertex_ai_search_linkedin(city_linkedin_query, num=10)
        print(f"[Phase1] City LinkedIn search: {len(city_linkedin_raw)} hits")

    # -------------------------
    # Convert raw results to SearchHit objects
    # -------------------------
    def _to_hits(raw: List[Dict], source: str, query_id: str, query_type: str) -> List[SearchHit]:
        return [
            SearchHit(
                url=h["url"],
                title=h["title"],
                snippet=h["snippet"],
                source=source,
                query_id=query_id,
                query_type=query_type,
                relevance_score=h.get("relevance_score", 0.0),
            )
            for h in raw if h.get("url")
        ]

    precision_hits = _to_hits(precision_raw, "vertex_ai_precision", "precision", "high_precision")
    llm_precision_hits = _to_hits(llm_precision_raw, "vertex_ai_precision", "precision_llm", "high_precision")
    provincial_linkedin_hits = _to_hits(provincial_linkedin_raw, "vertex_ai_linkedin", "provincial_linkedin", "high_precision")
    company_name_linkedin_hits = _to_hits(company_name_linkedin_raw, "vertex_ai_linkedin", "company_name_linkedin", "high_precision")
    recall_hits = _to_hits(recall_raw, "vertex_ai_recall", "recall", "high_recall")
    recall_2_hits = _to_hits(recall_2_raw, "vertex_ai_precision", "recall_2", "high_recall")

    # City LinkedIn fallback hits (if any)
    city_linkedin_hits = _to_hits(city_linkedin_raw, "vertex_ai_linkedin", "city_linkedin", "high_precision")

    # Append LinkedIn hits to precision_hits (so they're included in precision results)
    precision_hits.extend(provincial_linkedin_hits)
    precision_hits.extend(company_name_linkedin_hits)
    precision_hits.extend(city_linkedin_hits)

    print(f"[Phase1] Precision: {len(precision_hits)} hits, LLM Precision: {len(llm_precision_hits)} hits, "
          f"Recall: {len(recall_hits)} hits, Recall 2: {len(recall_2_hits)} hits")

    # Deduplicate hits
    seen = set()
    combined_hits: List[SearchHit] = []
    for hit in precision_hits + llm_precision_hits + recall_hits + recall_2_hits:
        if hit.url not in seen:
            seen.add(hit.url)
            combined_hits.append(hit)

    # Build queries payload for LLM
    queries_payload: List[Dict[str, Any]] = [
        {
            "id": "precision",
            "type": "high_precision",
            "query": precision_query_base,
            "hits": [asdict(h) for h in precision_hits],
        },
        {
            "id": "recall",
            "type": "high_recall",
            "query": recall_query,
            "hits": [asdict(h) for h in recall_hits],
        },
        {
            "id": "recall_2",
            "type": "high_recall",
            "query": recall_2_query,
            "hits": [asdict(h) for h in recall_2_hits],
        },
    ]

    # Add company name LinkedIn query if it was executed
    if company_name_linkedin_query:
        queries_payload.append({
            "id": "company_name_linkedin",
            "type": "high_precision",
            "query": company_name_linkedin_query,
            "hits": [asdict(h) for h in company_name_linkedin_hits],
        })
    
    # Add provincial LinkedIn query if it was executed
    if provincial_linkedin_query:
        queries_payload.append({
            "id": "provincial_linkedin",
            "type": "high_precision",
            "query": provincial_linkedin_query,
            "hits": [asdict(h) for h in provincial_linkedin_hits],
        })

    # Add LLM precision query if it was executed
    if precision_query:
        queries_payload.append({
            "id": "precision_llm",
            "type": "high_precision",
            "query": precision_query,
            "hits": [asdict(h) for h in llm_precision_hits],
        })

    # -------------------------
    # LLM identity scoring + HIBP breach lookup (in parallel)
    # -------------------------
    # These two calls are independent: HIBP doesn't need LLM results and vice versa.
    with ThreadPoolExecutor(max_workers=2) as executor:
        llm_future = executor.submit(vertex_ai_score, seed, queries_payload)
        hibp_future = executor.submit(hibp_breaches, email)

        # Collect LLM result
        try:
            scored = llm_future.result()
            scored_error = scored.get("error")
            grounding_metadata = scored.pop("_grounding_metadata", {})
        except RateLimitExhaustedError as e:
            # Re-raise rate limit errors so main() can return 429
            # This allows the workflow to retry the entire function call
            print(f"[Phase1] Rate limit exhausted for LLM scoring: {e}")
            raise
        except Exception as e:
            scored = {}
            scored_error = str(e)
            grounding_metadata = {}

        # Collect HIBP result
        try:
            breaches = hibp_future.result()
        except Exception as e:
            print(f"[Phase1] HIBP lookup failed: {e}")
            breaches = []

    # -------------------------
    # Location-based name search rerun
    # -------------------------
    llm_location = scored.get("location") or {}
    llm_city = (llm_location.get("city") or "").strip()
    llm_confidence = (llm_location.get("confidence") or "").lower()

    submitted_normalized = city.split(",")[0].strip().lower() if city else ""
    llm_normalized = llm_city.split(",")[0].strip().lower() if llm_city else ""

    if (
        full_name
        and llm_confidence == "high"
        and llm_normalized
        and llm_normalized != submitted_normalized
    ):
        print(f"[Phase1] LLM location '{llm_city}' differs from submitted '{city}' - rerunning precision search")

        new_city_token = llm_city.split(",")[0].strip()
        rerun_query = f'"{full_name}" {new_city_token}'
        rerun_raw = vertex_ai_search_precision(rerun_query, num=10)

        rerun_hits = []
        for h in rerun_raw:
            url = h.get("url")
            if url and url not in seen:
                hit = SearchHit(
                    url=url,
                    title=h.get("title", ""),
                    snippet=h.get("snippet", ""),
                    source="vertex_ai_precision",
                    query_id="precision_rerun",
                    query_type="high_precision",
                    relevance_score=h.get("relevance_score", 0.0),
                )
                rerun_hits.append(hit)
                seen.add(url)
                combined_hits.append(hit)

        queries_payload.append({
            "id": "precision_rerun",
            "type": "high_precision",
            "query": rerun_query,
            "reason": f"LLM high-confidence location '{llm_city}' != submitted '{city}'",
            "hits": [asdict(h) for h in rerun_hits],
        })

        print(f"[Phase1] Added {len(rerun_hits)} new hits from rerun")

    # -------------------------
    # Process identity clues
    # -------------------------
    top_context_hits = scored.get("identity_clues") or []
    handle_urls = {
        h.get("url")
        for h in scored.get("top_handles", [])
        if isinstance(h, dict) and h.get("url")
    }

    url_to_hit = {h.url: h for h in combined_hits}

    normalized_identity_clues = []
    for clue in top_context_hits:
        if not isinstance(clue, dict):
            continue
        url = clue.get("url")
        if not url or url in handle_urls:
            continue
        hit = url_to_hit.get(url)
        if hit is not None:
            clue["title"] = clue.get("title") or hit.title
            clue["snippet"] = hit.snippet
            clue["source_query_id"] = clue.get("source_query_id") or hit.query_id
            clue["source"] = extract_domain(url)
        normalized_identity_clues.append(clue)

    # -------------------------
    # Contactability scoring
    # -------------------------
    persona = classify_contactability(
        scored.get("top_handles", []),
        breaches,
    )

    # -------------------------
    # Build identity bundle
    # -------------------------
    golden_location_city = None
    if isinstance(scored.get("location"), dict):
        golden_location_city = scored["location"].get("city") or None
    if not golden_location_city and city:
        golden_location_city = city

    identity_bundle = {
        "seed": seed,
        "golden_name": full_name,
        "golden_location": golden_location_city,
        "location_confidence": llm_location.get("confidence", "low"),
        "top_handles": scored.get("top_handles", []),
        "identity_clues": normalized_identity_clues,
        "breaches": breaches,
        "contactability": {
            "score": persona.get("contactability"),
            "reason": persona.get("interpretation"),
            "num_social": persona.get("num_social"),
            "num_breaches": persona.get("num_breaches"),
            "footprint_bucket": persona.get("footprint_bucket"),
            "breach_bucket": persona.get("breach_bucket"),
        },
        "rationale": scored.get("rationale", ""),
        "scored": scored,
        "queries": queries_payload,
        "scored_error": scored_error,
        "grounding_metadata": grounding_metadata,
    }

    print(f"[Phase1] Complete - {len(scored.get('top_handles', []))} handles, {len(breaches)} breaches")

    return identity_bundle, 200
