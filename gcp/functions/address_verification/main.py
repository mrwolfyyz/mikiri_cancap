"""
Address Verification Cloud Function

Performs fraud detection analysis on Canadian business addresses for auto loan applications.
- Performs Google searches using Programmable Search Engine (PSE)
- Uses Vertex AI Gemini 3 Flash Preview to analyze results for fraud indicators
- Detects virtual workspaces, shipping locations, and verifies business presence
"""

import functions_framework
import os
import json
import requests
import re
import time
from typing import List, Dict, Any, Optional
from flask import Request, jsonify
from urllib.parse import quote_plus
from urllib.request import urlopen, Request as URLRequest
from urllib.error import URLError, HTTPError
from retry_utils import retry_with_backoff, RetryConfig, EmptyLLMResponseError

# Vertex AI imports
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

# -------------------------
# Config
# -------------------------
GOOGLE_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
GOOGLE_SEARCH_CX = os.environ.get("GOOGLE_SEARCH_CX", "")
REVIEWS_PSE_CX = os.environ.get("REVIEWS_PSE_CX", "")
COMPLAINTS_PSE_CX = os.environ.get("COMPLAINTS_PSE_CX", "")
GCP_PROJECT = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
# Use 'global' endpoint for Gemini models - routes to any supported region
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")

# -------------------------
# Google Programmable Search Engine (PSE) Integration
# -------------------------
def google_search(query: str, num: int = 20) -> List[Dict[str, str]]:
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


def google_search_reviews(query: str, num: int = 10) -> List[Dict[str, str]]:
    """Call Google Custom Search API (PSE) with retry logic for reviews/ratings searches."""
    if not GOOGLE_SEARCH_API_KEY:
        print("[Google Search Reviews] No API key set")
        return []
    if not REVIEWS_PSE_CX:
        print("[Google Search Reviews] No Reviews Search Engine ID (CX) set")
        return []
    
    # Custom Search API max is 10 results per request, so we may need pagination
    # For now, we'll request up to 10 results (the API limit)
    num = min(num, 10)
    
    endpoint = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": REVIEWS_PSE_CX,
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
            operation_name=f"Google Search Reviews: {query[:50]}"
        )
    except Exception as e:
        print(f"[Google Search Reviews] Error after retries: {e}")
        return []


def google_search_complaints(query: str, num: int = 10) -> List[Dict[str, str]]:
    """Call Google Custom Search API (PSE) with retry logic for complaints/fraud searches."""
    if not GOOGLE_SEARCH_API_KEY:
        print("[Google Search Complaints] No API key set")
        return []
    if not COMPLAINTS_PSE_CX:
        print("[Google Search Complaints] No Complaints Search Engine ID (CX) set")
        return []
    
    # Custom Search API max is 10 results per request, so we may need pagination
    # For now, we'll request up to 10 results (the API limit)
    num = min(num, 10)
    
    endpoint = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": COMPLAINTS_PSE_CX,
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
            operation_name=f"Google Search Complaints: {query[:50]}"
        )
    except Exception as e:
        print(f"[Google Search Complaints] Error after retries: {e}")
        return []


# -------------------------
# Geocoding Functions
# -------------------------
def clean_address_for_geocoding(address: str) -> str:
    """
    Clean address string to improve geocoding accuracy.
    Removes copyright text, years, company names, and other junk
    that appears before the actual civic address.
    """
    # Remove common prefixes
    patterns_to_remove = [
        r'^.*?©.*?Reserved\.\s*',  # Copyright text
        r'^.*?\d{4}\s+.*?Reserved\.\s*',  # Year + Reserved
        r'^.*?HEAD OFFICE\.\s*',  # HEAD OFFICE label
        r'^.*?OFFICE\.\s*',  # OFFICE label
        r'^.*?Contact:\s*',  # Contact: prefix
    ]
    
    for pattern in patterns_to_remove:
        address = re.sub(pattern, '', address, flags=re.IGNORECASE)
    
    # Trim and clean up
    address = address.strip()
    
    return address


def geocode_address(address: str) -> tuple:
    """
    Geocode an address using free Nominatim (OpenStreetMap) API.
    Returns (lat, lon) tuple or (None, None) if geocoding fails.
    Respects rate limits with a small delay.
    """
    try:
        # Nominatim requires a User-Agent
        url = f"https://nominatim.openstreetmap.org/search?q={quote_plus(address)}&format=json&limit=1"
        req = URLRequest(url, headers={'User-Agent': 'BorrowerIntelligence/1.0'})
        
        # Respect Nominatim rate limit (1 req/sec)
        time.sleep(1.1)
        
        with urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            if data and len(data) > 0:
                lat = float(data[0]['lat'])
                lon = float(data[0]['lon'])
                print(f"[Geocoding] ✓ Geocoded successfully: {lat:.6f}, {lon:.6f}")
                return (lat, lon)
            else:
                print(f"[Geocoding] ⚠️  No geocoding results found")
    except (URLError, HTTPError, KeyError, ValueError, json.JSONDecodeError) as e:
        print(f"[Geocoding] ⚠️  Geocoding failed: {e.__class__.__name__}")
    
    return (None, None)


def generate_street_view_url(address: str, lat: Optional[float] = None, lon: Optional[float] = None) -> str:
    """
    Generate a Google Maps Street View URL for a given address.
    If coordinates are provided, uses them directly. Otherwise attempts geocoding.
    Falls back to search URL if geocoding fails.
    
    Args:
        address: Address string
        lat: Optional latitude (if already geocoded)
        lon: Optional longitude (if already geocoded)
    """
    # Use provided coordinates if available
    if lat is not None and lon is not None:
        print(f"[Street View] Using provided coordinates: {lat:.6f}, {lon:.6f}")
        # Use the official Google Maps Street View URL format
        return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat},{lon}"
    
    # Try geocoding
    cleaned = clean_address_for_geocoding(address)
    geocode_lat, geocode_lon = geocode_address(cleaned)
    if geocode_lat is not None and geocode_lon is not None:
        # Direct Street View URL with coordinates using official format
        return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={geocode_lat},{geocode_lon}"
    
    # Fallback: search URL (one click to Street View via pegman)
    encoded = quote_plus(address)
    print(f"[Street View] Using fallback search URL")
    return f"https://www.google.com/maps/search/{encoded}"


# -------------------------
# Vertex AI Gemini Integration
# -------------------------
def vertex_ai_analyze_address(address: str, business_name: str, queries_payload: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze address and search results using Vertex AI Gemini 3 Flash Preview for fraud detection."""
    if not GCP_PROJECT:
        return {"error": "GCP_PROJECT not set"}
    
    # Initialize Vertex AI
    try:
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
    except Exception as e:
        print(f"[Vertex AI] Initialization error: {e}")
        return {"error": f"Vertex AI initialization failed: {str(e)}"}
    
    system_prompt = (
        "You are a fraud detection expert analyzing Canadian business information and addresses for auto loan applications. "
        "Your goal is to verify that the claimed business actually exists at the provided address and identify "
        "fraudulent or suspicious information and addresses that may indicate loan application fraud.\n\n"
        "Common red flags include:\n"
        "- Virtual office addresses (Regus, WeWork, co-working spaces, etc.)\n"
        "- Shipping/mailbox locations (UPS/FedEx stores, PO boxes, postal outlets)\n"
        "- Addresses where the claimed business doesn't exist\n"
        "- Addresses with inconsistent unit/suite numbers used by all other verified tenants in the building\n"
        "- Addresses that are clearly residential when a business is claimed\n"
        "- Absence of ratings or reviews or complaints or comments from customers or clients\n"
        "- Absence of any supporting information or evidence of the business's existence\n"
        "- Addresses or business names associated with known fraud patterns\n\n"
        "Analyze the search results to determine if this is a legitimate business or if it raises fraud concerns. "
        "The business name is provided - verify if this business actually exists at this address.\n\n"
        "Return STRICT JSON only."
    )
    
    schema = {
        "type": "object",
        "properties": {
            "business_at_address": {
                "type": "boolean",
                "description": "Does the claimed business exist at this address? This is the PRIMARY verification."
            },
            "is_virtual_workspace": {
                "type": "boolean",
                "description": "Is this a virtual office or co-working space? (Red flag)"
            },
            "is_shipping_location": {
                "type": "boolean",
                "description": "Is this a UPS/FedEx store, PO box, or postal outlet? (Red flag)"
            },
            "is_residential": {
                "type": "boolean",
                "description": "Is this a residential address? (Red flag if business is claimed)"
            },
            "is_suspicious": {
                "type": "boolean",
                "description": "Overall fraud risk indicator based on all findings"
            },
            "fraud_risk_level": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Overall fraud risk assessment"
            },
            "fraud_indicators": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific red flags found (e.g., 'Business not found at address', 'Virtual office address', 'UPS Store location')"
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "Confidence level in the analysis"
            },
            "reasoning": {
                "type": "string",
                "description": "Explanation of findings, especially business verification status"
            },
            "key_findings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Important details from search results"
            }
        },
        "required": ["business_at_address", "is_virtual_workspace", "is_shipping_location", "is_residential", "is_suspicious", "fraud_risk_level", "fraud_indicators", "confidence", "reasoning", "key_findings"]
    }
    
    user_prompt = f"""Analyze the following business address verification request:

Address: {address}
Business Name: {business_name}

Search Results from {len(queries_payload)} queries:
{json.dumps(queries_payload, indent=2)}

Return valid JSON with all required fields."""
    
    def _call_vertex_ai():
        try:
            # Use gemini-3-flash-preview with global endpoint
            # Search results are provided in the prompt context
            model = GenerativeModel(model_name="gemini-3-flash-preview")
            print(f"[Vertex AI] Calling Gemini 3 Flash Preview...")
            
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
            
            if not response or not response.text:
                raise EmptyLLMResponseError("Empty response from Vertex AI")
            
            # Parse JSON response
            content = response.text.strip()
            
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
            
            # Validate and provide defaults for required fields
            required_fields = {
                "business_at_address": False,
                "is_virtual_workspace": False,
                "is_shipping_location": False,
                "is_residential": False,
                "is_suspicious": False,
                "fraud_risk_level": "medium",
                "fraud_indicators": [],
                "confidence": "medium",
                "reasoning": "Analysis completed but some fields were missing from response.",
                "key_findings": []
            }
            
            # Fill in missing fields with defaults
            missing_fields = []
            for field, default_value in required_fields.items():
                if field not in result:
                    result[field] = default_value
                    missing_fields.append(field)
            
            if missing_fields:
                print(f"[Vertex AI] ⚠️  Missing fields filled with defaults: {missing_fields}")
                # Update reasoning to note missing fields
                if result.get("reasoning") == required_fields["reasoning"]:
                    result["reasoning"] = f"Analysis completed. Note: Some fields were missing from model response and filled with defaults: {', '.join(missing_fields)}"
            
            # Validate enum values
            if result.get("fraud_risk_level") not in ["low", "medium", "high"]:
                result["fraud_risk_level"] = "medium"
                print(f"[Vertex AI] ⚠️  Invalid fraud_risk_level, defaulting to 'medium'")
            
            if result.get("confidence") not in ["low", "medium", "high"]:
                result["confidence"] = "medium"
                print(f"[Vertex AI] ⚠️  Invalid confidence, defaulting to 'medium'")
            
            # Ensure arrays are actually arrays
            if not isinstance(result.get("fraud_indicators"), list):
                result["fraud_indicators"] = []
            if not isinstance(result.get("key_findings"), list):
                result["key_findings"] = []
            
            print(f"[Vertex AI] ✅ Successfully analyzed address")
            return result
            
        except Exception as e:
            print(f"[Vertex AI] Error: {e}")
            raise
    
    try:
        return retry_with_backoff(
            _call_vertex_ai,
            RetryConfig(max_attempts=3, base_delay_seconds=2.0, max_delay_seconds=60.0),
            operation_name="Vertex AI address analysis"
        )
    except Exception as e:
        return {"error": str(e)}


# -------------------------
# Main Function
# -------------------------
@functions_framework.http
def main(request: Request):
    """
    HTTP Cloud Function entry point.
    
    Expects JSON body (preferred format with separate fields):
    {
        "street_address": "123 Main St",
        "suite_unit": "Suite 280",
        "city": "Toronto",
        "province": "ON",
        "postal_code": "M5H 2N2",
        "business_name": "Acme Corporation"
    }
    
    Or (backward compatible):
    {
        "address": "123 Main St, Toronto, ON M5H 2N2",
        "business_name": "Acme Corporation"
    }
    
    Returns analysis results with fraud detection indicators.
    """
    # Enable CORS
    if request.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "3600",
        }
        return ("", 204, headers)
    
    headers = {"Access-Control-Allow-Origin": "*"}
    
    # Parse request
    try:
        req_data = request.get_json(silent=True) or {}
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400, headers
    
    # Accept either separate fields or combined address string (for backward compatibility)
    street_address = (req_data.get("street_address") or "").strip()
    suite_unit = (req_data.get("suite_unit") or "").strip()
    city = (req_data.get("city") or "").strip()
    province = (req_data.get("province") or "").strip()
    postal_code = (req_data.get("postal_code") or "").strip()
    address = (req_data.get("address") or "").strip()
    business_name = (req_data.get("business_name") or "").strip()
    
    # If separate fields provided, build address string; otherwise use provided address
    if street_address and city and province:
        address = street_address
        if suite_unit:
            address += f", {suite_unit}"
        address += f", {city}, {province}"
        if postal_code:
            address += f" {postal_code}"
    
    # Validate required fields
    if not address:
        return jsonify({"error": "address is required (or provide street_address, city, province)"}), 400, headers
    if not business_name:
        return jsonify({"error": "business_name is required"}), 400, headers
    
    print(f"[Address Verification] Starting verification: {business_name} at {address}")
    
    # Perform searches with multiple query variations
    queries_payload = []
    
    try:
        # Search 1: Business name + address (without quotes - more flexible)
        query1 = f"{business_name} {address}"
        print(f"[Address Verification] Query 1: {query1}")
        results1 = google_search(query1, num=10)
        queries_payload.append({
            "id": "business_name_and_address_flexible",
            "type": "high_precision",
            "query": query1,
            "hits": results1
        })
        print(f"[Address Verification] Query 1 returned {len(results1)} results")
        if results1:
            print(f"[Address Verification] Query 1 sample results (first 3):")
            for i, r in enumerate(results1[:3], 1):
                print(f"  [{i}] Title: {r.get('title', '')[:80]}")
                print(f"      URL: {r.get('url', '')[:80]}")
                print(f"      Snippet: {r.get('snippet', '')[:100]}")
        
        # Search 2: Address only (without suite/unit and without quotes)
        # Build address without suite/unit for this search
        if street_address and city and province:
            # Rebuild address without suite/unit
            address_for_search = street_address
            address_for_search += f", {city}, {province}"
            if postal_code:
                address_for_search += f" {postal_code}"
        else:
            # For backward compatibility: remove suite/unit from address string
            address_for_search = address
            if suite_unit:
                # Remove suite/unit patterns (e.g., ", Suite 280," or ", Suite 280")
                suite_patterns = [
                    rf',\s*{re.escape(suite_unit)}\s*,',
                    rf',\s*{re.escape(suite_unit)}\s*$',
                    rf'\s+{re.escape(suite_unit)}\s*,',
                    rf'\s+{re.escape(suite_unit)}\s*$',
                ]
                for pattern in suite_patterns:
                    address_for_search = re.sub(pattern, ',', address_for_search, flags=re.IGNORECASE)
                address_for_search = address_for_search.strip(',').strip()
        
        query2 = address_for_search
        print(f"[Address Verification] Query 2: {query2}")
        results2 = google_search(query2, num=10)
        queries_payload.append({
            "id": "address_only",
            "type": "context",
            "query": query2,
            "hits": results2
        })
        print(f"[Address Verification] Query 2 returned {len(results2)} results")
        
        # Use city and province directly if provided, otherwise extract from address
        city_province = None
        if city and province:
            # Use separate fields directly (no postal code included)
            city_province = f"{city}, {province}"
        else:
            # Fallback: extract from address string (for backward compatibility)
            address_parts = address.split(',')
            if len(address_parts) >= 2:
                extracted_city = address_parts[-2].strip() if len(address_parts) >= 2 else None
                province_part = address_parts[-1].strip() if len(address_parts) >= 1 else None
                
                # Remove postal code from province part (Canadian postal code format: A1A 1A1 or A1A1A1)
                if province_part:
                    province_part = re.sub(r'\s*[A-Z]\d[A-Z]\s*\d[A-Z]\d\s*$', '', province_part).strip()
                
                if extracted_city and province_part:
                    city_province = f"{extracted_city}, {province_part}"
        
        # Search 3: Business name + city/province (if address contains location info)
        if city_province:
            query3 = f"{business_name} {city_province}"
            print(f"[Address Verification] Query 3: {query3}")
            results3 = google_search(query3, num=10)
            queries_payload.append({
                "id": "business_name_and_location",
                "type": "high_recall",
                "query": query3,
                "hits": results3
            })
            print(f"[Address Verification] Query 3 returned {len(results3)} results")
        
        # Search 4: Business name + city/province reviews/ratings
        if city_province:
            query4 = f"{business_name} {city_province} reviews OR ratings"
            print(f"[Address Verification] Query 4: {query4}")
            results4 = google_search_reviews(query4, num=10)
            queries_payload.append({
                "id": "business_reviews_ratings",
                "type": "context",
                "query": query4,
                "hits": results4
            })
            print(f"[Address Verification] Query 4 returned {len(results4)} results")
            if results4:
                print(f"[Address Verification] Query 4 sample results (first 3):")
                for i, r in enumerate(results4[:3], 1):
                    print(f"  [{i}] Title: {r.get('title', '')[:80]}")
                    print(f"      URL: {r.get('url', '')[:80]}")
                    print(f"      Snippet: {r.get('snippet', '')[:100]}")
        
        # Search 5: Business name + city/province complaints/fraud/scam
        if city_province:
            query5 = f"{business_name} {city_province} complaints OR fraud OR scam"
            print(f"[Address Verification] Query 5: {query5}")
            results5 = google_search_complaints(query5, num=10)
            queries_payload.append({
                "id": "business_complaints_fraud",
                "type": "context",
                "query": query5,
                "hits": results5
            })
            print(f"[Address Verification] Query 5 returned {len(results5)} results")
            if results5:
                print(f"[Address Verification] Query 5 sample results (first 3):")
                for i, r in enumerate(results5[:3], 1):
                    print(f"  [{i}] Title: {r.get('title', '')[:80]}")
                    print(f"      URL: {r.get('url', '')[:80]}")
                    print(f"      Snippet: {r.get('snippet', '')[:100]}")
        
        # Analyze with Vertex AI Gemini
        print(f"[Address Verification] Analyzing results with Vertex AI Gemini...")
        print(f"[Address Verification] Total queries: {len(queries_payload)}, Total results: {sum(len(q.get('hits', [])) for q in queries_payload)}")
        analysis = vertex_ai_analyze_address(address, business_name, queries_payload)
        
        if "error" in analysis:
            print(f"[Address Verification] Vertex AI analysis error: {analysis['error']}")
            return jsonify({"error": f"Analysis failed: {analysis['error']}"}), 500, headers
        
        print(f"[Address Verification] Vertex AI analysis received:")
        print(f"  - business_at_address: {analysis.get('business_at_address')}")
        print(f"  - fraud_risk_level: {analysis.get('fraud_risk_level')}")
        print(f"  - confidence: {analysis.get('confidence')}")
        print(f"  - reasoning: {analysis.get('reasoning', '')[:200]}")
        
        # Geocode address for Street View link
        print(f"[Address Verification] Geocoding address for Street View...")
        lat, lon = geocode_address(address)
        street_view_url = generate_street_view_url(address, lat, lon)
        print(f"[Address Verification] Street View URL generated: {street_view_url[:80]}...")
        
        # Build response
        response = {
            "address": address,
            "business_name": business_name,
            "analysis": analysis,
            "geocoding": {
                "lat": lat,
                "lon": lon,
                "street_view_url": street_view_url
            },
            "search_results": {
                "queries": queries_payload
            }
        }
        
        print(f"[Address Verification] Complete - Business at address: {analysis.get('business_at_address')}, Risk: {analysis.get('fraud_risk_level')}")
        
        return jsonify(response), 200, headers
        
    except Exception as e:
        print(f"[Address Verification] Error during verification: {e}")
        return jsonify({"error": f"Verification failed: {str(e)}"}), 500, headers













