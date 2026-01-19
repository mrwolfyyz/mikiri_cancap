"""
Address Geocoding Cloud Function

Batch geocodes addresses extracted from investigation data.
Called from workflow as part of phase2 parallel execution.

Returns geocoding data that gets passed to aggregator.
"""

import functions_framework
import os
import json
import sys
import time
import re
from typing import Dict, Any, List, Optional
from urllib.parse import quote_plus
import pyap
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Import retry utilities (local copy for consistency with other phase2 functions)
from retry_utils import retry_with_backoff, RetryConfig, EmptyLLMResponseError

# Vertex AI imports
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

# -------------------------
# Vertex AI Config
# -------------------------
GCP_PROJECT = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")


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
        from urllib.request import urlopen, Request
        from urllib.error import URLError, HTTPError
        
        # Nominatim requires a User-Agent
        url = f"https://nominatim.openstreetmap.org/search?q={quote_plus(address)}&format=json&limit=1"
        req = Request(url, headers={'User-Agent': 'BorrowerIntelligence/1.0'})
        
        # Note: Rate limiting is handled by spacing requests 1 second apart when starting them
        # No sleep needed here since requests are already spaced
        
        with urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            if data and len(data) > 0:
                lat = float(data[0]['lat'])
                lon = float(data[0]['lon'])
                print(f"    ✓ Geocoded successfully: {lat:.6f}, {lon:.6f}")
                return (lat, lon)
            else:
                print(f"    ⚠️  No geocoding results found")
    except (URLError, HTTPError, KeyError, ValueError, json.JSONDecodeError) as e:
        print(f"    ⚠️  Geocoding failed: {e.__class__.__name__}")
    
    return (None, None)


def extract_1st_addresses_fallback(text: str) -> List[str]:
    """
    Fallback regex to extract US addresses with '1st' or 'First' that pyap cannot parse.
    Returns list of address strings.
    """
    # Pattern for US addresses with 1st/First in street name
    # Matches: street_number + (1st|First) + street_type + optional_direction + city + state + zip
    pattern = re.compile(
        r'\b(\d{1,6})\s+(1st|First)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)\s+((?:NW|NE|SW|SE|North|South|East|West|N|S|E|W)\s*)?,\s*([A-Za-z\s]+?),\s*([A-Z]{2})\s*,\s*(\d{5}(?:-\d{4})?)\b',
        re.IGNORECASE
    )
    
    addresses = []
    for match in pattern.finditer(text):
        street_num = match.group(1)
        ordinal = match.group(2)
        street_type = match.group(3)
        direction = (match.group(4) or "").strip()
        city = match.group(5).strip()
        state = match.group(6)
        zip_code = match.group(7)
        
        # Reconstruct address with direction if present
        if direction:
            addr = f"{street_num} {ordinal} {street_type} {direction}, {city}, {state}, {zip_code}"
        else:
            addr = f"{street_num} {ordinal} {street_type}, {city}, {state}, {zip_code}"
        addresses.append(addr)
    
    return addresses


ADDRESS_SCHEMA = {
    "type": "object",
    "properties": {
        "addresses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "address_raw": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"]
                    },
                    "source_url": {"type": "string"},
                    "snippet": {"type": "string"}
                },
                "required": ["address_raw", "source_url"]
            }
        }
    },
    "required": ["addresses"]
}


def extract_addresses_from_queries_llm(
    queries: List[Dict[str, Any]], 
    seed: Optional[Dict[str, Any]] = None
) -> List[Dict[str, str]]:
    """
    Extract addresses from query hits using Vertex AI Gemini.
    Returns list of address dicts with address_raw, source_url, snippet.
    """
    if not GCP_PROJECT:
        print("[LLM Address Extraction] GCP_PROJECT not set, returning empty results")
        return []
    
    # Initialize Vertex AI
    try:
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
    except Exception as e:
        print(f"[LLM Address Extraction] Vertex AI init error: {e}")
        return []
    
    # Count total hits for prompt
    total_hits = sum(len(q.get("hits", [])) for q in queries)
    
    if total_hits == 0:
        print("[LLM Address Extraction] No hits in queries, returning empty results")
        return []
    
    # Build prompts
    system_prompt = (
        "You are an address extractor for skip tracing investigations. You extract physical addresses from web search results.\n\n"
        "Your task:\n"
        "1. Extract addresses that appear to belong to the target person (seed information provided if available)\n"
        "2. Filter out addresses that clearly belong to other people or are unrelated\n"
        "3. Provide confidence scores (high/medium/low) based on how clearly the address relates to the target person\n"
        "4. Include the source URL and snippet for each extracted address\n\n"
        "Guidelines:\n"
        "- HIGH confidence: Address is clearly associated with the target person (name match, context strongly suggests it's them)\n"
        "- MEDIUM confidence: Address likely belongs to target person but with some ambiguity (similar name, partial context match)\n"
        "- LOW confidence: Address might be related but evidence is weak (same city, generic context)\n\n"
        "For addresses:\n"
        "- Extract complete civic addresses (street number, street name, city, state/province, postal code)\n"
        "- Prefer addresses that appear in property records, business registrations, or official documents\n"
        "- Skip partial addresses or addresses without postal codes unless they're clearly relevant\n"
        "- Return ONLY addresses that have at least LOW confidence. Do not include addresses with no relevance to the target person."
    )
    
    seed_info_text = ""
    if seed:
        seed_info_text = f"""Target Person:
- Name: {seed.get('full_name', 'N/A')}
- Email: {seed.get('email', 'N/A')}
- City: {seed.get('last_known_city', 'N/A')}
- Company: {seed.get('company_name', 'N/A') if seed.get('company_name') else 'N/A'}

"""
    
    user_prompt = f"""{seed_info_text}Extract addresses from the following search results:

Search Results ({len(queries)} queries, {total_hits} total hits):
{json.dumps(queries, indent=2)}

Return valid JSON with addresses array. Each item should have address_raw, confidence, source_url, and snippet fields."""
    
    def _call_vertex_ai():
        try:
            model = GenerativeModel(model_name="gemini-2.5-flash")
            print(f"[LLM Address Extraction] Calling Gemini 2.5 Flash for {total_hits} hits...")
            
            full_prompt = f"{system_prompt}\n\n{user_prompt}"
            response = model.generate_content(
                full_prompt,
                generation_config=GenerationConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=ADDRESS_SCHEMA,
                )
            )
            
            if not response:
                raise EmptyLLMResponseError("Empty response from Vertex AI")
            
            response_text = response.text
            if not response_text:
                raise EmptyLLMResponseError("Empty response text")
            
            # Parse and validate
            content = response_text.strip()
            
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
            
            # Validate structure
            if "addresses" not in result:
                result["addresses"] = []
            
            if not isinstance(result.get("addresses"), list):
                result["addresses"] = []
            
            # Normalize addresses
            normalized_addresses = []
            seen_addresses = set()
            
            for addr_obj in result.get("addresses", []):
                if not isinstance(addr_obj, dict):
                    continue
                address_raw = addr_obj.get("address_raw", "").strip()
                if not address_raw:
                    continue
                
                # Clean address for deduplication
                addr_cleaned = clean_address_for_geocoding(address_raw)
                addr_normalized = addr_cleaned.lower().strip()
                addr_normalized = re.sub(r',', ' ', addr_normalized)
                addr_normalized = re.sub(r'\s+', ' ', addr_normalized)
                
                if addr_normalized in seen_addresses:
                    continue
                seen_addresses.add(addr_normalized)
                
                normalized_addresses.append({
                    "address_raw": addr_cleaned,
                    "source_url": addr_obj.get("source_url", ""),
                    "snippet": addr_obj.get("snippet", "").strip()
                })
            
            print(f"[LLM Address Extraction] Extracted {len(normalized_addresses)} addresses")
            return normalized_addresses
            
        except Exception as e:
            print(f"[LLM Address Extraction] Error: {e}")
            raise
    
    try:
        return retry_with_backoff(
            _call_vertex_ai,
            RetryConfig(max_attempts=3, base_delay_seconds=2.0, max_delay_seconds=60.0),
            operation_name="LLM address extraction"
        )
    except Exception as e:
        print(f"[LLM Address Extraction] Error after retries: {e}")
        return []


def extract_addresses_from_queries(queries: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Scan query hits for address-like patterns using pyap.
    Falls back to regex for addresses with '1st' that pyap cannot parse.
    """
    results = []
    seen = set()

    for q in queries or []:
        for hit in q.get("hits", []):
            text = f"{hit.get('title','')} {hit.get('snippet','')}"
            source = hit.get("url", "")
            snippet = hit.get("snippet", "").strip()

            # Try US addresses
            addresses = pyap.parse(text, country='US')
            # Add Canadian addresses
            addresses.extend(pyap.parse(text, country='CA'))
            
            # Fallback: if pyap found nothing, check for "1st" addresses
            if len(addresses) == 0 and ('1st' in text or 'First' in text):
                fallback_addrs = extract_1st_addresses_fallback(text)
                # Convert to string format compatible with pyap output
                for addr_str in fallback_addrs:
                    addresses.append(addr_str)
            
            for addr_obj in addresses:
                # Handle both pyap objects and fallback strings
                if isinstance(addr_obj, str):
                    addr_raw = addr_obj
                else:
                    addr_raw = str(addr_obj)
                
                addr_cleaned = clean_address_for_geocoding(addr_raw)
                
                # Validate: Check if address contains street information
                # Skip if it's just a city/state/province (no street number or street name pattern)
                # For pyap objects, check if structured components would indicate street info
                if not isinstance(addr_obj, str):
                    # pyap address object - check structured components
                    street_number = getattr(addr_obj, 'street_number', None)
                    street_name = getattr(addr_obj, 'street_name', None)
                    
                    if not street_number and not street_name:
                        # This is a city-only address, skip it
                        print(f"[Address Extraction] Filtered out city-only address: {addr_cleaned}")
                        continue
                else:
                    # String address - check for street patterns in the raw string
                    # Look for street number pattern (digit at start) or street name indicators
                    has_street_number = bool(re.search(r'^\d{1,6}\s+[A-Za-z]', addr_cleaned))
                    # Check for common street name patterns (Avenue, Street, Road, etc. preceded by text)
                    has_street_name = bool(re.search(r'\b([A-Za-z0-9.\-\s]+?(?:Avenue|Street|Road|Lane|Drive|Boulevard|Way|Court|Place|Crescent|Circle|Terrace|Parkway|Highway|Ave|St|Rd|Dr|Blvd|Ln|Way|Ct|Pl|Cres|Cir|Terr|Pkwy|Hwy))\b', addr_cleaned, re.IGNORECASE))
                    
                    if not has_street_number and not has_street_name:
                        # This appears to be a city-only address, skip it
                        print(f"[Address Extraction] Filtered out city-only address (string): {addr_cleaned}")
                        continue
                
                # Normalize for deduplication
                addr_normalized = addr_cleaned.lower().strip()
                addr_normalized = re.sub(r',', ' ', addr_normalized)
                addr_normalized = re.sub(r'\s+', ' ', addr_normalized)
                
                if addr_normalized in seen:
                    continue
                seen.add(addr_normalized)
                
                results.append({
                    "address_raw": addr_cleaned,
                    "source_url": source,
                    "snippet": snippet,
                })

    return results


@functions_framework.http
def main(request):
    """
    HTTP Cloud Function entry point.
    
    Expects JSON body:
    {
        "identity": {
            "queries": [...]  // from phase1_identity, contains search results
        },
        "corporate": {  // optional, from phase2_corporate
            "debug": {
                "full_hits_raw": [...],
                "last_hits_raw": [...]
            }
        }
    }
    
    Returns geocoding data dict (consistent with other phase2 functions).
    """
    # Parse request
    try:
        req_data = request.get_json(silent=True) or {}
    except Exception:
        return {"error": "Invalid JSON"}, 400
    
    identity = req_data.get('identity') or {}
    queries = identity.get('queries', []) if isinstance(identity, dict) else []
    
    # Extract corporate data (optional)
    corporate = req_data.get('corporate')
    print(f"[AddressGeocoding] DEBUG: corporate type={type(corporate)}, value={corporate is not None}")
    if corporate and isinstance(corporate, dict):
        print(f"[AddressGeocoding] DEBUG: corporate keys={list(corporate.keys())}")
        corporate_debug = corporate.get('debug', {})
        print(f"[AddressGeocoding] DEBUG: corporate_debug type={type(corporate_debug)}, keys={list(corporate_debug.keys()) if isinstance(corporate_debug, dict) else 'N/A'}")
        full_hits_raw = corporate_debug.get('full_hits_raw', []) if isinstance(corporate_debug, dict) else []
        last_hits_raw = corporate_debug.get('last_hits_raw', []) if isinstance(corporate_debug, dict) else []
    else:
        corporate_debug = {}
        full_hits_raw = []
        last_hits_raw = []
    
    # Log what we received
    if corporate and (full_hits_raw or last_hits_raw):
        print(f"[AddressGeocoding] Corporate data provided: {len(full_hits_raw)} full hits, {len(last_hits_raw)} last hits")
    elif not identity or not queries:
        print(f"[AddressGeocoding] No identity data provided, only geocoding corporate addresses")
    else:
        print(f"[AddressGeocoding] No corporate data provided, only geocoding identity addresses")
    
    # Build query list from identity queries
    identity_queries = queries if queries else []
    
    # Convert corporate hits to query format (same as report generator does)
    corporate_queries = []
    if full_hits_raw:
        corporate_queries.append({"hits": full_hits_raw})
    if last_hits_raw:
        corporate_queries.append({"hits": last_hits_raw})
    
    # Combine all queries
    all_queries = identity_queries + corporate_queries
    
    if not all_queries:
        print(f"[AddressGeocoding] No queries or corporate hits provided, nothing to geocode")
        return {
            'addresses': {},
        }, 200
    
    print(f"[AddressGeocoding] Extracting addresses from {len(identity_queries)} identity queries and {len(corporate_queries)} corporate query sets")
    
    # Extract seed info if available for context
    seed = identity.get('seed') if isinstance(identity, dict) else None
    
    # Extract addresses from all queries using LLM
    addresses = extract_addresses_from_queries_llm(all_queries, seed=seed)
    
    if not addresses:
        print(f"[AddressGeocoding] No addresses found in queries")
        return {
            'addresses': {},
        }, 200
    
    print(f"[AddressGeocoding] Found {len(addresses)} unique addresses to geocode")
    
    geocoding_results = {}
    results_lock = threading.Lock()
    
    def geocode_single_address(addr_obj, index):
        """Geocode a single address with retry logic."""
        addr_raw = addr_obj.get('address_raw', '')
        cleaned = clean_address_for_geocoding(addr_raw)
        
        print(f"[AddressGeocoding] [{index}/{len(addresses)}] Geocoding: {cleaned[:60]}...")
        
        # Geocode with retry logic
        try:
            lat, lon = retry_with_backoff(
                lambda: geocode_address(cleaned),
                RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=10.0),
                operation_name=f"Geocoding: {cleaned[:50]}"
            )
            with results_lock:
                geocoding_results[addr_raw] = {
                    'lat': lat,
                    'lon': lon,
                    'cleaned': cleaned,
                    'error': None
                }
        except Exception as e:
            with results_lock:
                geocoding_results[addr_raw] = {
                    'lat': None,
                    'lon': None,
                    'cleaned': cleaned,
                    'error': f"Geocoding failed: {str(e)}"
                }
            print(f"[AddressGeocoding] Failed to geocode {cleaned[:50]}: {e}")
    
    # Process addresses in parallel, but space requests 1 second apart to respect rate limit
    # This allows requests to overlap while still respecting Nominatim's 1 req/sec policy
    with ThreadPoolExecutor(max_workers=len(addresses)) as executor:
        futures = []
        for i, addr_obj in enumerate(addresses, 1):
            # Schedule each request to start 1 second after the previous one
            if i > 1:
                time.sleep(1.0)  # Space requests 1 second apart
            
            future = executor.submit(geocode_single_address, addr_obj, i)
            futures.append(future)
        
        # Wait for all requests to complete
        for future in as_completed(futures):
            future.result()  # This will raise any exceptions that occurred
    
    print(f"[AddressGeocoding] Complete - geocoded {len([r for r in geocoding_results.values() if r.get('lat')])} of {len(addresses)} addresses")
    
    # Return data (like other phase2 functions), not write to Firestore
    return {
        'addresses': geocoding_results,
    }, 200

