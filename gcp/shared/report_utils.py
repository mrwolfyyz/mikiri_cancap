"""
Shared utility functions for report generators (origination and skiptrace).
Extracted from duplicated code in generate_markdown_reports.py and
generate_markdown_reports_skiptrace.py.

Copied to function directories by scripts/prepare-functions.sh.
"""

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

# Shared utilities (copied by prepare-functions.sh from gcp/shared/)
from address_utils import clean_address_for_geocoding
from domain_utils import extract_email_domain

# ------------------------------
# Text Utilities
# ------------------------------


def slugify(value: str) -> str:
    """
    Convert a string into a safe slug for tags:
    - lowercase
    - whitespace, '.', '@', '/' -> '_'
    - strip non-alphanumeric/underscore
    - collapse multiple underscores
    """
    if value is None:
        return "unknown"
    value = value.strip().lower()
    value = re.sub(r"[\s.@/]+", "_", value)
    value = re.sub(r"[^a-z0-9_]+", "", value)
    value = re.sub(r"_+", "_", value)
    value = value.strip("_")
    return value or "unknown"


# ------------------------------
# Geocoding & Street View
# ------------------------------


def geocode_address(address: str) -> tuple:
    """
    Geocode an address using free Nominatim (OpenStreetMap) API.
    Returns (lat, lon) tuple or (None, None) if geocoding fails.
    Respects rate limits with a small delay.
    """
    import json
    import time

    try:
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        # Nominatim requires a User-Agent
        url = f"https://nominatim.openstreetmap.org/search?q={quote_plus(address)}&format=json&limit=1"
        req = Request(url, headers={"User-Agent": "BorrowerIntelligence/1.0"})

        # Respect Nominatim rate limit (1 req/sec)
        time.sleep(1.1)

        with urlopen(req, timeout=30) as response:  # nosec B310 — hardcoded https URL
            data = json.loads(response.read().decode())
            if data and len(data) > 0:
                lat = float(data[0]["lat"])
                lon = float(data[0]["lon"])
                print(f"    ✓ Geocoded successfully: {lat:.6f}, {lon:.6f}")
                return (lat, lon)
            else:
                print("    ⚠️  No geocoding results found")
    except (URLError, HTTPError, KeyError, ValueError, json.JSONDecodeError) as e:
        print(f"    ⚠️  Geocoding failed: {e.__class__.__name__}")

    return (None, None)


def generate_street_view_url(address: str, geocode: bool = True, cached_coords: dict[str, float] = None) -> str:
    """
    Generate a Google Maps Street View URL for a given address.
    When coordinates are available, uses official map_action=pano format to open Street View directly.
    Falls back to search URL if no coordinates (search does not open Street View; user must click pegman).

    Args:
        address: Address string
        geocode: Whether to attempt geocoding
        cached_coords: Optional pre-fetched coordinates dict with 'lat' and 'lon' keys
    """
    # Use cached coordinates if available
    if cached_coords and cached_coords.get("lat") and cached_coords.get("lon"):
        lat = cached_coords["lat"]
        lon = cached_coords["lon"]
        print(f"    ✓ Using cached coordinates: {lat:.6f}, {lon:.6f}")
        return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat},{lon}"

    if geocode:
        # Clean the address to improve geocoding accuracy
        cleaned = clean_address_for_geocoding(address)
        lat, lon = geocode_address(cleaned)
        if lat and lon:
            # Direct Street View URL with coordinates (official map_action=pano format)
            return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat},{lon}"

    # Fallback: search URL (one click to Street View via pegman)
    encoded = quote_plus(address)
    return f"https://www.google.com/maps/search/{encoded}"


# ------------------------------
# Disposable Email Detection
# ------------------------------

# Module-level cache for disposable email blocklist (avoids re-reading from disk on every invocation)
_disposable_email_blocklist_cache = None


def load_disposable_email_blocklist(blocklist_path: Path) -> set:
    """Load disposable email domains from blocklist file. Cached at module level."""
    global _disposable_email_blocklist_cache
    if _disposable_email_blocklist_cache is not None:
        return _disposable_email_blocklist_cache

    domains = set()
    try:
        with open(blocklist_path, encoding="utf-8") as f:
            for line in f:
                domain = line.strip().lower()
                if domain and not domain.startswith("#"):
                    domains.add(domain)
        print(f"[Info] Loaded {len(domains)} disposable email domains from blocklist")
    except Exception as e:
        print(f"[Warning] Could not load disposable email blocklist: {e}")

    _disposable_email_blocklist_cache = domains
    return domains


def is_disposable_email_domain(email: str, blocklist: set) -> bool:
    """Check if email domain is in the disposable email blocklist."""
    domain = extract_email_domain(email)
    if not domain:
        return False
    return domain.lower() in blocklist


# ------------------------------
# Domain WHOIS Lookup
# ------------------------------


def get_domain_registration_date(domain: str) -> dict[str, Any]:
    """
    Perform whois lookup and extract registration date.
    Returns dict with 'success', 'registration_date', and 'error' fields.
    """
    try:
        from datetime import datetime

        import whois

        # Perform whois lookup with timeout
        w = whois.whois(domain)

        # Try multiple field names for registration date (different TLDs use different fields)
        creation_date = None
        for field_name in ["creation_date", "created", "registered", "registration_date", "domain_date_created"]:
            field_value = getattr(w, field_name, None)
            if field_value:
                creation_date = field_value
                break

        # If no standard field found, check the raw dict
        if not creation_date and hasattr(w, "__dict__"):
            for key in ["creation_date", "created", "registered", "registration_date", "domain_date_created"]:
                if key in w.__dict__ and w.__dict__[key]:
                    creation_date = w.__dict__[key]
                    break

        # For .ai domains (and other TLDs that don't parse dates), try parsing raw text
        if not creation_date and hasattr(w, "text") and w.text:
            import re

            date_patterns = [
                r"creation date[:\s]+(\d{4}-\d{2}-\d{2})",
                r"created[:\s]+(\d{4}-\d{2}-\d{2})",
                r"registration date[:\s]+(\d{4}-\d{2}-\d{2})",
                r"registered on[:\s]+(\d{4}-\d{2}-\d{2})",
                r"domain created[:\s]+(\d{4}-\d{2}-\d{2})",
                r"creation date[:\s]+(\d{2}/\d{2}/\d{4})",
                r"created[:\s]+(\d{2}/\d{2}/\d{4})",
            ]
            for pattern in date_patterns:
                match = re.search(pattern, w.text, re.IGNORECASE)
                if match:
                    date_str = match.group(1)
                    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y"]:
                        try:
                            creation_date = datetime.strptime(date_str, fmt)
                            break
                        except ValueError:
                            continue
                    if creation_date:
                        break

        if creation_date:
            # Handle list of dates (take first)
            if isinstance(creation_date, list):
                creation_date = creation_date[0]

            # Convert to datetime if it's a string
            if isinstance(creation_date, str):
                for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"]:
                    try:
                        creation_date = datetime.strptime(creation_date, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    try:
                        from dateutil import parser

                        creation_date = parser.parse(creation_date)
                    except Exception:  # nosec B110 — best-effort date parsing
                        pass

            # Format as YYYY-MM-DD
            if isinstance(creation_date, datetime):
                reg_date_str = creation_date.strftime("%Y-%m-%d")
                print(f"    ✓ Whois lookup successful: {domain} registered {reg_date_str}")
                return {"success": True, "registration_date": reg_date_str, "error": None}

        print(f"    ⚠️  No registration date found for {domain}")
        return {"success": False, "registration_date": None, "error": "No registration date in whois data"}

    except ImportError:
        print("    ⚠️  Whois lookup failed: python-whois not available")
        return {"success": False, "registration_date": None, "error": "python-whois library not installed"}
    except Exception as e:
        error_msg = str(e)
        print(f"    ⚠️  Whois lookup failed: {e.__class__.__name__}: {error_msg}")

        # Try to parse creation date from error message
        import re

        date_patterns = [
            r"creation date[:\s]+(\d{4}-\d{2}-\d{2})T?\d*:?\d*:?\d*Z?",
            r"creation date[:\s]+(\d{4}-\d{2}-\d{2})",
            r"created[:\s]+(\d{4}-\d{2}-\d{2})T?\d*:?\d*:?\d*Z?",
            r"created[:\s]+(\d{4}-\d{2}-\d{2})",
        ]

        for pattern in date_patterns:
            match = re.search(pattern, error_msg, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                try:
                    creation_date = datetime.strptime(date_str, "%Y-%m-%d")
                    reg_date_str = creation_date.strftime("%Y-%m-%d")
                    print(f"    ✓ Extracted registration date from error message: {domain} registered {reg_date_str}")
                    return {"success": True, "registration_date": reg_date_str, "error": None}
                except ValueError:
                    continue

        return {"success": False, "registration_date": None, "error": error_msg}


# ------------------------------
# Gravatar Profile Lookup
# ------------------------------


def get_gravatar_profile(email: str) -> dict[str, Any]:
    """
    Query Gravatar API for profile information.
    Returns dict with 'success', 'profile_url', 'thumbnail_url', and 'error' fields.
    """
    try:
        import hashlib
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        normalized_email = email.lower().strip()
        if not normalized_email or "@" not in normalized_email:
            return {"success": False, "profile_url": None, "thumbnail_url": None, "error": "Invalid email address"}

        email_hash = hashlib.md5(normalized_email.encode("utf-8"), usedforsecurity=False).hexdigest()  # nosec B324
        gravatar_url = f"https://www.gravatar.com/{email_hash}.json"
        req = Request(gravatar_url, headers={"User-Agent": "BorrowerIntelligence/1.0"})

        with urlopen(req, timeout=10) as response:  # nosec B310 — hardcoded https URL
            data = json.loads(response.read().decode())

            if data and "entry" in data and len(data["entry"]) > 0:
                profile_url = f"https://www.gravatar.com/{email_hash}"
                thumbnail_url = f"https://www.gravatar.com/avatar/{email_hash}?s=80"

                print(f"    ✓ Gravatar profile found for {normalized_email}")
                return {"success": True, "profile_url": profile_url, "thumbnail_url": thumbnail_url, "error": None}
            else:
                print(f"    ⚠️  No Gravatar profile found for {normalized_email}")
                return {"success": False, "profile_url": None, "thumbnail_url": None, "error": "No profile found"}

    except HTTPError as e:
        if e.code == 404:
            print(f"    ⚠️  No Gravatar profile found for {email}")
            return {"success": False, "profile_url": None, "thumbnail_url": None, "error": "Profile not found (404)"}
        else:
            print(f"    ⚠️  Gravatar lookup failed: HTTP {e.code}")
            return {"success": False, "profile_url": None, "thumbnail_url": None, "error": f"HTTP {e.code}"}
    except (URLError, json.JSONDecodeError, ValueError) as e:
        print(f"    ⚠️  Gravatar lookup failed: {e.__class__.__name__}")
        return {"success": False, "profile_url": None, "thumbnail_url": None, "error": str(e)}
    except Exception as e:
        print(f"    ⚠️  Gravatar lookup failed: {e.__class__.__name__}: {str(e)}")
        return {"success": False, "profile_url": None, "thumbnail_url": None, "error": str(e)}


# ------------------------------
# MX Record Analysis
# ------------------------------


def check_domain_mx_records(domain: str) -> dict[str, Any]:
    """
    Analyze a domain's MX records to determine if it uses legitimate
    business email infrastructure or default/parked services.
    Returns dict with 'success', 'status', 'provider_detected', 'mx_records', 'risk_level', and 'error' fields.
    """
    HIGH_TRUST_PROVIDERS = {
        "google.com": "Google Workspace (High Trust)",
        "googlemail.com": "Google Workspace (High Trust)",
        "outlook.com": "Microsoft 365 (High Trust)",
        "microsoft.com": "Microsoft 365 (High Trust)",
        "pphosted.com": "Proofpoint (Enterprise Security - High Trust)",
        "mimecast.com": "Mimecast (Enterprise Security - High Trust)",
        "messagelabs.com": "Symantec/Broadcom (Enterprise Security - High Trust)",
        "barracudanetworks.com": "Barracuda Networks (Enterprise Security - High Trust)",
        "barracuda.com": "Barracuda Networks (Enterprise Security - High Trust)",
        "ironport.com": "Cisco IronPort (Enterprise Security - High Trust)",
        "mailcontrol.com": "Forcepoint (Enterprise Security - High Trust)",
        "trendmicro.com": "Trend Micro (Enterprise Security - High Trust)",
        "sophos.com": "Sophos (Enterprise Security - High Trust)",
        "fortinet.com": "Fortinet (Enterprise Security - High Trust)",
        "checkpoint.com": "Check Point (Enterprise Security - High Trust)",
        "zix.com": "Zix (Enterprise Security - High Trust)",
        "appriver.com": "AppRiver (Enterprise Security - High Trust)",
        "reflexion.net": "Reflexion (Enterprise Security - High Trust)",
    }

    STANDARD_TRUST_PROVIDERS = {
        "zoho.com": "Zoho Mail (Standard Business)",
        "protonmail": "ProtonMail (Privacy/Standard)",
        "fastmail.com": "Fastmail (Standard)",
        "rackspace.com": "Rackspace Email (Standard)",
        "intermedia.net": "Intermedia (Standard)",
        "hostedemail.com": "OpenSRS/Tucows (Reseller Email - Likely Legit Small Biz)",
        "dreamhost.com": "DreamHost Email (Hosting Provider - Likely Legit Small Biz)",
    }

    LOW_TRUST_FLAGS = {
        "secureserver.net": "GoDaddy Default (Often unused/forwarding only)",
        "registrar-servers.com": "Namecheap Default (Forwarding/Parked)",
        "name-services.com": "eNom Default (Parking/Forwarding)",
        "domaincontrol.com": "GoDaddy DNS (Generic/Default)",
        "parked": "Domain Parking Service",
        "sedoparking": "Sedo (Domain for Sale/Parked)",
        "parklogic": "ParkLogic (Domain Parking)",
        "bodis.com": "Bodis (Domain Parking)",
    }

    results = {
        "success": False,
        "domain": domain,
        "status": "Unknown",
        "provider_detected": None,
        "mx_records": [],
        "risk_level": "UNKNOWN",
        "error": None,
    }

    try:
        import dns.resolver

        answers = dns.resolver.resolve(domain, "MX")
        sorted_mx = sorted(answers, key=lambda r: r.preference)

        for rdata in sorted_mx:
            mx_value = rdata.exchange.to_text().lower().strip(".")
            results["mx_records"].append(mx_value)

        if not results["mx_records"]:
            results["status"] = "No MX Records Found"
            results["risk_level"] = "CRITICAL"
            results["error"] = "Domain has no MX records"
            return results

        primary_mx = results["mx_records"][0]

        for sig, name in HIGH_TRUST_PROVIDERS.items():
            if sig in primary_mx:
                results.update(
                    {
                        "success": True,
                        "status": "Legitimate Business Email",
                        "provider_detected": name,
                        "risk_level": "LOW",
                    }
                )
                print(f"    ✓ MX lookup successful: {domain} uses {name}")
                return results

        for sig, name in STANDARD_TRUST_PROVIDERS.items():
            if sig in primary_mx:
                results.update(
                    {
                        "success": True,
                        "status": "Standard Business Email",
                        "provider_detected": name,
                        "risk_level": "LOW/MEDIUM",
                    }
                )
                print(f"    ✓ MX lookup successful: {domain} uses {name}")
                return results

        for sig, name in LOW_TRUST_FLAGS.items():
            if sig in primary_mx:
                results.update(
                    {
                        "success": True,
                        "status": "Registrar Default / Parked",
                        "provider_detected": name,
                        "risk_level": "HIGH",
                    }
                )
                print(f"    ⚠️  MX lookup: {domain} uses {name}")
                return results

        if domain in primary_mx:
            results.update(
                {
                    "success": True,
                    "status": "Self-Hosted / Local Hosting",
                    "provider_detected": "Private Server (e.g., cPanel/Exchange)",
                    "risk_level": "MEDIUM",
                }
            )
            print(f"    ✓ MX lookup: {domain} uses self-hosted email")
            return results

        results.update(
            {
                "success": True,
                "status": "Unknown Email Provider",
                "provider_detected": f"Unrecognized provider: {primary_mx}",
                "risk_level": "MEDIUM",
            }
        )
        print(f"    ⚠️  MX lookup: {domain} uses unrecognized provider: {primary_mx}")
        return results

    except ImportError:
        print("    ⚠️  MX lookup failed: dnspython not available")
        results["error"] = "dnspython library not installed"
        return results
    except dns.resolver.NoAnswer:
        results.update({"status": "No Email Configured", "risk_level": "CRITICAL", "error": "Domain has no MX records"})
        print(f"    ⚠️  MX lookup: {domain} has no MX records")
        return results
    except dns.resolver.NXDOMAIN:
        results.update({"status": "Domain Not Found", "risk_level": "CRITICAL", "error": "Domain does not exist"})
        print(f"    ⚠️  MX lookup failed: {domain} does not exist")
        return results
    except Exception as e:
        print(f"    ⚠️  MX lookup failed: {e.__class__.__name__}: {str(e)}")
        results["error"] = str(e)
        return results


# ------------------------------
# Address Helper Functions
# ------------------------------


def normalize_address(address: str) -> str:
    """
    Normalize address for deduplication purposes.
    - Lowercase
    - Remove commas and extra whitespace
    - Standardize abbreviations (St->street, Ave->avenue, etc.)
    """
    addr = address.lower().strip()
    addr = re.sub(r",", " ", addr)
    addr = re.sub(r"\s+", " ", addr)

    replacements = {
        r"\bst\b": "street",
        r"\bave\b": "avenue",
        r"\bavenue\b": "avenue",
        r"\brd\b": "road",
        r"\bdr\b": "drive",
        r"\bblvd\b": "boulevard",
        r"\bln\b": "lane",
        r"\bct\b": "court",
        r"\bpl\b": "place",
        r"\bter\b": "terrace",
        r"\bpkwy\b": "parkway",
        r"\bcir\b": "circle",
    }

    for pattern, replacement in replacements.items():
        addr = re.sub(pattern, replacement, addr)

    return addr.strip()


def generate_google_search_url(addr_data: dict[str, Any]) -> str:
    """Generate standard Google search URL with address as query."""
    address = addr_data.get("address_raw", "")
    return f"https://www.google.com/search?q={quote_plus(address)}"


# ------------------------------
# Phone Number Helper Functions
# ------------------------------


def generate_phone_variations(phone_data: dict[str, str]) -> list[str]:
    """
    Generate common phone number format variations for search.
    Takes phone data with number_digits and returns list of formatted variations.
    """
    digits = phone_data.get("number_digits", "")
    if not digits:
        return []

    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]

    if len(digits) != 10:
        return []

    area_code = digits[0:3]
    prefix = digits[3:6]
    line_number = digits[6:10]

    return [
        f"{area_code}-{prefix}-{line_number}",
        f"({area_code}) {prefix}-{line_number}",
        f"{area_code}.{prefix}.{line_number}",
        f"+1 {area_code} {prefix} {line_number}",
    ]


def generate_google_search_url_for_phone(phone_data: dict[str, str]) -> str:
    """Generate Google search URL with phone number variations using OR operator."""
    variations = generate_phone_variations(phone_data)
    if not variations:
        number_raw = phone_data.get("number_raw", "")
        return f"https://www.google.com/search?q={quote_plus(number_raw)}"

    query = " | ".join(f'"{v}"' for v in variations)
    return f"https://www.google.com/search?q={quote_plus(query)}"


# ------------------------------
# Email Helper Functions
# ------------------------------


def generate_google_search_url_for_email(email: str) -> str:
    """Generate Google search URL with email as query."""
    return f"https://www.google.com/search?q={quote_plus(email)}"
