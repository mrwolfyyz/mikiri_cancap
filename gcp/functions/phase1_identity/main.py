"""
Phase 1: Identity Resolution Cloud Function

Performs:
- Google Custom Search API (PSE) searches (precision, context, recall, name)
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
import requests
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Tuple, Optional
from urllib.parse import urlparse
from retry_utils import retry_with_backoff, RetryConfig, EmptyLLMResponseError

# Vertex AI imports
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

# -------------------------
# Config
# -------------------------
GOOGLE_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
GOOGLE_SEARCH_CX = os.environ.get("GOOGLE_SEARCH_CX", "")
PRECISION_PSE_CX = os.environ.get("PRECISION_PSE_CX", "")
RECALL_PSE_CX = os.environ.get("RECALL_PSE_CX", "")
RECALL_PSE_CX_2 = os.environ.get("RECALL_PSE_CX_2", "")
LINKEDIN_PSE_CX = os.environ.get("LINKEDIN_PSE_CX", "")
GCP_PROJECT = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
# Use 'global' endpoint for Gemini models - routes to any supported region
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")
HIBP_API_KEY = os.environ.get("HIBP_API_KEY", "")

# Social platforms for precision queries
PLATFORMS = [
    "instagram.com",
    "twitter.com",
    "x.com",
    "facebook.com",
   
    "github.com",
    "tiktok.com",
    "linkedin.com/in",
   
   
   
    "gravatar.com",
   
   
    
    
    
    "chess.com/member",
   
   
  
    
    "theknot.com",
    "zola.com",
]

# Lifestyle sites for recall queries
LIFESTYLE_SITES = [
    "inaturalist.org/people", "alltrails.com", "github.com", "gravatar.com",
    "ravelry.com/designers",  "varagesale.com/store", 
    "poshmark.ca/closet", "chess.com/member",  
     "flickr.com/people", "goodreads.com/user", 
    "discogs.com/user", "untappd.com", "fiverr.com", "upwork.com",
    "t.me",  "theknot.com", "zola.com",
]


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str
    source: str
    query_id: str
    query_type: str
    relevance_score: float = 0.0  # 0.0-1.0, default 0.0 for PSE results


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
    "icloud.com",
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
def google_search(query: str, num: int = 5) -> List[Dict[str, str]]:
    """Call Google Custom Search API (PSE) with retry logic."""
    if not GOOGLE_SEARCH_API_KEY:
        print("[Google Search] No API key set")
        return []
    if not GOOGLE_SEARCH_CX:
        print("[Google Search] No Search Engine ID (CX) set")
        return []
    
    # Custom Search API max is 10 results per request, so we may need pagination
    # For now, we'll request up to 10 results (the API limit)
    num = min(num, 10)
    
    endpoint = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": GOOGLE_SEARCH_CX,
        "q": query,
        "num": num
    }
    
    def _call_google_search():
        r = requests.get(endpoint, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in (data.get("items", []) or [])[:num]:
            results.append({
                "url": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            })
        return results
    
    try:
        return retry_with_backoff(
            _call_google_search,
            RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=30.0),
            operation_name=f"Google Search: {query[:50]}"
        )
    except Exception as e:
        print(f"[Google Search] Error after retries: {e}")
        return []


def google_search_precision(query: str, num: int = 5) -> List[Dict[str, str]]:
    """Call Google Custom Search API (PSE) with retry logic for precision searches."""
    if not GOOGLE_SEARCH_API_KEY:
        print("[Google Search Precision] No API key set")
        return []
    if not PRECISION_PSE_CX:
        print("[Google Search Precision] No Precision Search Engine ID (CX) set")
        return []
    
    # Custom Search API max is 10 results per request, so we may need pagination
    # For now, we'll request up to 10 results (the API limit)
    num = min(num, 10)
    
    endpoint = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": PRECISION_PSE_CX,
        "q": query,
        "num": num
    }
    
    def _call_google_search():
        r = requests.get(endpoint, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in (data.get("items", []) or [])[:num]:
            results.append({
                "url": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            })
        return results
    
    try:
        return retry_with_backoff(
            _call_google_search,
            RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=30.0),
            operation_name=f"Google Search Precision: {query[:50]}"
        )
    except Exception as e:
        print(f"[Google Search Precision] Error after retries: {e}")
        return []


def google_search_recall(query: str, num: int = 5) -> List[Dict[str, str]]:
    """Call Google Custom Search API (PSE) with retry logic for recall searches."""
    if not GOOGLE_SEARCH_API_KEY:
        print("[Google Search Recall] No API key set")
        return []
    if not RECALL_PSE_CX:
        print("[Google Search Recall] No Recall Search Engine ID (CX) set")
        return []
    
    # Custom Search API max is 10 results per request, so we may need pagination
    # For now, we'll request up to 10 results (the API limit)
    num = min(num, 10)
    
    endpoint = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": RECALL_PSE_CX,
        "q": query,
        "num": num
    }
    
    def _call_google_search():
        r = requests.get(endpoint, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in (data.get("items", []) or [])[:num]:
            results.append({
                "url": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            })
        return results
    
    try:
        return retry_with_backoff(
            _call_google_search,
            RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=30.0),
            operation_name=f"Google Search Recall: {query[:50]}"
        )
    except Exception as e:
        print(f"[Google Search Recall] Error after retries: {e}")
        return []


def google_search_recall_v2(query: str, num: int = 5) -> List[Dict[str, str]]:
    """
    Recall search with Vertex AI Search, falling back to PSE.

    Returns same dict format as PSE for compatibility with existing call site
    that converts to SearchHit.

    Set RECALL_USE_VERTEX_AI=true to enable Vertex AI Search.
    """
    use_vertex = os.environ.get("RECALL_USE_VERTEX_AI", "false").lower() == "true"
    print(f"[Recall Search v2] use_vertex={use_vertex}, query={query[:100]}")

    if use_vertex:
        results = vertex_ai_search_recall(query, num)
        print(f"[Recall Search v2] Vertex AI Search returned {len(results)} results")
        if results:
            # Mark results as from Vertex AI Search
            for r in results:
                r["_source"] = "vertex_ai_recall"  # Internal marker
            print(f"[Recall Search v2] Returning Vertex AI Search results")
            return results
        print("[Recall Search v2] Vertex AI Search returned no results, falling back to PSE")

    # Fall back to existing PSE function
    pse_results = google_search_recall(query, num)
    # Add relevance_score for consistency and mark as PSE
    return [
        {
            "url": r["url"],
            "title": r["title"],
            "snippet": r["snippet"],
            "relevance_score": 0.0,  # PSE doesn't provide scores
            "_source": "pse"  # Internal marker
        }
        for r in pse_results
    ]


def google_search_recall_2(query: str, num: int = 5) -> List[Dict[str, str]]:
    """Call Google Custom Search API (PSE) with retry logic for additional recall searches."""
    if not GOOGLE_SEARCH_API_KEY:
        print("[Google Search Recall 2] No API key set")
        return []
    if not RECALL_PSE_CX_2:
        print("[Google Search Recall 2] No Recall Search Engine ID (CX) set")
        return []
    
    # Custom Search API max is 10 results per request, so we may need pagination
    # For now, we'll request up to 10 results (the API limit)
    num = min(num, 10)
    
    endpoint = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": RECALL_PSE_CX_2,
        "q": query,
        "num": num
    }
    
    def _call_google_search():
        r = requests.get(endpoint, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in (data.get("items", []) or [])[:num]:
            results.append({
                "url": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            })
        return results
    
    try:
        return retry_with_backoff(
            _call_google_search,
            RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=30.0),
            operation_name=f"Google Search Recall 2: {query[:50]}"
        )
    except Exception as e:
        print(f"[Google Search Recall 2] Error after retries: {e}")
        return []


def google_search_linkedin(query: str, num: int = 5) -> List[Dict[str, str]]:
    """Call Google Custom Search API (PSE) with retry logic for LinkedIn searches."""
    if not GOOGLE_SEARCH_API_KEY:
        print("[Google Search LinkedIn] No API key set")
        return []
    if not LINKEDIN_PSE_CX:
        print("[Google Search LinkedIn] No LinkedIn Search Engine ID (CX) set")
        return []
    
    # Custom Search API max is 10 results per request, so we may need pagination
    # For now, we'll request up to 10 results (the API limit)
    num = min(num, 10)
    
    endpoint = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": LINKEDIN_PSE_CX,
        "q": query,
        "num": num
    }
    
    def _call_google_search():
        r = requests.get(endpoint, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in (data.get("items", []) or [])[:num]:
            results.append({
                "url": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            })
        return results
    
    try:
        return retry_with_backoff(
            _call_google_search,
            RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=30.0),
            operation_name=f"Google Search LinkedIn: {query[:50]}"
        )
    except Exception as e:
        print(f"[Google Search LinkedIn] Error after retries: {e}")
        return []


def vertex_ai_search_linkedin(query: str, num: int = 5) -> List[Dict[str, str]]:
    """
    Search LinkedIn profiles using Vertex AI Search.
    
    Returns same interface as google_search_linkedin() for drop-in replacement.
    Conversion to SearchHit happens at call site (same as PSE).
    
    Args:
        query: Natural language search query (e.g., "John Smith CanCap Group")
        num: Maximum number of results (default 5, max 25 for basic indexing)
    
    Returns:
        List of dicts with keys: url, title, snippet, relevance_score
    """
    # Basic website indexing max is 25 results
    num = min(num, 25)
    
    # Get configuration from environment
    engine_id = os.environ.get("LINKEDIN_ENGINE_ID", "linkedin-search-engine")
    
    try:
        from google.cloud import discoveryengine_v1 as discoveryengine
        
        # Client setup - global location uses default endpoint
        client = discoveryengine.SearchServiceClient()
        
        # Serving config path - use engines path with default_search
        serving_config = (
            f"projects/{GCP_PROJECT}/locations/global/collections/default_collection"
            f"/engines/{engine_id}/servingConfigs/default_search"
        )
        
        # Content search spec - request snippets
        content_search_spec = discoveryengine.SearchRequest.ContentSearchSpec(
            snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                return_snippet=True
            )
        )
        
        # Relevance score spec - request scores
        relevance_score_spec = discoveryengine.SearchRequest.RelevanceScoreSpec(
            return_relevance_score=True
        )
        
        # Build request
        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=query,
            page_size=num,
            content_search_spec=content_search_spec,
            relevance_score_spec=relevance_score_spec,
        )
        
        # Execute search
        print(f"[Vertex AI Search LinkedIn] Executing search: query={query}, serving_config={serving_config}")
        response = client.search(request)
        print(f"[Vertex AI Search LinkedIn] Search completed: {len(response.results)} results returned")
        
        # Transform results to same format as PSE (List[Dict])
        results = []
        for idx, result in enumerate(response.results):
            # Extract fields from derivedStructData
            doc = result.document
            derived = doc.derived_struct_data
            url = derived.get("link", "")
            title = derived.get("title", "")
            
            # Extract snippet - may be in snippets array
            snippet = ""
            snippets = derived.get("snippets", [])
            if snippets and len(snippets) > 0:
                snippet = snippets[0].get("snippet", "")
            
            # Extract relevance score (additional field, not in PSE)
            # Note: model_scores and rank_signals are empty for basic website indexing
            # So relevance_score will always be 0.0
            relevance_score = 0.0
            
            # Return same dict format as PSE, plus relevance_score
            results.append({
                "url": url,
                "title": title,
                "snippet": snippet,
                "relevance_score": relevance_score,
            })
        
        print(f"[Vertex AI Search LinkedIn] Returning {len(results)} results with relevance_scores: {[r.get('relevance_score', 0.0) for r in results]}")
        return results
        
    except Exception as e:
        import traceback
        print(f"[Vertex AI Search LinkedIn] Error: {e}")
        print(f"[Vertex AI Search LinkedIn] Traceback: {traceback.format_exc()}")
        return []


def transform_pse_query_to_natural_language(pse_query: str) -> str:
    """
    Convert PSE-style query operators to natural language for Vertex AI Search.

    Vertex AI Search doesn't support PSE operators like intitle:, intext:, etc.
    This function removes those operators and converts to natural language.

    Handles edge cases:
    - Quoted and unquoted content after operators
    - Nested quotes (though PSE queries typically don't have these)
    - Escape characters
    - Multiple operators in same query

    Examples:
    - 'intitle:"John Smith" OR intitle:"Smith" Toronto' -> 'John Smith OR Smith Toronto'
    - 'intext:prefix OR "John Smith"' -> 'prefix OR John Smith'
    - 'intitle:John intitle:"Michael Smith"' -> 'John Michael Smith'

    Args:
        pse_query: PSE-style query with operators (intitle:, intext:, etc.)

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
    """
    Search social platforms using Vertex AI Search.

    Note: The query parameter may contain PSE operators (intitle:, intext:).
    These will be transformed to natural language before sending to Vertex AI.

    Returns same interface as google_search_precision() for drop-in replacement.
    Conversion to SearchHit happens at call site (same as PSE).

    Args:
        query: PSE-style query (will be transformed) or natural language query
        num: Maximum number of results (default 5, max 25 for basic indexing)

    Returns:
        List of dicts with keys: url, title, snippet, relevance_score
    """
    # Only transform if query contains PSE operators (intitle:, intext:)
    # If no PSE operators, use query as-is to preserve quotes and other formatting
    if 'intitle:' in query or 'intext:' in query:
        query_nl = transform_pse_query_to_natural_language(query)
    else:
        query_nl = query  # Preserve quotes and original formatting
    num = min(num, 25)
    engine_id = os.environ.get("PRECISION_ENGINE_ID", "precision-search-engine")

    try:
        from google.cloud import discoveryengine_v1 as discoveryengine

        client = discoveryengine.SearchServiceClient()
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
            query=query_nl,
            page_size=num,
            content_search_spec=content_search_spec,
            relevance_score_spec=relevance_score_spec,
        )
        print(f"[Vertex AI Search Precision] Executing search: query={query_nl[:80]}..., serving_config={serving_config}")
        response = client.search(request)
        print(f"[Vertex AI Search Precision] Search completed: {len(response.results)} results returned")

        results = []
        for result in response.results:
            doc = result.document
            derived = doc.derived_struct_data
            url = derived.get("link", "")
            title = derived.get("title", "")
            snippet = ""
            snippets = derived.get("snippets", [])
            if snippets:
                snippet = snippets[0].get("snippet", "")
            relevance_score = 0.0
            results.append({
                "url": url,
                "title": title,
                "snippet": snippet,
                "relevance_score": relevance_score,
            })
        return results

    except Exception as e:
        import traceback
        print(f"[Vertex AI Search Precision] Error: {e}")
        print(f"[Vertex AI Search Precision] Traceback: {traceback.format_exc()}")
        return []


def vertex_ai_search_recall(query: str, num: int = 5) -> List[Dict[str, str]]:
    """
    Search lifestyle/hobby sites using Vertex AI Search.

    Note: The query parameter may contain PSE operators (intitle:, intext:).
    These will be transformed to natural language before sending to Vertex AI.

    Returns same interface as google_search_recall() for drop-in replacement.
    Conversion to SearchHit happens at call site (same as PSE).

    Args:
        query: PSE-style query (will be transformed) or natural language query
        num: Maximum number of results (default 5, max 25 for basic indexing)

    Returns:
        List of dicts with keys: url, title, snippet, relevance_score
    """
    # Transform PSE query to natural language if needed
    if 'intitle:' in query or 'intext:' in query:
        query_nl = transform_pse_query_to_natural_language(query)
    else:
        query_nl = query

    num = min(num, 25)
    engine_id = os.environ.get("RECALL_ENGINE_ID", "recall-search-engine")

    try:
        from google.cloud import discoveryengine_v1 as discoveryengine

        client = discoveryengine.SearchServiceClient()
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
            query=query_nl,
            page_size=num,
            content_search_spec=content_search_spec,
            relevance_score_spec=relevance_score_spec,
        )

        print(f"[Vertex AI Search Recall] Executing search: query={query_nl[:80]}..., serving_config={serving_config}")
        response = client.search(request)
        print(f"[Vertex AI Search Recall] Search completed: {len(response.results)} results returned")

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

            relevance_score = 0.0

            results.append({
                "url": url,
                "title": title,
                "snippet": snippet,
                "relevance_score": relevance_score,
            })

        print(f"[Vertex AI Search Recall] Returning {len(results)} results")
        return results

    except Exception as e:
        import traceback
        print(f"[Vertex AI Search Recall] Error: {e}")
        print(f"[Vertex AI Search Recall] Traceback: {traceback.format_exc()}")
        return []


def google_search_linkedin_v2(query: str, num: int = 5) -> List[Dict[str, str]]:
    """
    LinkedIn search with Vertex AI Search, falling back to PSE.
    
    Returns same dict format as PSE for compatibility with existing call site
    that converts to SearchHit.
    
    Set LINKEDIN_USE_VERTEX_AI=true to enable Vertex AI Search.
    """
    use_vertex = os.environ.get("LINKEDIN_USE_VERTEX_AI", "false").lower() == "true"
    print(f"[LinkedIn Search v2] use_vertex={use_vertex}, query={query[:100]}")
    
    if use_vertex:
        results = vertex_ai_search_linkedin(query, num)
        print(f"[LinkedIn Search v2] Vertex AI Search returned {len(results)} results")
        if results:
            # Mark results as from Vertex AI Search by adding a marker field
            # We'll use this to determine source even if relevance_score is 0.0
            for r in results:
                r["_source"] = "vertex_ai_search"  # Internal marker
            print(f"[LinkedIn Search v2] Returning Vertex AI Search results (first result relevance_score={results[0].get('relevance_score', 0.0) if results else 'N/A'})")
            return results  # Already returns List[Dict] with relevance_score
        print("[LinkedIn Search v2] Vertex AI Search returned no results, falling back to PSE")
    
    # Fall back to existing PSE function (returns List[Dict] without relevance_score)
    pse_results = google_search_linkedin(query, num)
    # Add relevance_score for consistency and mark as PSE
    return [
        {
            "url": r["url"],
            "title": r["title"],
            "snippet": r["snippet"],
            "relevance_score": 0.0,  # PSE doesn't provide scores
            "_source": "pse"  # Internal marker
        }
        for r in pse_results
    ]


def google_search_precision_v2(query: str, num: int = 5) -> List[Dict[str, str]]:
    """
    Precision search with Vertex AI Search, falling back to PSE.

    Returns same dict format as PSE for compatibility with existing call site
    that converts to SearchHit.

    Set PRECISION_USE_VERTEX_AI=true to enable Vertex AI Search.
    """
    use_vertex = os.environ.get("PRECISION_USE_VERTEX_AI", "false").lower() == "true"
    if use_vertex:
        results = vertex_ai_search_precision(query, num)
        if results:
            for r in results:
                r["_source"] = "vertex_ai_search"
            return results
    pse_results = google_search_precision(query, num)
    return [
        {"url": r["url"], "title": r["title"], "snippet": r["snippet"], "relevance_score": 0.0, "_source": "pse"}
        for r in pse_results
    ]


def google_search_recall_2_v2(query: str, num: int = 5) -> List[Dict[str, str]]:
    """
    Recall_2 search with Vertex AI Search, falling back to PSE.

    Uses the same Vertex AI engine as precision (since RECALL_PSE_CX_2
    uses the same PSE as PRECISION_PSE_CX).

    Note: The query will be transformed from PSE format (intext:, etc.)
    to natural language before sending to Vertex AI.

    Returns same dict format as PSE for compatibility with existing call site.

    Set PRECISION_USE_VERTEX_AI=true to enable Vertex AI Search.
    """
    use_vertex = os.environ.get("PRECISION_USE_VERTEX_AI", "false").lower() == "true"
    if use_vertex:
        results = vertex_ai_search_precision(query, num)
        if results:
            for r in results:
                r["_source"] = "vertex_ai_search"
            return results
    pse_results = google_search_recall_2(query, num)
    return [
        {"url": r["url"], "title": r["title"], "snippet": r["snippet"], "relevance_score": 0.0, "_source": "pse"}
        for r in pse_results
    ]


def vertex_ai_score(seed: Dict[str, Any], queries_payload: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Phase 1: Identity resolver LLM call using Vertex AI Gemini."""
    if not GCP_PROJECT:
        return {"error": "GCP_PROJECT not set"}
    
    # Initialize Vertex AI
    try:
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
    except Exception as e:
        print(f"[Vertex AI] Initialization error: {e}")
        return {"error": f"Vertex AI initialization failed: {str(e)}"}
    
    system_prompt = (
        "You are an evidence-driven resolver. You help skip tracers find and contact debtors. You ONLY use the provided evidence.\n"
        "- You receive a seed (full_name, email, optional city, optional company_name) and a list of search queries.\n"
        "- Each query has a type: 'high_precision' or 'high_recall'.\n"
        "- 'high_precision' hits are more likely to be real social profiles and accurate professional profiles.\n"
        "- 'high_recall' hits are broader and noisier; only trust them if they clearly match the seed.\n"
        "Your goals:\n"
        "1) Identify social handles that plausibly belong to the same person as the seed.\n"
        "2) For each handle, output platform (if inferable from URL), handle, url, optional city, and confidence (high/medium/low).\n"
        "3) Optionally infer an aggregated city + confidence. Use all hits to infer the city and confidence.\n"
        "4) Be conservative (especially common name/city matches - without addional corroborating evidence these should not be higher than medium confidence): prefer precision hits; use recall hits only when evidence matches strongly.\n"
        "Also select up to 5 unique, high-quality identity clues (from context or name queries) that clearly refer to the same person as the seed and are not already represented in your top_handles or other parts of the output, and return them in the `identity_clues` array with title, url, snippet, and source_query_id.\n"
        "Do not include any identity clues that contradict the top_handles"
        "People sometimes use variations of their names. For example, their middle and last name. Include these when they match other seed information, like city or same/similiar company."
        "When two sources confirm the same address, prefer the one that includes a property purchase, deed, or asset transaction.\n"
        "Return STRICT JSON only.\n"
    )
    
    schema = {
        "type": "object",
        "properties": {
            "top_handles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "platform": {"type": "string"},
                        "handle": {"type": "string"},
                        "url": {"type": "string"},
                        "city": {"type": "string"},
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                    },
                    "required": ["platform", "handle", "url", "confidence"],
                },
            },
            "identity_clues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                        "snippet": {"type": "string"},
                        "source_query_id": {"type": "string"},
                    },
                    "required": ["title", "url"],
                },
            },
            "location": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
            },
            "rationale": {"type": "string"},
        },
        "required": ["top_handles"],
    }
    
    user_prompt = f"""Analyze the following identity resolution request:

Seed Information:
{json.dumps(seed, indent=2)}

Search Results from {len(queries_payload)} queries:
{json.dumps(queries_payload, indent=2)}

Return valid JSON with all required fields."""
    
    def _call_vertex_ai():
        try:
            # Use gemini-2.5-flash with global endpoint for faster processing
            model = GenerativeModel(model_name="gemini-2.5-flash")
            print(f"[Vertex AI] Calling Gemini 2.5 Flash...")
            
            # Generate response
            # Combine system prompt and user prompt since system_instruction may not be available
            full_prompt = f"{system_prompt}\n\n{user_prompt}"
            response = model.generate_content(
                full_prompt,
                generation_config=GenerationConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=schema,
                )
            )
            
            # Check for empty response - handle cases where response.text might not exist or be None
            if not response:
                raise EmptyLLMResponseError("Empty response from Vertex AI")
            
            # Safely get response text, handling AttributeError if text property doesn't exist
            try:
                response_text = response.text
            except AttributeError:
                raise EmptyLLMResponseError("Response object missing text attribute")
            
            if not response_text:
                raise EmptyLLMResponseError("Empty response from Vertex AI")
            
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
            
            result = json.loads(content)
            
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
                    print(f"[Vertex AI] ⚠️  Invalid confidence value, defaulting to 'medium'")
            
            print(f"[Vertex AI] ✅ Successfully analyzed identity results")
            return result
            
        except Exception as e:
            print(f"[Vertex AI] Error: {e}")
            raise
    
    try:
        return retry_with_backoff(
            _call_vertex_ai,
            RetryConfig(max_attempts=3, base_delay_seconds=2.0, max_delay_seconds=60.0),
            operation_name="Vertex AI identity scoring"
        )
    except json.JSONDecodeError as e:
        # Try to get content if available
        try:
            return {"raw": str(e)}
        except:
            return {"error": "JSON decode error"}
    except Exception as e:
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

    print(f"[Phase1] Starting identity resolution for job {job_id}: {full_name} <{email}>")

    # -------------------------
    # Execute search queries
    # -------------------------
    
    # 1. Precision query - social platforms with full name
    # Note: Site restrictions are handled by the PRECISION_PSE_CX PSE configuration
    # Generate name variations (full name + middle+last if applicable)
    name_full, name_variation = generate_name_variations(full_name)
    if name_variation:
        precision_query = f'"{name_full}" OR "{name_variation}"'
    else:
        precision_query = f'"{name_full}"'
    if city:
        precision_query += f' {city.split(",")[0]}'
    precision_raw = google_search_precision_v2(precision_query, num=10)
    has_vertex_results = any(h.get("_source") == "vertex_ai_search" for h in precision_raw if h.get("url"))
    source_value = "vertex_ai_precision" if has_vertex_results else "google_search"
    precision_hits = [
        SearchHit(
            url=h["url"],
            title=h["title"],
            snippet=h["snippet"],
            source=source_value,
            query_id="precision",
            query_type="high_precision",
            relevance_score=h.get("relevance_score", 0.0),
        )
        for h in precision_raw if h.get("url")
    ]

    # Middle name LinkedIn search - if middle name is detected
    middle_name_linkedin_hits: List[SearchHit] = []
    middle_name_linkedin_query = ""

    if name_variation:  # Middle name detected
        print(f"[Phase1] Middle name detected: {name_variation} - performing LinkedIn search")
        
        # Build query with same format as precision query
        middle_name_linkedin_query = f'"{name_full}" OR "{name_variation}"'
        if city:
            middle_name_linkedin_query += f' {city.split(",")[0]}'
        
        # Execute LinkedIn search (uses Vertex AI or PSE based on config)
        middle_name_linkedin_raw = google_search_linkedin_v2(middle_name_linkedin_query, num=10)
        
        # Determine source based on _source marker (set by google_search_linkedin_v2)
        # This is more reliable than relevance_score since Vertex AI Search may return relevance_score=0.0
        # when model_scores is empty (as seen in logs: "Keys: []")
        has_vertex_results = any(h.get("_source") == "vertex_ai_search" for h in middle_name_linkedin_raw if h.get("url"))
        source_value = "vertex_ai_linkedin" if has_vertex_results else "google_search"
        _source_markers = [h.get("_source") for h in middle_name_linkedin_raw if h.get("url")]
        print(f"[Phase1] Middle name LinkedIn search: results_count={len(middle_name_linkedin_raw)}, _source_markers={_source_markers}, has_vertex_results={has_vertex_results}, source={source_value}")
        
        # Convert to SearchHit objects
        middle_name_linkedin_hits = [
            SearchHit(
                url=h["url"],
                title=h["title"],
                snippet=h["snippet"],
                source=source_value,  # Differentiate: "vertex_ai_linkedin" or "google_search"
                query_id="middle_name_linkedin",
                query_type="high_precision",
                relevance_score=h.get("relevance_score", 0.0),  # Extract if present (may be 0.0 even for Vertex AI)
            )
            for h in middle_name_linkedin_raw if h.get("url")
        ]
        
        # Append to precision_hits (so they're included in precision results)
        precision_hits.extend(middle_name_linkedin_hits)
        
        print(f"[Phase1] Middle name LinkedIn search: {len(middle_name_linkedin_hits)} hits")

    # Business email searches - if email is a business domain
    business_domain_hits: List[SearchHit] = []
    business_linkedin_hits: List[SearchHit] = []
    business_domain_query = ""
    business_linkedin_query = ""

    if is_business_email(email):
        domain = extract_email_domain(email)
        if domain:
            print(f"[Phase1] Business email detected: {domain} - performing additional searches")

            # Search 1: Full Name and business domain name (without quotes)
            business_domain_query = f"{full_name} {domain}"
            business_domain_raw = google_search(business_domain_query, num=10)
            business_domain_hits = [
                SearchHit(
                    url=h["url"],
                    title=h["title"],
                    snippet=h["snippet"],
                    source="google_search",
                    query_id="business_domain",
                    query_type="high_precision",
                )
                for h in business_domain_raw if h.get("url")
            ]
            
            # Search 2: Full Name business domain site:linkedin.com
            business_linkedin_query = f"{full_name} {domain} site:linkedin.com/in"
            business_linkedin_raw = google_search(business_linkedin_query, num=10)
            business_linkedin_hits = [
                SearchHit(
                    url=h["url"],
                    title=h["title"],
                    snippet=h["snippet"],
                    source="google_search",
                    query_id="business_linkedin",
                    query_type="high_precision",
                )
                for h in business_linkedin_raw if h.get("url")
            ]

            # Append to precision_hits (so they're included in precision results)
            precision_hits.extend(business_domain_hits)
            precision_hits.extend(business_linkedin_hits)
            
            print(f"[Phase1] Business email searches: {len(business_domain_hits)} domain hits, {len(business_linkedin_hits)} LinkedIn hits")

    # Company name searches - if company_name is provided
    company_name_hits: List[SearchHit] = []
    company_name_linkedin_hits: List[SearchHit] = []
    company_name_query = ""
    company_name_linkedin_query = ""

    if company_name:
        print(f"[Phase1] Company name provided: {company_name} - performing company name searches")
        
        # Search 1: Full Name and Company Name (general search)
        company_name_query = f"{full_name} {company_name}"
        company_name_raw = google_search(company_name_query, num=10)
        company_name_hits = [
            SearchHit(
                url=h["url"],
                title=h["title"],
                snippet=h["snippet"],
                source="google_search",
                query_id="company_name",
                query_type="high_precision",
            )
            for h in company_name_raw if h.get("url")
        ]
        
        # Append to precision_hits (so they're included in precision results)
        precision_hits.extend(company_name_hits)
        
        # Search 2: Full Name and Company Name on LinkedIn
        # Note: Site restriction removed as LINKEDIN_PSE_CX is already scoped to ca.linkedin.com
        company_name_linkedin_query = f'{full_name} {company_name}'
        company_name_linkedin_raw = google_search_linkedin_v2(company_name_linkedin_query, num=10)
        
        # Determine source based on _source marker (set by google_search_linkedin_v2)
        # This is more reliable than relevance_score since Vertex AI Search may return relevance_score=0.0
        # when model_scores is empty (as seen in logs: "Keys: []")
        has_vertex_results = any(h.get("_source") == "vertex_ai_search" for h in company_name_linkedin_raw if h.get("url"))
        source_value = "vertex_ai_linkedin" if has_vertex_results else "google_search"
        _source_markers = [h.get("_source") for h in company_name_linkedin_raw if h.get("url")]
        print(f"[Phase1] LinkedIn search: results_count={len(company_name_linkedin_raw)}, _source_markers={_source_markers}, has_vertex_results={has_vertex_results}, source={source_value}")
        
        company_name_linkedin_hits = [
            SearchHit(
                url=h["url"],
                title=h["title"],
                snippet=h["snippet"],
                source=source_value,  # Differentiate: "vertex_ai_linkedin" or "google_search"
                query_id="company_name_linkedin",
                query_type="high_precision",
                relevance_score=h.get("relevance_score", 0.0),  # Extract if present (may be 0.0 even for Vertex AI)
            )
            for h in company_name_linkedin_raw if h.get("url")
        ]
        
        # Append to precision_hits (so they're included in precision results)
        precision_hits.extend(company_name_linkedin_hits)
        
        print(f"[Phase1] Company name searches: {len(company_name_hits)} hits, {len(company_name_linkedin_hits)} LinkedIn hits")

    # 2. Context query - email prefix across web
    context_query = ""
    context_hits: List[SearchHit] = []
    if len(prefix) >= 4:
        prefix_no_dots = prefix.replace(".", "")
        if prefix_no_dots != prefix:
            context_query = f"intext:{prefix} OR intext:{prefix_no_dots} OR {full_name}"
        else:
            context_query = f"intext:{prefix} OR {full_name}"
        context_raw = google_search(context_query, num=10)
        context_hits = [
            SearchHit(
                url=h["url"],
                title=h["title"],
                snippet=h["snippet"],
                source="google_search",
                query_id="context",
                query_type="context",
            )
            for h in context_raw if h.get("url")
        ]

    # 3. Recall query - lifestyle sites
    # Note: Site restrictions are handled by the RECALL_PSE_CX PSE configuration
    recall_query = ""
    recall_hits: List[SearchHit] = []
    if len(prefix) >= 4:
        recall_query = f'intext:{prefix} OR "{full_name}"'
        recall_raw = google_search_recall_v2(recall_query, num=10)
        # Detect if results came from Vertex AI
        has_vertex_results = any(h.get("_source") == "vertex_ai_recall" for h in recall_raw if h.get("url"))
        source_value = "vertex_ai_recall" if has_vertex_results else "google_search"
        recall_hits = [
            SearchHit(
                url=h["url"],
                title=h["title"],
                snippet=h["snippet"],
                source=source_value,
                query_id="recall",
                query_type="high_recall",
                relevance_score=h.get("relevance_score", 0.0),
            )
            for h in recall_raw if h.get("url")
        ]

    # 3b. Additional recall query - using second PSE
    # Note: Site restrictions are handled by the RECALL_PSE_CX_2 PSE configuration
    recall_2_query = ""
    recall_2_hits: List[SearchHit] = []
    if len(prefix) >= 4:
        recall_2_query = f'{prefix} OR "{full_name}"'
        recall_2_raw = google_search_recall_2_v2(recall_2_query, num=10)
        has_vertex_results = any(h.get("_source") == "vertex_ai_search" for h in recall_2_raw if h.get("url"))
        source_value = "vertex_ai_precision" if has_vertex_results else "google_search"
        recall_2_hits = [
            SearchHit(
                url=h["url"],
                title=h["title"],
                snippet=h["snippet"],
                source=source_value,
                query_id="recall_2",
                query_type="high_recall",
                relevance_score=h.get("relevance_score", 0.0),
            )
            for h in recall_2_raw if h.get("url")
        ]

    # 4. Name query - contact info discovery
    name_query = ""
    name_hits: List[SearchHit] = []
    if full_name and city:
        city_token = city.split(",")[0]
        name_query = (
            f'intext:"{full_name}" '
            f'("phone" OR "tel" OR "contact" OR "address" OR "email") {city_token}'
        )
        name_raw = google_search(name_query, num=10)
        name_hits = [
            SearchHit(
                url=h["url"],
                title=h["title"],
                snippet=h["snippet"],
                source="google_search",
                query_id="name_search",
                query_type="context",
            )
            for h in name_raw if h.get("url")
        ]

    # Deduplicate hits
    seen = set()
    combined_hits: List[SearchHit] = []
    for hit in precision_hits + context_hits + recall_hits + recall_2_hits + name_hits:
        if hit.url not in seen:
            seen.add(hit.url)
            combined_hits.append(hit)

    # Build queries payload for LLM
    queries_payload: List[Dict[str, Any]] = [
        {
            "id": "precision",
            "type": "high_precision",
            "query": precision_query,
            "hits": [asdict(h) for h in precision_hits],
        },
        {
            "id": "context",
            "type": "context",
            "query": context_query,
            "hits": [asdict(h) for h in context_hits],
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
        {
            "id": "name_search",
            "type": "context",
            "query": name_query,
            "hits": [asdict(h) for h in name_hits],
        },
    ]
    
    # Add business email queries if they were executed
    if business_domain_query:
        queries_payload.append({
            "id": "business_domain",
            "type": "high_precision",
            "query": business_domain_query,
            "hits": [asdict(h) for h in business_domain_hits],
        })
    
    if business_linkedin_query:
        queries_payload.append({
            "id": "business_linkedin",
            "type": "high_precision",
            "query": business_linkedin_query,
            "hits": [asdict(h) for h in business_linkedin_hits],
        })
    
    # Add company name queries if they were executed
    if company_name_query:
        queries_payload.append({
            "id": "company_name",
            "type": "high_precision",
            "query": company_name_query,
            "hits": [asdict(h) for h in company_name_hits],
        })
    
    if company_name_linkedin_query:
        queries_payload.append({
            "id": "company_name_linkedin",
            "type": "high_precision",
            "query": company_name_linkedin_query,
            "hits": [asdict(h) for h in company_name_linkedin_hits],
        })
    
    # Add middle name LinkedIn query if it was executed
    if middle_name_linkedin_query:
        queries_payload.append({
            "id": "middle_name_linkedin",
            "type": "high_precision",
            "query": middle_name_linkedin_query,
            "hits": [asdict(h) for h in middle_name_linkedin_hits],
        })

    # -------------------------
    # LLM identity scoring
    # -------------------------
    try:
        scored = vertex_ai_score(seed, queries_payload)
        scored_error = scored.get("error")
    except Exception as e:
        scored = {}
        scored_error = str(e)

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
        rerun_raw = google_search_precision_v2(rerun_query, num=10)

        rerun_hits = []
        for h in rerun_raw:
            url = h.get("url")
            if url and url not in seen:
                source_value = h.get("_source", "pse")
                hit = SearchHit(
                    url=url,
                    title=h.get("title", ""),
                    snippet=h.get("snippet", ""),
                    source=source_value,
                    query_id="precision_rerun",
                    query_type="high_precision",
                    relevance_score=h.get("relevance_score", 0.0),
                )
                rerun_hits.append(hit)
                seen.add(url)
                combined_hits.append(hit)
                name_hits.append(hit)

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
    # HIBP breach lookup
    # -------------------------
    breaches = hibp_breaches(email)

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
    }

    print(f"[Phase1] Complete - {len(scored.get('top_handles', []))} handles, {len(breaches)} breaches")

    return identity_bundle, 200
