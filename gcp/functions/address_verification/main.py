"""
Address Verification Cloud Function

Performs fraud detection analysis on Canadian business addresses for auto loan applications.
- Uses Gemini 2.5 Flash with Google Search grounding to analyze addresses for fraud indicators
- Detects virtual workspaces, shipping locations, and verifies business presence
"""

import functions_framework
import os
import json
import re
import time
from typing import List, Dict, Any, Optional
from flask import Request, jsonify
from urllib.parse import quote_plus
from urllib.request import urlopen, Request as URLRequest
from urllib.error import URLError, HTTPError
from retry_utils import retry_with_backoff, RetryConfig, EmptyLLMResponseError

# Google Gen AI SDK imports (for Gemini 2.5 Flash with grounding support)
from google import genai
from google.genai.types import GenerateContentConfig, Tool, GoogleSearch

# -------------------------
# Config
# -------------------------
GCP_PROJECT = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
GCP_LOCATION = os.environ.get("GCP_LOCATION", "northamerica-northeast1")



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
# Vertex AI Gemini Integration with Google Search Grounding
# -------------------------
def extract_grounding_metadata(response) -> Dict[str, Any]:
    """
    Extract grounding metadata from Google Gen AI SDK response for audit trail.
    Maps to existing queries_payload format for downstream compatibility.
    """
    metadata = {
        "grounding_sources": [],
        "search_queries": [],
        "search_entry_point": ""
    }
    
    try:
        # Google Gen AI SDK response structure
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            
            # Extract grounding metadata if available
            if hasattr(candidate, 'grounding_metadata'):
                grounding = candidate.grounding_metadata
                
                # Extract search queries
                if hasattr(grounding, 'web_search_queries'):
                    metadata["search_queries"] = list(grounding.web_search_queries)
                
                # Extract grounding chunks (sources)
                if hasattr(grounding, 'grounding_chunks'):
                    for chunk in grounding.grounding_chunks:
                        if hasattr(chunk, 'web'):
                            metadata["grounding_sources"].append({
                                "url": getattr(chunk.web, 'uri', ''),
                                "title": getattr(chunk.web, 'title', ''),
                                "snippet": ""  # Grounding doesn't return snippets
                            })
                
                # Extract search entry point if available
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


def map_grounding_to_queries_payload(grounding_metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Transform grounding metadata to match existing queries_payload format.
    Maintains backward compatibility with downstream consumers (report generators).
    """
    queries_payload = []
    
    if not grounding_metadata:
        return queries_payload
    
    sources = grounding_metadata.get("grounding_sources", [])
    search_queries = grounding_metadata.get("search_queries", [])
    
    if sources:
        queries_payload.append({
            "id": "gemini_grounded_search",
            "type": "grounded",
            "query": ", ".join(search_queries) if search_queries else "Gemini-determined queries",
            "hits": sources
        })
    
    return queries_payload


def vertex_ai_analyze_address_grounded(address: str, business_name: str) -> Dict[str, Any]:
    """
    Analyze address using Gemini 2.5 Flash with Google Search grounding.
    The model performs its own searches and returns grounded analysis.
    """
    if not GCP_PROJECT:
        return {"error": "GCP_PROJECT not set"}
    
    system_prompt = """You are a fraud detection expert analyzing Canadian business information and addresses for auto loan applications. Your goal is to verify that the claimed business actually exists at the provided address and identify fraudulent or suspicious information and addresses that may indicate loan application fraud.

Common red flags include:
- Virtual office addresses (Regus, WeWork, co-working spaces, etc.)
- Shipping/mailbox locations (UPS/FedEx stores, PO boxes, postal outlets)
- Addresses where the claimed business doesn't exist
- Addresses with inconsistent unit/suite numbers used by all other verified tenants in the building
- Addresses that are clearly residential when a business is claimed
- Absence of ratings or reviews or complaints or comments from customers or clients
- Absence of any supporting information or evidence of the business's existence
- Addresses or business names associated with known fraud patterns

CRITICAL - UPS Store and FedEx Office mailbox detection:
If a UPS Store or FedEx Office is located at the address being verified with a different unit number, you MUST treat this as a significant fraud indicator. The presence of a distinct unit number DOES NOT indicate a dedicated physical space. Flag this as "potential_ups_fedex_mailbox" in fraud_indicators.

Analyze the search results to determine if this is a legitimate business or if it raises fraud concerns. The business name is provided - verify if this business actually exists at this address."""

    user_prompt = f"""Analyze the following business address verification request:

Business Name: {business_name}
Address: {address}

Search the web to verify this business exists at this address. Check for reviews, complaints, virtual office indicators, and any evidence of business presence or fraud.

Return valid JSON with these fields:
{{
  "business_at_address": boolean,
  "is_virtual_workspace": boolean,
  "is_shipping_location": boolean,
  "is_residential": boolean,
  "is_suspicious": boolean,
  "fraud_risk_level": "low" | "medium" | "high",
  "fraud_indicators": string[],
  "confidence": "low" | "medium" | "high",
  "reasoning": string,
  "key_findings": string[]
}}

Return JSON only."""

    def _call_vertex_ai_grounded():
        try:
            # Initialize Google Gen AI client with Vertex AI backend
            client = genai.Client(
                vertexai=True,
                project=GCP_PROJECT,
                location=GCP_LOCATION
            )
            
            # Configure Google Search grounding tool
            google_search_tool = Tool(google_search=GoogleSearch())
            
            print(f"[Vertex AI] Calling Gemini 2.5 Flash with Google Search grounding...")
            
            # Generate response with grounding
            # NOTE: Cannot use response_schema with grounding in Gemini 2.5 Flash
            # (Structured outputs with tools only available in Gemini 3)
            # Must parse JSON manually from text response
            # Use system_instruction parameter to match Google AI Studio behavior
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_prompt,
                config=GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=[google_search_tool],
                    temperature=0.1,
                    top_p=0.95,  # Match Google AI Studio default
                    max_output_tokens=2048,  # Ensure complete responses
                )
            )
            
            if not response or not hasattr(response, 'text') or not response.text:
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
            
            if not content.strip():
                raise EmptyLLMResponseError("Empty content after stripping markdown")
            
            result = json.loads(content)
            
            # Extract grounding metadata for audit trail
            grounding_metadata = extract_grounding_metadata(response)
            result["_grounding_metadata"] = grounding_metadata
            
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
            
            missing_fields = []
            for field, default_value in required_fields.items():
                if field not in result:
                    result[field] = default_value
                    missing_fields.append(field)
            
            if missing_fields:
                print(f"[Vertex AI] ⚠️  Missing fields filled with defaults: {missing_fields}")
            
            # Validate enum values
            if result.get("fraud_risk_level") not in ["low", "medium", "high"]:
                result["fraud_risk_level"] = "medium"
            
            if result.get("confidence") not in ["low", "medium", "high"]:
                result["confidence"] = "medium"
            
            # Ensure arrays are actually arrays
            if not isinstance(result.get("fraud_indicators"), list):
                result["fraud_indicators"] = []
            if not isinstance(result.get("key_findings"), list):
                result["key_findings"] = []
            
            print(f"[Vertex AI] ✅ Successfully analyzed address with grounding")
            return result
            
        except Exception as e:
            print(f"[Vertex AI] Error: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    try:
        return retry_with_backoff(
            _call_vertex_ai_grounded,
            RetryConfig(max_attempts=3, base_delay_seconds=2.0, max_delay_seconds=60.0),
            operation_name="Vertex AI grounded address analysis"
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
    
    try:
        # Analyze with Vertex AI Gemini using Google Search grounding
        print(f"[Address Verification] Analyzing with Gemini 2.5 Flash + Google Search grounding...")
        analysis = vertex_ai_analyze_address_grounded(address, business_name)
        
        if "error" in analysis:
            print(f"[Address Verification] Vertex AI analysis error: {analysis['error']}")
            return jsonify({"error": f"Analysis failed: {analysis['error']}"}), 500, headers
        
        # Extract grounding metadata and map to queries_payload format
        grounding_metadata = analysis.pop("_grounding_metadata", {})
        queries_payload = map_grounding_to_queries_payload(grounding_metadata)
        
        print(f"[Address Verification] Vertex AI analysis received:")
        print(f"  - business_at_address: {analysis.get('business_at_address')}")
        print(f"  - fraud_risk_level: {analysis.get('fraud_risk_level')}")
        print(f"  - confidence: {analysis.get('confidence')}")
        print(f"  - grounding_sources: {len(grounding_metadata.get('grounding_sources', []))}")
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
                "queries": queries_payload,
                "grounding_metadata": grounding_metadata  # Full metadata for audit
            }
        }
        
        print(f"[Address Verification] Complete - Business at address: {analysis.get('business_at_address')}, Risk: {analysis.get('fraud_risk_level')}")
        
        return jsonify(response), 200, headers
        
    except Exception as e:
        print(f"[Address Verification] Error during verification: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Verification failed: {str(e)}"}), 500, headers













