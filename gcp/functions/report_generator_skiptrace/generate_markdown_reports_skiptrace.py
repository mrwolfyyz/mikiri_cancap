#!/usr/bin/env python3
"""
Generate Markdown reports from borrower investigation JSON data.
"""

import json
import sys
import re
import os
import pyap

from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import quote_plus

# Vertex AI imports
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig
from retry_utils import retry_with_backoff, RetryConfig, EmptyLLMResponseError

# -------------------------
# Vertex AI Config
# -------------------------
GCP_PROJECT = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")


def format_name(name: str) -> str:
    """Format name to title case for display."""
    return name.title()


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
    value = re.sub(r'[\s.@/]+', '_', value)
    value = re.sub(r'[^a-z0-9_]+', '', value)
    value = re.sub(r'_+', '_', value)
    value = value.strip('_')
    return value or "unknown"



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
    import time
    import json
    try:
        from urllib.request import urlopen, Request
        from urllib.error import URLError, HTTPError
        
        # Nominatim requires a User-Agent
        url = f"https://nominatim.openstreetmap.org/search?q={quote_plus(address)}&format=json&limit=1"
        req = Request(url, headers={'User-Agent': 'BorrowerIntelligence/1.0'})
        
        # Respect Nominatim rate limit (1 req/sec)
        time.sleep(1.1)
        
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


def generate_street_view_url(address: str, geocode: bool = True, cached_coords: Dict[str, float] = None) -> str:
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
    if cached_coords and cached_coords.get('lat') and cached_coords.get('lon'):
        lat = cached_coords['lat']
        lon = cached_coords['lon']
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
# Personal Email Domains
# ------------------------------

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


# ------------------------------
# Domain Whois Functions
# ------------------------------

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


def load_disposable_email_blocklist(blocklist_path: Path) -> set:
    """Load disposable email domains from blocklist file."""
    domains = set()
    try:
        with open(blocklist_path, 'r', encoding='utf-8') as f:
            for line in f:
                domain = line.strip().lower()
                if domain and not domain.startswith('#'):
                    domains.add(domain)
    except Exception as e:
        print(f"[Warning] Could not load disposable email blocklist: {e}")
    return domains


def is_disposable_email_domain(email: str, blocklist: set) -> bool:
    """Check if email domain is in the disposable email blocklist."""
    domain = extract_email_domain(email)
    if not domain:
        return False
    return domain.lower() in blocklist


def get_domain_registration_date(domain: str) -> Dict[str, Any]:
    """
    Perform whois lookup and extract registration date.
    Returns dict with 'success', 'registration_date', and 'error' fields.
    Similar error handling pattern to geocode_address().
    """
    try:
        import whois
        from datetime import datetime
        
        # Perform whois lookup with timeout
        w = whois.whois(domain)
        
        # Try multiple field names for registration date (different TLDs use different fields)
        creation_date = None
        for field_name in ['creation_date', 'created', 'registered', 'registration_date', 'domain_date_created']:
            field_value = getattr(w, field_name, None)
            if field_value:
                creation_date = field_value
                break
        
        # If no standard field found, check the raw dict
        if not creation_date and hasattr(w, '__dict__'):
            for key in ['creation_date', 'created', 'registered', 'registration_date', 'domain_date_created']:
                if key in w.__dict__ and w.__dict__[key]:
                    creation_date = w.__dict__[key]
                    break
        
        # For .ai domains (and other TLDs that don't parse dates), try parsing raw text
        if not creation_date and hasattr(w, 'text') and w.text:
            import re
            # Look for creation date patterns in whois text, prioritizing "Creation Date" field
            # Patterns: "Creation Date: 2025-06-24T19:33:23Z", "Created: 2023-01-15", etc.
            date_patterns = [
                r'creation date[:\s]+(\d{4}-\d{2}-\d{2})',  # Prioritize "Creation Date" field
                r'created[:\s]+(\d{4}-\d{2}-\d{2})',
                r'registration date[:\s]+(\d{4}-\d{2}-\d{2})',
                r'registered on[:\s]+(\d{4}-\d{2}-\d{2})',
                r'domain created[:\s]+(\d{4}-\d{2}-\d{2})',
                r'creation date[:\s]+(\d{2}/\d{2}/\d{4})',
                r'created[:\s]+(\d{2}/\d{2}/\d{4})',
            ]
            for pattern in date_patterns:
                match = re.search(pattern, w.text, re.IGNORECASE)
                if match:
                    date_str = match.group(1)
                    # Try to parse the date
                    for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%d-%m-%Y']:
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
                # Try to parse common date formats
                for fmt in ['%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%d-%b-%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ']:
                    try:
                        creation_date = datetime.strptime(creation_date, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    # If parsing fails, try whois library's parser or dateutil
                    try:
                        from dateutil import parser
                        creation_date = parser.parse(creation_date)
                    except Exception:
                        pass
            
            # Format as YYYY-MM-DD
            if isinstance(creation_date, datetime):
                reg_date_str = creation_date.strftime("%Y-%m-%d")
                print(f"    ✓ Whois lookup successful: {domain} registered {reg_date_str}")
                return {
                    "success": True,
                    "registration_date": reg_date_str,
                    "error": None
                }
        
        print(f"    ⚠️  No registration date found for {domain}")
        return {
            "success": False,
            "registration_date": None,
            "error": "No registration date in whois data"
        }
        
    except ImportError:
        print(f"    ⚠️  Whois lookup failed: python-whois not available")
        return {
            "success": False,
            "registration_date": None,
            "error": "python-whois library not installed"
        }
    except Exception as e:
        error_msg = str(e)
        print(f"    ⚠️  Whois lookup failed: {e.__class__.__name__}: {error_msg}")
        
        # Try to parse creation date from error message (some whois libraries return data in error)
        import re
        date_patterns = [
            r'creation date[:\s]+(\d{4}-\d{2}-\d{2})T?\d*:?\d*:?\d*Z?',  # ISO format with optional time
            r'creation date[:\s]+(\d{4}-\d{2}-\d{2})',  # Date only
            r'created[:\s]+(\d{4}-\d{2}-\d{2})T?\d*:?\d*:?\d*Z?',
            r'created[:\s]+(\d{4}-\d{2}-\d{2})',
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, error_msg, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                try:
                    creation_date = datetime.strptime(date_str, "%Y-%m-%d")
                    reg_date_str = creation_date.strftime("%Y-%m-%d")
                    print(f"    ✓ Extracted registration date from error message: {domain} registered {reg_date_str}")
                    return {
                        "success": True,
                        "registration_date": reg_date_str,
                        "error": None
                    }
                except ValueError:
                    continue
        
        return {
            "success": False,
            "registration_date": None,
            "error": error_msg
        }


def get_gravatar_profile(email: str) -> Dict[str, Any]:
    """
    Query Gravatar API for profile information.
    Returns dict with 'success', 'profile_url', 'thumbnail_url', and 'error' fields.
    Similar error handling pattern to geocode_address() and get_domain_registration_date().
    """
    try:
        import hashlib
        from urllib.request import urlopen, Request
        from urllib.error import URLError, HTTPError
        
        # Normalize email: lowercase and trim whitespace
        normalized_email = email.lower().strip()
        if not normalized_email or "@" not in normalized_email:
            return {
                "success": False,
                "profile_url": None,
                "thumbnail_url": None,
                "error": "Invalid email address"
            }
        
        # Generate MD5 hash of normalized email
        email_hash = hashlib.md5(normalized_email.encode('utf-8')).hexdigest()
        
        # Gravatar JSON API endpoint
        gravatar_url = f"https://www.gravatar.com/{email_hash}.json"
        
        # Make request with User-Agent (some APIs require it)
        req = Request(gravatar_url, headers={'User-Agent': 'BorrowerIntelligence/1.0'})
        
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            
            # Check if profile exists (Gravatar returns {"entry": [...]} if found)
            if data and 'entry' in data and len(data['entry']) > 0:
                profile_url = f"https://www.gravatar.com/{email_hash}"
                thumbnail_url = f"https://www.gravatar.com/avatar/{email_hash}?s=80"
                
                print(f"    ✓ Gravatar profile found for {normalized_email}")
                return {
                    "success": True,
                    "profile_url": profile_url,
                    "thumbnail_url": thumbnail_url,
                    "error": None
                }
            else:
                print(f"    ⚠️  No Gravatar profile found for {normalized_email}")
                return {
                    "success": False,
                    "profile_url": None,
                    "thumbnail_url": None,
                    "error": "No profile found"
                }
                
    except HTTPError as e:
        if e.code == 404:
            # 404 means no profile exists - this is expected for many emails
            print(f"    ⚠️  No Gravatar profile found for {email}")
            return {
                "success": False,
                "profile_url": None,
                "thumbnail_url": None,
                "error": "Profile not found (404)"
            }
        else:
            print(f"    ⚠️  Gravatar lookup failed: HTTP {e.code}")
            return {
                "success": False,
                "profile_url": None,
                "thumbnail_url": None,
                "error": f"HTTP {e.code}"
            }
    except (URLError, json.JSONDecodeError, ValueError) as e:
        print(f"    ⚠️  Gravatar lookup failed: {e.__class__.__name__}")
        return {
            "success": False,
            "profile_url": None,
            "thumbnail_url": None,
            "error": str(e)
        }
    except Exception as e:
        print(f"    ⚠️  Gravatar lookup failed: {e.__class__.__name__}: {str(e)}")
        return {
            "success": False,
            "profile_url": None,
            "thumbnail_url": None,
            "error": str(e)
        }


def check_domain_mx_records(domain: str) -> Dict[str, Any]:
    """
    Analyze a domain's MX records to determine if it uses legitimate 
    business email infrastructure or default/parked services.
    Returns dict with 'success', 'status', 'provider_detected', 'mx_records', 'risk_level', and 'error' fields.
    Similar error handling pattern to get_domain_registration_date().
    """
    # 1. High Trust: Enterprise-grade services used by established businesses
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
        "reflexion.net": "Reflexion (Enterprise Security - High Trust)"
    }

    # 2. Standard Trust: Legitimate paid/pro email services (Small Biz)
    STANDARD_TRUST_PROVIDERS = {
        "zoho.com": "Zoho Mail (Standard Business)",
        "protonmail": "ProtonMail (Privacy/Standard)",
        "fastmail.com": "Fastmail (Standard)",
        "rackspace.com": "Rackspace Email (Standard)",
        "intermedia.net": "Intermedia (Standard)",
        "hostedemail.com": "OpenSRS/Tucows (Reseller Email - Likely Legit Small Biz)",
        "dreamhost.com": "DreamHost Email (Hosting Provider - Likely Legit Small Biz)"
    }

    # 3. Low Trust / Risk Flags: Default registrar pages, parking services, or forwarding
    LOW_TRUST_FLAGS = {
        "secureserver.net": "GoDaddy Default (Often unused/forwarding only)",
        "registrar-servers.com": "Namecheap Default (Forwarding/Parked)",
        "name-services.com": "eNom Default (Parking/Forwarding)",
        "domaincontrol.com": "GoDaddy DNS (Generic/Default)",
        "parked": "Domain Parking Service",
        "sedoparking": "Sedo (Domain for Sale/Parked)",
        "parklogic": "ParkLogic (Domain Parking)",
        "bodis.com": "Bodis (Domain Parking)"
    }

    results = {
        "success": False,
        "domain": domain,
        "status": "Unknown",
        "provider_detected": None,
        "mx_records": [],
        "risk_level": "UNKNOWN",
        "error": None
    }

    try:
        import dns.resolver
        
        # Fetch MX records
        answers = dns.resolver.resolve(domain, 'MX')
        
        # Sort by priority (lowest number is primary)
        sorted_mx = sorted(answers, key=lambda r: r.preference)
        
        for rdata in sorted_mx:
            mx_value = rdata.exchange.to_text().lower().strip('.')
            results["mx_records"].append(mx_value)

        # ANALYSIS LOGIC
        if not results["mx_records"]:
            results["status"] = "No MX Records Found"
            results["risk_level"] = "CRITICAL"
            results["error"] = "Domain has no MX records"
            return results

        primary_mx = results["mx_records"][0]
        
        # Check High Trust
        for sig, name in HIGH_TRUST_PROVIDERS.items():
            if sig in primary_mx:
                results["success"] = True
                results["status"] = "Legitimate Business Email"
                results["provider_detected"] = name
                results["risk_level"] = "LOW"
                print(f"    ✓ MX lookup successful: {domain} uses {name}")
                return results

        # Check Standard Trust
        for sig, name in STANDARD_TRUST_PROVIDERS.items():
            if sig in primary_mx:
                results["success"] = True
                results["status"] = "Standard Business Email"
                results["provider_detected"] = name
                results["risk_level"] = "LOW/MEDIUM"
                print(f"    ✓ MX lookup successful: {domain} uses {name}")
                return results

        # Check Low Trust / Parking
        for sig, name in LOW_TRUST_FLAGS.items():
            if sig in primary_mx:
                results["success"] = True
                results["status"] = "Registrar Default / Parked"
                results["provider_detected"] = name
                results["risk_level"] = "HIGH"
                print(f"    ⚠️  MX lookup: {domain} uses {name}")
                return results

        # If it points to the domain itself (e.g., mail.custom-domain.com)
        # This is likely a private server or cPanel hosting. Harder to verify, but usually not "Parking".
        if domain in primary_mx:
            results["success"] = True
            results["status"] = "Self-Hosted / Local Hosting"
            results["provider_detected"] = "Private Server (e.g., cPanel/Exchange)"
            results["risk_level"] = "MEDIUM"
            print(f"    ✓ MX lookup: {domain} uses self-hosted email")
            return results

        # Unknown/Unrecognized MX record
        results["success"] = True
        results["status"] = "Unknown Email Provider"
        results["provider_detected"] = f"Unrecognized provider: {primary_mx}"
        results["risk_level"] = "MEDIUM"
        print(f"    ⚠️  MX lookup: {domain} uses unrecognized provider: {primary_mx}")
        return results

    except ImportError:
        print(f"    ⚠️  MX lookup failed: dnspython not available")
        results["error"] = "dnspython library not installed"
        return results
    except dns.resolver.NoAnswer:
        results["status"] = "No Email Configured"
        results["risk_level"] = "CRITICAL"
        results["error"] = "Domain has no MX records"
        print(f"    ⚠️  MX lookup: {domain} has no MX records")
        return results
    except dns.resolver.NXDOMAIN:
        results["status"] = "Domain Not Found"
        results["risk_level"] = "CRITICAL"
        results["error"] = "Domain does not exist"
        print(f"    ⚠️  MX lookup failed: {domain} does not exist")
        return results
    except Exception as e:
        print(f"    ⚠️  MX lookup failed: {e.__class__.__name__}: {str(e)}")
        results["error"] = str(e)
        return results


def get_mx_callout(mx_result: Dict[str, Any]) -> str:
    """
    Return appropriate call-out type based on MX record risk level.
    Maps risk levels to Obsidian callout types:
    - CRITICAL or HIGH → "danger"
    - MEDIUM or LOW/MEDIUM → "warning"
    - LOW → "info"
    - UNKNOWN or errors → "warning"
    """
    if not mx_result or not mx_result.get("success"):
        return "warning"
    
    risk_level = mx_result.get("risk_level", "UNKNOWN")
    
    if risk_level in ["CRITICAL", "HIGH"]:
        return "danger"
    elif risk_level in ["MEDIUM", "LOW/MEDIUM"]:
        return "warning"
    elif risk_level == "LOW":
        return "info"
    else:
        return "warning"




def get_domain_age_callout(registration_date: str) -> str:
    """
    Return appropriate call-out type based on domain age.
    - Less than 90 days: danger (most serious)
    - Less than 1 year: warning (needs flagging)
    - 1 year or older: info (informational)
    """
    try:
        # Parse the registration date (datetime is imported at module level)
        reg_date = datetime.strptime(registration_date, "%Y-%m-%d")
        today = datetime.now()
        age_days = (today - reg_date).days
        
        if age_days < 90:
            return "danger"
        elif age_days < 365:
            return "warning"
        else:
            return "info"
    except Exception:
        # If we can't calculate age, default to info
        return "info"


def get_regulator_callout(num_events: int) -> str:
    """Return appropriate call-out type based on regulator events."""
    if num_events == 0:
        return "tip"
    elif num_events <= 2:
        return "warning"
    else:
        return "danger"


def get_litigation_callout(num_events: int) -> str:
    """Return appropriate call-out type based on litigation events."""
    if num_events == 0:
        return "tip"
    elif num_events <= 3:
        return "warning"
    else:
        return "danger"


def get_corporate_callout(num_total: int) -> str:
    """Return appropriate call-out type based on total corporations."""
    if num_total == 0:
        return "tip"
    elif num_total <= 2:
        return "warning"
    else:
        return "danger"


def get_regulator_label(num_events: int) -> str:
    """Return contextual label for regulator section."""
    if num_events == 0:
        return "✓ No regulatory events found"
    elif num_events <= 2:
        return f"⚠️ {num_events} regulatory event(s) requiring review"
    else:
        return f"⚠️ Significant regulatory history: {num_events} events"


def get_litigation_label(num_events: int) -> str:
    """Return contextual label for litigation section."""
    if num_events == 0:
        return "✓ No adverse media found"
    elif num_events <= 2:
        return f"Limited adverse media history: {num_events} event(s)"
    elif num_events <= 4:
        return f"Multiple disputes: {num_events} events"
    else:
        return f"⚠️ Extensive adverse media history: {num_events} events"


def get_corporate_label(num_direct: int, num_family: int) -> str:
    """Return contextual label for corporate section."""
    num_total = num_direct + num_family
    if num_total == 0:
        return "✓ No corporate directorships found"
    elif num_total <= 3:
        return f"Limited corporate activity: {num_total} corporation(s)"
    elif num_total <= 7:
        return f"Moderate corporate footprint: {num_total} corporations"
    else:
        return f"⚠️ Extensive corporate network: {num_total} corporations"


def get_regulator_risk_level(num_events: int) -> str:
    """Map regulator event count to risk/regulator/<level>."""
    if num_events == 0:
        return "none"
    elif num_events <= 2:
        return "low"
    else:
        return "high"


def get_litigation_risk_level(num_events: int) -> str:
    """Map litigation event count to risk/litigation/<level>."""
    if num_events == 0:
        return "none"
    elif num_events <= 2:
        return "low"
    elif num_events <= 4:
        return "medium"
    else:
        return "high"


def get_corporate_risk_level(num_total: int) -> str:
    """Map corporate footprint to risk/corporate/<level>."""
    if num_total == 0:
        return "none"
    elif num_total <= 3:
        return "limited"
    elif num_total <= 7:
        return "moderate"
    else:
        return "extensive"

def get_navigation_bar(data: Dict[str, Any], name: str, current_report: str) -> str:
    """
    Generate a navigation bar showing all reports with status indicators.
    current_report should be one of: 'identity', 'corporate', 'litigation', 'regulator', 'skiptrace'
    Note: 'litigation' refers to adverse media internally but displays as 'Adverse Media' to users.
    """
    wiki_name = name.replace(' ', '_')
    
    # Calculate all metrics
    num_regulator = len(data['regulator_phase2']['confirmed_regulator_hits'])
    num_corporate_direct = data['corporate_debug']['num_direct']
    num_corporate_family = data['corporate_debug']['num_family']
    num_corporate_total = num_corporate_direct + num_corporate_family
    num_litigation = len(data.get('litigation_phase2', {}).get('confirmed_litigation_hits', []))
    
    # Determine status icons
    # Identity is always "verified" if we have a report
    identity_status = "🟢 Identity"
    
    # Regulator
    if num_regulator == 0:
        regulator_status = "🟢 Regulator"
    elif num_regulator <= 2:
        regulator_status = f"🟡 Regulator ({num_regulator})"
    else:
        regulator_status = f"🔴 Regulator ({num_regulator})"
    
    # Corporate
    if num_corporate_total == 0:
        corporate_status = "🟢 Corporate"
    elif num_corporate_total <= 2:
        corporate_status = f"🟡 Corporate ({num_corporate_total})"
    else:
        corporate_status = f"🔴 Corporate ({num_corporate_total})"
    
    # Litigation
    if num_litigation == 0:
        litigation_status = "🟢 Adverse Media"
    elif num_litigation <= 3:
        litigation_status = f"🟡 Adverse Media ({num_litigation})"
    else:
        litigation_status = f"🔴 Adverse Media ({num_litigation})"
    
    # Build navigation with current report highlighted
    nav_items = {
        'identity': f"[[Identity___{wiki_name}|{identity_status}]]",
        'regulator': f"[[Regulator___{wiki_name}|{regulator_status}]]",
        'corporate': f"[[Corporate___{wiki_name}|{corporate_status}]]",
        'litigation': f"[[Adverse_Media___{wiki_name}|{litigation_status}]]",
    }

    # Bold the current report
    if current_report in nav_items:
        nav_items[current_report] = f"**{nav_items[current_report]}**"

    nav_bar = f"""> [!abstract] -
> **← [[Borrower_Summary_-_{wiki_name}|Back to Summary]]**
> {nav_items['identity']} • {nav_items['regulator']} • {nav_items['corporate']} • {nav_items['litigation']}

---

"""
    return nav_bar

def load_json(filepath: str) -> Dict[str, Any]:
    """Load JSON data from file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_linkedin_connections(snippet: str) -> Optional[int]:
    """
    Extract LinkedIn connection count from snippet text.
    Handles patterns like "500+ connections", "1K+ connections", "connected to 500", etc.
    Returns integer count or None if not found.
    """
    if not snippet:
        return None
    
    import re
    
    # Pattern 1: "500+ connections" or "10 connections" or "500+ connection"
    pattern1 = r'(\d+)\s*\+?\s*connections?'
    match1 = re.search(pattern1, snippet, re.IGNORECASE)
    if match1:
        count = int(match1.group(1))
        return count
    
    # Pattern 2: "1K+ connections" or "5K connections"
    pattern2 = r'(\d+)K\s*\+?\s*connections?'
    match2 = re.search(pattern2, snippet, re.IGNORECASE)
    if match2:
        count = int(match2.group(1)) * 1000
        return count
    
    # Pattern 3: "connected to 500" or "connected with 500"
    pattern3 = r'connected\s+(?:to|with)\s+(\d+)'
    match3 = re.search(pattern3, snippet, re.IGNORECASE)
    if match3:
        count = int(match3.group(1))
        return count
    
    # Pattern 4: "500 connections" (without +)
    pattern4 = r'(\d+)\s+connections?'
    match4 = re.search(pattern4, snippet, re.IGNORECASE)
    if match4:
        count = int(match4.group(1))
        return count
    
    return None


def get_linkedin_snippet(top_handles: List[Dict[str, Any]], queries: List[Dict[str, Any]]) -> Optional[str]:
    """
    Get snippet for LinkedIn profile from top_handles and queries.
    Returns snippet string or None if LinkedIn profile not found.
    (Deprecated: use get_all_linkedin_snippets() to check multiple profiles)
    """
    if not top_handles:
        return None
    
    # Find LinkedIn profile in top_handles
    linkedin_handle = None
    for handle in top_handles:
        platform = handle.get('platform', '').lower()
        url = handle.get('url', '').lower()
        if platform == 'linkedin' or 'linkedin.com' in url:
            linkedin_handle = handle
            break
    
    if not linkedin_handle:
        return None
    
    # Extract snippet using same logic as identity report
    url = linkedin_handle.get('url', '')
    snippet = None
    for query in queries or []:
        for hit in query.get('hits', []):
            hit_url = hit.get('url', '').lower()
            if url.lower() == hit_url or (hit_url and hit_url.rstrip('/') == url.lower().rstrip('/')):
                snippet = hit.get('snippet', '')
                break
        if snippet:
            break
    
    return snippet


def get_all_linkedin_snippets(top_handles: List[Dict[str, Any]], queries: List[Dict[str, Any]]) -> List[str]:
    """
    Get snippets for all LinkedIn profiles from top_handles and queries.
    Returns list of snippet strings (may be empty if no LinkedIn profiles found).
    """
    if not top_handles:
        return []
    
    # Find all LinkedIn profiles in top_handles
    linkedin_handles = []
    for handle in top_handles:
        platform = handle.get('platform', '').lower()
        url = handle.get('url', '').lower()
        if platform == 'linkedin' or 'linkedin.com' in url:
            linkedin_handles.append(handle)
    
    if not linkedin_handles:
        return []
    
    # Extract snippets for all LinkedIn profiles
    snippets = []
    for linkedin_handle in linkedin_handles:
        url = linkedin_handle.get('url', '')
        snippet = None
        for query in queries or []:
            for hit in query.get('hits', []):
                hit_url = hit.get('url', '').lower()
                if url.lower() == hit_url or (hit_url and hit_url.rstrip('/') == url.lower().rstrip('/')):
                    snippet = hit.get('snippet', '')
                    break
            if snippet:
                break
        if snippet:
            snippets.append(snippet)
    
    return snippets


def generate_borrower_summary(data: Dict[str, Any], name: str, output_dir: Path, company_domain: str = None, enrichment_data: Dict[str, Any] = None) -> None:
    """Generate the main Borrower Summary markdown file.
    
    Args:
        data: Investigation data
        name: Borrower name
        output_dir: Output directory for markdown file
        company_domain: Optional company domain from company_domain_lookup
        enrichment_data: Optional pre-fetched enrichment data with 'domains' and 'addresses' keys
    """
    
    # Extract key information
    email = data['seed']['email']
    location = data.get('scored', {}).get('location', {}).get('city', 'Unknown')
    location_confidence = data.get('scored', {}).get('location', {}).get('confidence', 'unknown')
    num_social = data['contactability']['num_social']
    num_breaches = data['contactability']['num_breaches']
    contactability = data['contactability']['score']

    contactability_obj = data.get('contactability', {})
    footprint_bucket = contactability_obj.get('footprint_bucket', 'unknown')
    breach_bucket = contactability_obj.get('breach_bucket', 'unknown')
    
    # Load disposable email blocklist and check if email is disposable
    blocklist_path = Path(__file__).parent / "disposable_email_blocklist.conf"
    disposable_blocklist = load_disposable_email_blocklist(blocklist_path)
    is_disposable = is_disposable_email_domain(email, disposable_blocklist)
    
    # Check LinkedIn profile for connection count (check all LinkedIn profiles)
    top_handles = data.get('scored', {}).get('top_handles', [])
    queries = data.get('queries', [])
    linkedin_snippets = get_all_linkedin_snippets(top_handles, queries)
    linkedin_connections = None
    linkedin_alert_level = None
    
    # Check all LinkedIn profiles - alert if ANY meet the threshold
    for snippet in linkedin_snippets:
        connections = extract_linkedin_connections(snippet)
        if connections is not None:
            # Use the lowest connection count (most conservative) for display
            if linkedin_connections is None or connections < linkedin_connections:
                linkedin_connections = connections
            
            # Set alert level based on most severe threshold met
            if 1 <= connections <= 10:
                linkedin_alert_level = "danger"  # Most severe
            elif 10 < connections <= 100 and linkedin_alert_level != "danger":
                linkedin_alert_level = "warning"
    
    # Extract domain and perform whois/MX lookups for warning callouts
    # Check both email domain and company domain (company domain takes priority if provided)
    domain = extract_email_domain(email)
    print(f"[Borrower Summary] Extracted email domain: {domain}")
    
    # Use company domain if provided, otherwise use email domain
    domain_to_check = None
    if company_domain:
        company_domain = company_domain.strip() if company_domain else None
        if company_domain:
            domain_to_check = company_domain
            print(f"[Borrower Summary] Using company domain: {domain_to_check}")
    elif domain and not is_personal_email_domain(domain):
        domain_to_check = domain
        print(f"[Borrower Summary] Using email domain (business): {domain_to_check}")
    
    whois_result = None
    mx_result = None
    
    # Use pre-fetched enrichment data if available
    if domain_to_check and enrichment_data and enrichment_data.get('domains'):
        domain_enrichment = enrichment_data['domains'].get(domain_to_check, {})
        print(f"[Borrower Summary] Domain enrichment data found for {domain_to_check}: {bool(domain_enrichment)}")
        if domain_enrichment:
            whois_result = domain_enrichment.get('whois')
            mx_result = domain_enrichment.get('mx')
            print(f"[Borrower Summary] Using pre-fetched enrichment data for domain: {domain_to_check}")
            print(f"[Borrower Summary] Whois result: {bool(whois_result)}, MX result: {bool(mx_result)}")
    
    # Fallback to inline lookups if enrichment data not available and domain is business domain
    is_business_domain = domain_to_check is not None
    print(f"[Borrower Summary] Is business domain: {is_business_domain}")
    if is_business_domain:
        if not whois_result:
            print(f"[Borrower Summary] Performing whois lookup for business domain: {domain_to_check}")
            whois_result = get_domain_registration_date(domain_to_check)
        if not mx_result:
            print(f"[Borrower Summary] Performing MX record lookup for business domain: {domain_to_check}")
            mx_result = check_domain_mx_records(domain_to_check)
    
    num_regulator_hits = len(data['regulator_phase2']['confirmed_regulator_hits'])
    num_corporate_direct = data['corporate_debug']['num_direct']
    num_corporate_family = data['corporate_debug']['num_family']
    num_corporate_total = num_corporate_direct + num_corporate_family
    num_litigation_hits = len(
        data.get('litigation_phase2', {}).get('confirmed_litigation_hits', [])
    )

    # Get dynamic call-out types
    regulator_callout = get_regulator_callout(num_regulator_hits)
    litigation_callout = get_litigation_callout(num_litigation_hits)
    corporate_callout = get_corporate_callout(num_corporate_total)
    
    # Get contextual labels
    regulator_label = get_regulator_label(num_regulator_hits)
    litigation_label = get_litigation_label(num_litigation_hits)
    corporate_label = get_corporate_label(num_corporate_direct, num_corporate_family)

    # Risk levels for tags
    regulator_risk = get_regulator_risk_level(num_regulator_hits)
    litigation_risk = get_litigation_risk_level(num_litigation_hits)
    corporate_risk = get_corporate_risk_level(num_corporate_total)

    # Determine domain age and MX record callout types for warnings
    domain_callout = None
    mx_callout = None
    domain_warning_message = None
    domain_warning_title = None
    domain_warning_body = None
    mx_warning_message = None
    mx_warning_title = None
    
    # Check domain age callout if whois data is available
    print(f"[Borrower Summary] Checking domain age - whois_result: {bool(whois_result)}")
    if whois_result and whois_result.get('success') and whois_result.get('registration_date'):
        reg_date = whois_result['registration_date']
        print(f"[Borrower Summary] Registration date: {reg_date}")
        domain_callout = get_domain_age_callout(reg_date)
        print(f"[Borrower Summary] Domain callout type: {domain_callout}")
        # Only include if it's a warning or danger (not info)
        if domain_callout in ["danger", "warning"]:
            # Calculate age for display
            try:
                reg_date_obj = datetime.strptime(reg_date, "%Y-%m-%d")
                today = datetime.now()
                age_days = (today - reg_date_obj).days
                if age_days < 30:
                    age_text = f"{age_days} days"
                elif age_days < 365:
                    age_text = f"{age_days // 30} months"
                else:
                    age_text = f"{age_days // 365} years"
            except Exception:
                age_text = "unknown"
            
            if domain_callout == "danger":
                domain_warning_title = "Recently Registered Domain – Business Tenure Mismatch Risk"
                domain_warning_body = f"This domain was registered less than 90 days ago (domain age: {age_text}), which may conflict with claims of long-term business operations.\n\nOperational impact: Reduces confidence in employer/business tenure and increases the likelihood the business is newly formed or misrepresented.\n\nSuggested action: Validate business tenure via corporate registry filings, operating history, and independent business presence checks."
            elif domain_callout == "warning":
                domain_warning_title = "Recently Registered Domain – Business Tenure Mismatch Risk"
                domain_warning_body = f"This domain was registered less than 1 year ago (domain age: {age_text}), which may conflict with claims of long-term business operations.\n\nOperational impact: Reduces confidence in employer/business tenure and increases the likelihood the business is newly formed or misrepresented.\n\nSuggested action: Validate business tenure via corporate registry filings, operating history, and independent business presence checks."
            else:
                domain_warning_title = None
                domain_warning_body = None
            domain_warning_message = domain_warning_title is not None  # Set flag for display check
            print(f"[Borrower Summary] Domain warning message set: {bool(domain_warning_message)}")
    
    # Check MX record callout if MX data is available
    print(f"[Borrower Summary] Checking MX records - domain: {domain_to_check}, is_business: {is_business_domain}, mx_result: {bool(mx_result)}")
    if is_business_domain:
        # Determine callout type and warning message
        if mx_result and mx_result.get("success"):
            risk_level = mx_result.get("risk_level", "UNKNOWN")
            print(f"[Borrower Summary] MX success, risk_level: {risk_level}")
            mx_callout = get_mx_callout(mx_result)
            print(f"[Borrower Summary] MX callout type: {mx_callout}")
            # Only include if it's a warning or danger (not info)
            if mx_callout in ["danger", "warning"]:
                if risk_level == "HIGH":
                    mx_warning_title = "Default Registrar Email Services – Business Email Not Deliverable"
                    mx_warning_message = "This domain uses default registrar email services (forwarding/parking only), meaning it cannot reliably receive email and the business email may be inactive or misrepresented.\n\nOperational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\nSuggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources."
                elif risk_level == "MEDIUM":
                    mx_warning_title = "Self-Hosted Email Infrastructure – Business Email Verification Needed"
                    mx_warning_message = "This domain uses self-hosted or less common email infrastructure, which may reduce confidence in the reliability and legitimacy of the business email.\n\nOperational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\nSuggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources."
                elif risk_level == "LOW/MEDIUM":
                    mx_warning_title = None
                    mx_warning_message = None
                print(f"[Borrower Summary] MX warning message set: {bool(mx_warning_message)}")
        else:
            # MX lookup failed or no result - determine risk level first, then callout type
            error_msg = mx_result.get("error", "Unknown error") if mx_result else "MX lookup not performed"
            status = mx_result.get("status", "Lookup Failed") if mx_result else "Lookup Failed"
            risk_level = mx_result.get("risk_level", "UNKNOWN") if mx_result else "UNKNOWN"
            print(f"[Borrower Summary] MX lookup failed/unsuccessful - status: {status}, risk_level: {risk_level}")
            
            # Determine callout type based on risk level
            mx_callout = get_mx_callout(mx_result) if mx_result else "warning"
            print(f"[Borrower Summary] MX callout type (failed case): {mx_callout}")
            # Only include if it's a warning or danger (not info)
            if mx_callout in ["danger", "warning"]:
                if risk_level == "CRITICAL" and status == "No Email Configured":
                    mx_warning_title = "No MX Records – Business Email Not Deliverable"
                    mx_warning_message = "This domain has no MX records configured, meaning it cannot receive email and the business email may be inactive or misrepresented.\n\nOperational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\nSuggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources."
                elif risk_level == "CRITICAL" and status == "Domain Not Found":
                    mx_warning_title = "Domain Not Found – Business Email Invalid"
                    mx_warning_message = "This domain does not exist (NXDOMAIN). The business email address is invalid or fraudulent.\n\nOperational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\nSuggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources."
                else:
                    mx_warning_title = "Email Infrastructure Verification Failed"
                    mx_warning_message = "Unable to verify email infrastructure. All legitimate business emails must be able to receive email. This inability to verify is a strong indicator that the business email address may be invalid, inactive, or fraudulent.\n\nOperational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\nSuggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources."
                print(f"[Borrower Summary] MX warning message set (failed case): {bool(mx_warning_message)}")

    # Create wiki-link compatible name (replace spaces with underscores)
    wiki_name = name.replace(' ', '_')

    # --- Front matter tags ---
    borrower_slug = slugify(name)
    email_slug = slugify(email)
    location_slug = slugify(location)
    contact_score_slug = slugify(str(contactability))
    footprint_slug = slugify(str(footprint_bucket))
    breach_bucket_slug = slugify(str(breach_bucket))

    tags = [
        f"borrower/{borrower_slug}",
        f"email/{email_slug}",
        f"location/{location_slug}",
        "note/summary",
        f"contactability/score/{contact_score_slug}",
        f"contactability/footprint/{footprint_slug}",
        f"contactability/breach/{breach_bucket_slug}",
        f"risk/regulator/{regulator_risk}",
        f"risk/litigation/{litigation_risk}",
        f"risk/corporate/{corporate_risk}",
    ]

    # Build YAML front matter
    tags = sorted(set(tags))
    tags_block = "\n".join(f"  - {t}" for t in tags)
    header = f"---\ntags:\n{tags_block}\n---\n\n"
    
    content = header + f"""> [!info] Identity

- Name: **{name}**
- Email: `{email}`
- Location (scored): **{location}** ({location_confidence} confidence)
- Social handles (confirmed): {num_social}
- Data Breaches: {num_breaches}
- Contact-ability: {contactability}
- Identity Confirmation:
  - {data.get('scored', {}).get('rationale', 'No rationale provided')}

- See: [[Identity___{wiki_name}]]

---"""

    # Add domain age warning if applicable
    print(f"[Borrower Summary] Adding domain warning: {bool(domain_warning_title)}, callout: {domain_callout}")
    if domain_warning_title and domain_warning_body:
        content += f"""

> [!{domain_callout}] {domain_warning_title}

{domain_warning_body}

- See: [[Identity___{wiki_name}]]

---"""

    # Add MX record warning if applicable
    print(f"[Borrower Summary] Adding MX warning: {bool(mx_warning_message)}, callout: {mx_callout}")
    if mx_warning_message and mx_warning_title:
        content += f"""

> [!{mx_callout}] {mx_warning_title}

{mx_warning_message}

- See: [[Identity___{wiki_name}]]

---"""


    # Add no breaches alert if applicable
    if num_breaches == 0:
        content += f"""

> [!warning] No Breach History Detected – Possible Identity Risk

This email address has no known public breach exposure, which is sometimes seen with newly created or application-specific emails used in first-party fraud or synthetic identities.

Operational impact: Reduces confidence in long-term email usage and post-funding reachability, particularly if claimed employment or business tenure is longer.

Suggested action: Validate against claimed tenure and corroborate with alternate contact and identity signals.

- See: [[Identity___{wiki_name}]]

---"""

    # Add disposable email alert if applicable
    if is_disposable:
        content += f"""

> [!danger] Disposable Email Detected – High Identity Risk

This email address uses a known disposable email domain, which is designed for temporary or anonymous use and is commonly associated with first-party fraud and synthetic identities.

Operational impact: Significantly reduces confidence in identity stability and post-funding contactability.

Suggested action: Require a non-disposable email and corroborate identity using alternate contact and verification signals.

- See: [[Identity___{wiki_name}]]

---"""

    # Add LinkedIn connections alert if applicable
    if linkedin_alert_level and linkedin_connections is not None:
        # Use same format for both danger and warning levels
        callout_type = "danger" if linkedin_alert_level == "danger" else "warning"
        content += f"""

> [!{callout_type}] Very Low LinkedIn Connectivity – Identity Credibility Risk

This LinkedIn profile shows {linkedin_connections} connections, which is unusually low for someone claiming established employment or business activity and is sometimes seen with newly created or minimally used profiles (including those tied to first-party fraud or synthetic identities).

Operational impact: Reduces confidence in the claimed professional history and employment stability.

Suggested action: Verify employment using independent sources and corroborate with non-social identity and contact signals.

- See: [[Identity___{wiki_name}]]

---"""

    content += f"""

> [!{regulator_callout}] Regulator & Conduct Signals

- {regulator_label}
- See: [[Regulator___{wiki_name}]]

---

> [!{corporate_callout}] Corporate Footprint & Related Parties

- {corporate_label}
- Number of corporations listing borrower: {num_corporate_direct}
- Number of additional corporation(s) listing relatives with the same surname at the same address: {num_corporate_family}
- See: [[Corporate___{wiki_name}]]

---

> [!{litigation_callout}] Adverse Media or complaints

- {litigation_label}
- See: [[Adverse_Media___{wiki_name}]]

---

## Navigation

- [[Identity___{wiki_name}]]
- [[Regulator___{wiki_name}]]
- [[Adverse_Media___{wiki_name}]]
- [[Corporate___{wiki_name}]]
"""
    
    output_path = output_dir / f"Borrower_Summary_-_{name.replace(' ', '_')}.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Generated: {output_path}")


# ------------------------------
# Shared phone & address helpers
# ------------------------------

PHONE_PATTERN = re.compile(
    r"""
    (?:(?<=\D)|^)          # start at non-digit or beginning
    (?:\+?1[\s\-.]*)?      # optional country code
    (?:\(?\d{3}\)?[\s\-.]*) # area code
    \d{3}[\s\-.]*          # prefix
    \d{4}                  # line number
    (?=\D|$)               # end at non-digit or end
    """,
    re.VERBOSE
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# ------------------------------
# LLM-based contact extraction
# ------------------------------

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "phones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number_raw": {"type": "string"},
                    "number_digits": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"]
                    },
                    "source_url": {"type": "string"},
                    "snippet": {"type": "string"}
                },
                "required": ["number_raw", "number_digits", "confidence", "source_url"]
            }
        },
        "emails": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"]
                    },
                    "source_url": {"type": "string"},
                    "snippet": {"type": "string"}
                },
                "required": ["email", "confidence", "source_url"]
            }
        },
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
                "required": ["address_raw", "confidence", "source_url"]
            }
        }
    },
    "required": ["phones", "emails", "addresses"]
}


def extract_contact_info_llm(
    queries: List[Dict[str, Any]], 
    seed: Dict[str, Any],
    exclude_email: Optional[str] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Extract phone numbers, emails, and addresses from query hits using Vertex AI Gemini.
    
    Args:
        queries: List of query dictionaries with 'hits' containing title/snippet/url
        seed: Seed information dict with 'full_name', 'email', optional 'last_known_city', 'company_name'
        exclude_email: Optional email to exclude from results (typically the seed email)
    
    Returns:
        {
            "phones": [...],
            "emails": [...],
            "addresses": [...]
        }
    """
    if not GCP_PROJECT:
        print("[LLM Extraction] GCP_PROJECT not set, returning empty results")
        return {"phones": [], "emails": [], "addresses": []}
    
    # Initialize Vertex AI
    try:
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
    except Exception as e:
        print(f"[LLM Extraction] Vertex AI init error: {e}")
        return {"phones": [], "emails": [], "addresses": []}
    
    # Count total hits for prompt
    total_hits = sum(len(q.get("hits", [])) for q in queries)
    
    if total_hits == 0:
        print("[LLM Extraction] No hits in queries, returning empty results")
        return {"phones": [], "emails": [], "addresses": []}
    
    # Build prompts
    system_prompt = (
        "You are a contact information extractor for skip tracing investigations. You extract phone numbers, email addresses, and physical addresses from web search results.\n\n"
        "Your task:\n"
        "1. Extract contact information that appears to belong to the target person (seed information provided)\n"
        "2. Filter out contact info that clearly belongs to other people or is unrelated\n"
        "3. Provide confidence scores (high/medium/low) based on how clearly the info relates to the target person\n"
        "4. Include the source URL and snippet for each extracted item\n\n"
        "Guidelines:\n"
        "- HIGH confidence: Contact info is clearly associated with the target person (name match, context strongly suggests it's them)\n"
        "- MEDIUM confidence: Contact info likely belongs to target person but with some ambiguity (similar name, partial context match)\n"
        "- LOW confidence: Contact info might be related but evidence is weak (same city, generic context)\n\n"
        "For addresses:\n"
        "- Extract complete civic addresses (street number, street name, city, state/province, postal code)\n"
        "- Prefer addresses that appear in property records, business registrations, or official documents\n"
        "- Skip partial addresses or addresses without postal codes unless they're clearly relevant\n\n"
        "For phone numbers:\n"
        "- Extract phone numbers in any format (digits only normalization will be done separately)\n"
        "- Include country code if present\n"
        "- Skip fax numbers, customer service numbers, or clearly unrelated phone listings\n\n"
        "For emails:\n"
        "- Extract email addresses that appear to belong to the target person\n"
        "- Skip generic contact emails (info@, contact@, support@) unless context strongly suggests they're personal\n"
        "- Skip the seed email if it appears in results (it will be excluded separately)\n\n"
        "Return ONLY contact information that has at least LOW confidence. Do not include items with no relevance to the target person."
    )
    
    user_prompt = f"""Extract contact information from the following search results for:

Target Person:
- Name: {seed.get('full_name', '')}
- Email: {seed.get('email', '')}
- City: {seed.get('last_known_city', 'N/A')}
- Company: {seed.get('company_name', 'N/A') if seed.get('company_name') else 'N/A'}

Search Results ({len(queries)} queries, {total_hits} total hits):
{json.dumps(queries, indent=2)}

Return valid JSON with phones, emails, and addresses arrays. Each item should have confidence, source_url, and snippet fields."""
    
    def _call_vertex_ai():
        try:
            model = GenerativeModel(model_name="gemini-2.5-flash")
            print(f"[LLM Extraction] Calling Gemini 2.5 Flash for {total_hits} hits...")
            
            full_prompt = f"{system_prompt}\n\n{user_prompt}"
            response = model.generate_content(
                full_prompt,
                generation_config=GenerationConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=EXTRACTION_SCHEMA,
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
            
            # Parse JSON - if it fails due to empty/invalid content, treat as retryable
            try:
                result = json.loads(content)
            except json.JSONDecodeError as e:
                # JSON decode errors often indicate empty or malformed responses
                # that should be retried (similar to EmptyLLMResponseError)
                error_msg = str(e).lower()
                if "expecting value" in error_msg or "empty" in error_msg or len(content.strip()) == 0:
                    raise EmptyLLMResponseError(f"JSON decode error (likely empty response): {e}")
                # For other JSON decode errors (malformed JSON), still retry as it might be transient
                raise EmptyLLMResponseError(f"JSON decode error (malformed response): {e}")
            
            # Validate structure
            if "phones" not in result:
                result["phones"] = []
            if "emails" not in result:
                result["emails"] = []
            if "addresses" not in result:
                result["addresses"] = []
            
            # Ensure lists
            if not isinstance(result.get("phones"), list):
                result["phones"] = []
            if not isinstance(result.get("emails"), list):
                result["emails"] = []
            if not isinstance(result.get("addresses"), list):
                result["addresses"] = []
            
            # Validate and normalize phone numbers
            normalized_phones = []
            seen_digits = set()
            for phone in result.get("phones", []):
                if not isinstance(phone, dict):
                    continue
                number_raw = phone.get("number_raw", "").strip()
                if not number_raw:
                    continue
                # Extract digits for deduplication
                number_digits = re.sub(r"\D", "", number_raw)
                if not number_digits or number_digits in seen_digits:
                    continue
                seen_digits.add(number_digits)
                
                # Validate confidence
                confidence = phone.get("confidence", "medium")
                if confidence not in ["high", "medium", "low"]:
                    confidence = "medium"
                
                normalized_phones.append({
                    "number_raw": number_raw,
                    "number_digits": number_digits,
                    "confidence": confidence,
                    "source_url": phone.get("source_url", ""),
                    "snippet": phone.get("snippet", "").strip()
                })
            
            # Filter excluded email and normalize emails
            normalized_emails = []
            seen_emails = set()
            exclude_lower = exclude_email.lower().strip() if exclude_email else None
            
            for email_obj in result.get("emails", []):
                if not isinstance(email_obj, dict):
                    continue
                email = email_obj.get("email", "").strip()
                if not email:
                    continue
                email_lower = email.lower()
                
                # Skip excluded email
                if exclude_lower and email_lower == exclude_lower:
                    continue
                
                if email_lower in seen_emails:
                    continue
                seen_emails.add(email_lower)
                
                # Validate confidence
                confidence = email_obj.get("confidence", "medium")
                if confidence not in ["high", "medium", "low"]:
                    confidence = "medium"
                
                normalized_emails.append({
                    "email": email,
                    "confidence": confidence,
                    "source_url": email_obj.get("source_url", ""),
                    "snippet": email_obj.get("snippet", "").strip()
                })
            
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
                
                # Validate: Check if address contains street information
                # Skip if it's just a city/state/province (no street number or street name pattern)
                # Look for street number pattern (digit at start) or street name indicators
                has_street_number = bool(re.search(r'^\d{1,6}\s+[A-Za-z]', addr_cleaned))
                # Check for common street name patterns (Avenue, Street, Road, etc. preceded by text)
                has_street_name = bool(re.search(r'\b([A-Za-z0-9.\-\s]+?(?:Avenue|Street|Road|Lane|Drive|Boulevard|Way|Court|Place|Crescent|Circle|Terrace|Parkway|Highway|Ave|St|Rd|Dr|Blvd|Ln|Way|Ct|Pl|Cres|Cir|Terr|Pkwy|Hwy))\b', addr_cleaned, re.IGNORECASE))
                
                if not has_street_number and not has_street_name:
                    # This appears to be a city-only address, skip it
                    print(f"[LLM Extraction] Filtered out city-only address: {addr_cleaned}")
                    continue
                
                addr_normalized = addr_cleaned.lower().strip()
                addr_normalized = re.sub(r',', ' ', addr_normalized)
                addr_normalized = re.sub(r'\s+', ' ', addr_normalized)
                
                if addr_normalized in seen_addresses:
                    continue
                seen_addresses.add(addr_normalized)
                
                # Validate confidence
                confidence = addr_obj.get("confidence", "medium")
                if confidence not in ["high", "medium", "low"]:
                    confidence = "medium"
                
                normalized_addresses.append({
                    "address_raw": addr_cleaned,
                    "confidence": confidence,
                    "source_url": addr_obj.get("source_url", ""),
                    "snippet": addr_obj.get("snippet", "").strip()
                })
            
            result["phones"] = normalized_phones
            result["emails"] = normalized_emails
            result["addresses"] = normalized_addresses
            
            print(f"[LLM Extraction] Extracted {len(normalized_phones)} phones, {len(normalized_emails)} emails, {len(normalized_addresses)} addresses")
            return result
            
        except Exception as e:
            print(f"[LLM Extraction] Error: {e}")
            raise
    
    try:
        return retry_with_backoff(
            _call_vertex_ai,
            RetryConfig(max_attempts=3, base_delay_seconds=2.0, max_delay_seconds=60.0),
            operation_name="LLM contact info extraction"
        )
    except Exception as e:
        print(f"[LLM Extraction] Error after retries: {e}")
        return {"phones": [], "emails": [], "addresses": []}


def extract_phone_numbers_from_queries(queries: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Scan query hits and extract unique phone-like patterns.
    """
    results: List[Dict[str, str]] = []
    seen_digits = set()

    for query in queries or []:
        for hit in query.get("hits", []):
            title = hit.get("title", "") or ""
            snippet = hit.get("snippet", "") or ""
            url = hit.get("url", "") or ""
            text = f"{title} {snippet}"

            for match in PHONE_PATTERN.findall(text):
                digits = re.sub(r"\D", "", match)

                if digits in seen_digits:
                    continue
                seen_digits.add(digits)

                results.append(
                    {
                        "number_raw": match.strip(),
                        "number_digits": digits,
                        "source_url": url,
                        "snippet": snippet.strip(),
                    }
                )

    return results

def extract_email_addresses_from_queries(queries: List[Dict[str, Any]], exclude_email: str = None) -> List[Dict[str, str]]:
    """
    Scan query hits and extract unique email addresses.
    Optionally exclude a specific email (e.g., the seed email).
    """
    results: List[Dict[str, str]] = []
    seen_emails = set()
    
    # Normalize exclusion email if provided
    exclude_lower = exclude_email.lower().strip() if exclude_email else None

    for query in queries or []:
        for hit in query.get("hits", []):
            title = hit.get("title", "") or ""
            snippet = hit.get("snippet", "") or ""
            url = hit.get("url", "") or ""
            text = f"{title} {snippet}"

            for match in EMAIL_RE.findall(text):
                email_lower = match.lower()
                
                # Skip if this is the excluded email
                if exclude_lower and email_lower == exclude_lower:
                    continue
                
                if email_lower in seen_emails:
                    continue
                seen_emails.add(email_lower)

                results.append(
                    {
                        "email": match,
                        "source_url": url,
                        "snippet": snippet.strip(),
                    }
                )

    return results

# ------------------------------
# Address patterns (US + Canada)
# ------------------------------

# Widely-used US civic address regex
# Improved US civic address regex with compass directions
US_REGEX = re.compile(
    r"""\b
    \d{1,6}\s+[A-Za-z0-9.\- ]+             # street number + street name
    (?:Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|Drive|Dr|
       Court|Ct|Lane|Ln|Way|Terrace|Ter|Place|Pl)?    # optional suffix
    (?:\s+(?:NW|NE|SW|SE|North|South|East|West|N|S|E|W))?\b  # optional compass direction
    [, ]+\s*
    [A-Za-z.\- ]{2,40}                     # city
    [, ]+\s*
    [A-Z]{2}                                # state
    \s+
    \d{5}(?:-\d{4})?                        # ZIP or ZIP+4
    \b""",
    re.IGNORECASE | re.VERBOSE,
)

# Widely-used Canada Post–compatible regex
CA_REGEX = re.compile(
    r"""\b
    \d{1,6}\s+[A-Za-z0-9.\- ]+               # street number + street
    [, ]+\s*
    [A-Za-z.\- ]{2,40}                       # city
    [, ]+\s*
    (AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)  # province
    \s+
    [A-Z]\d[A-Z]\s?\d[A-Z]\d                 # postal code
    (?:\s+Canada)?                           # optional "Canada"
    \b""",
    re.IGNORECASE | re.VERBOSE,
)

ADDRESS_PATTERNS = [US_REGEX, CA_REGEX]


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
                    # Fallback string - no structured components available
                    # Use clean_address_for_geocoding to match address_geocoding function
                    addr_cleaned = clean_address_for_geocoding(addr_raw)
                    
                    # Validate: Check if fallback string contains street information
                    # Skip if it's just a city/state/province (no street number or street name pattern)
                    # Look for street number pattern (digit at start) or street name indicators
                    has_street_number = bool(re.search(r'^\d{1,6}\s+[A-Za-z]', addr_cleaned))
                    # Check for common street name patterns (Avenue, Street, Road, etc. preceded by text)
                    has_street_name = bool(re.search(r'\b([A-Za-z0-9.\-\s]+?(?:Avenue|Street|Road|Lane|Drive|Boulevard|Way|Court|Place|Crescent|Circle|Terrace|Parkway|Highway|Ave|St|Rd|Dr|Blvd|Ln|Way|Ct|Pl|Cres|Cir|Terr|Pkwy|Hwy))\b', addr_cleaned, re.IGNORECASE))
                    
                    if not has_street_number and not has_street_name:
                        # This appears to be a city-only address, skip it
                        print(f"[Address Extraction] Filtered out city-only address (fallback): {addr_cleaned}")
                        continue
                    
                    addr_data = {
                        "address_raw": addr_cleaned,
                        "source_url": source,
                        "snippet": snippet,
                        "street_number": None,
                        "street_name": None,
                        "city": None,
                        "province": None,
                        "state": None,
                        "postal_code": None,
                        "zip_code": None,
                    }
                else:
                    # pyap address object - extract structured components
                    addr_raw = str(addr_obj)
                    # Use clean_address_for_geocoding to match address_geocoding function
                    addr_cleaned = clean_address_for_geocoding(addr_raw)
                    
                    # Extract components from pyap object
                    street_number = getattr(addr_obj, 'street_number', None)
                    street_name = getattr(addr_obj, 'street_name', None)
                    city = getattr(addr_obj, 'city', None)
                    
                    # Canadian addresses have province and postal_code
                    province = getattr(addr_obj, 'province', None)
                    postal_code = getattr(addr_obj, 'postal_code', None)
                    
                    # US addresses have state and zip_code
                    state = getattr(addr_obj, 'state', None)
                    zip_code = getattr(addr_obj, 'zip_code', None)
                    
                    # Validate: Skip addresses that only have city/state/province but no street component
                    if not street_number and not street_name:
                        # This is a city-only address, skip it
                        print(f"[Address Extraction] Filtered out city-only address: {addr_cleaned}")
                        continue
                    
                    addr_data = {
                        "address_raw": addr_cleaned,
                        "source_url": source,
                        "snippet": snippet,
                        "street_number": street_number,
                        "street_name": street_name,
                        "city": city,
                        "province": province,
                        "state": state,
                        "postal_code": postal_code,
                        "zip_code": zip_code,
                    }
                
                addr_normalized = normalize_address(addr_data["address_raw"])
                
                if addr_normalized in seen:
                    continue
                seen.add(addr_normalized)
                
                results.append(addr_data)

    return results


# ------------------------------
# Identity report
# ------------------------------

def generate_identity_report(data: Dict[str, Any], name: str, output_dir: Path, company_domain: str = None, enrichment_data: Dict[str, Any] = None) -> None:
    """Generate the Identity markdown file.
    
    Args:
        data: Investigation data
        name: Borrower name
        output_dir: Output directory for markdown file
        company_domain: Optional company domain from company_domain_lookup
        enrichment_data: Optional pre-fetched enrichment data with 'domains' and 'addresses' keys
    """
    
    report_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    # Identity report generation
    scored = data['scored']
    seed = data['seed']
    
    # Build snapshot section
    email = seed['email']
    location = scored.get('location', {}).get('city', 'Unknown')
    location_confidence = scored.get('location', {}).get('confidence', 'unknown')
    
    # Extract domain and perform whois lookup if not personal email
    domain = extract_email_domain(email)
    whois_result = None
    mx_result = None
    
    # Use pre-fetched enrichment data if available
    if enrichment_data and enrichment_data.get('domains'):
        domain_enrichment = enrichment_data['domains'].get(domain, {})
        if domain_enrichment:
            whois_result = domain_enrichment.get('whois')
            mx_result = domain_enrichment.get('mx')
            print(f"[Identity Report] Using pre-fetched enrichment data for domain: {domain}")
    
    # Fallback to inline lookups if enrichment data not available
    if domain and not is_personal_email_domain(domain):
        if not whois_result:
            print(f"[Identity Report] Performing whois lookup for business domain: {domain}")
            whois_result = get_domain_registration_date(domain)
        if not mx_result:
            print(f"[Identity Report] Performing MX record lookup for business domain: {domain}")
            mx_result = check_domain_mx_records(domain)
    
    # Perform company domain checks if provided
    print(f"[Identity Report] DEBUG: generate_identity_report called with company_domain={repr(company_domain)} (type: {type(company_domain)})")
    company_whois_result = None
    company_mx_result = None
    if company_domain:
        print(f"[Identity Report] DEBUG: company_domain is truthy, stripping...")
        company_domain = company_domain.strip()
        print(f"[Identity Report] DEBUG: After strip: {repr(company_domain)}")
        if company_domain:
            # Use pre-fetched enrichment data if available
            if enrichment_data and enrichment_data.get('domains') and company_domain in enrichment_data['domains']:
                company_enrichment = enrichment_data['domains'][company_domain]
                company_whois_result = company_enrichment.get('whois')
                company_mx_result = company_enrichment.get('mx')
                print(f"[Identity Report] Using pre-fetched enrichment data for company domain: {company_domain}")
            else:
                print(f"[Identity Report] Performing whois lookup for company domain: {company_domain}")
                company_whois_result = get_domain_registration_date(company_domain)
                print(f"[Identity Report] DEBUG: whois_result: success={company_whois_result.get('success') if company_whois_result else None}, registration_date={company_whois_result.get('registration_date') if company_whois_result else None}")
                print(f"[Identity Report] Performing MX record lookup for company domain: {company_domain}")
                company_mx_result = check_domain_mx_records(company_domain)
                print(f"[Identity Report] DEBUG: mx_result: success={company_mx_result.get('success') if company_mx_result else None}, status={company_mx_result.get('status') if company_mx_result else None}, error={company_mx_result.get('error') if company_mx_result else None}")
        else:
            print(f"[Identity Report] DEBUG: company_domain is empty after strip")
    else:
        print(f"[Identity Report] DEBUG: company_domain is falsy, skipping lookups")

    # Check Gravatar profile if personal email
    gravatar_result = None
    if domain and is_personal_email_domain(domain):
        print(f"[Identity Report] Checking Gravatar profile for personal email: {email}")
        gravatar_result = get_gravatar_profile(email)

    # Load disposable email blocklist and check if email is disposable
    blocklist_path = Path(__file__).parent / "disposable_email_blocklist.conf"
    disposable_blocklist = load_disposable_email_blocklist(blocklist_path)
    is_disposable = is_disposable_email_domain(email, disposable_blocklist)

    contactability = data.get('contactability', {})
    score = contactability.get('score', 'unknown')
    reason = contactability.get('reason', 'No information available')
    num_social = contactability.get('num_social', 0)
    num_breaches = contactability.get('num_breaches', 0)
    footprint_bucket = contactability.get('footprint_bucket', 'unknown')
    breach_bucket = contactability.get('breach_bucket', 'unknown')

    breaches = data.get('breaches', [])
    top_handles = scored.get('top_handles', [])
    queries = data.get('queries', [])
    
    # Calculate earliest breach date
    earliest_breach_date = None
    if breaches:
        valid_dates = []
        for breach in breaches:
            date_str = breach.get('date')
            if date_str:
                try:
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                    valid_dates.append(date_obj)
                except (ValueError, TypeError):
                    pass
        if valid_dates:
            earliest_breach_date = min(valid_dates).strftime("%Y-%m-%d")

    # --- Front matter tags ---
    borrower_slug = slugify(name)
    email_slug = slugify(email)
    location_slug = slugify(location)
    contact_score_slug = slugify(str(score))
    footprint_slug = slugify(str(footprint_bucket))
    breach_bucket_slug = slugify(str(breach_bucket))

    tags = [
        f"borrower/{borrower_slug}",
        "note/identity",
        f"email/{email_slug}",
        f"location/{location_slug}",
        f"contactability/score/{contact_score_slug}",
        f"contactability/footprint/{footprint_slug}",
        f"contactability/breach/{breach_bucket_slug}",
    ]

    # Breach tags
    for breach in breaches or []:
        breach_name = breach.get('name')
        if breach_name:
            breach_slug = slugify(breach_name)
            if breach_slug != "unknown":
                tags.append(f"breach/{breach_slug}")

    # Social platform + handle tags
    platforms = set()
    for handle in top_handles or []:
        platform = handle.get('platform')
        handle_name = handle.get('handle')
        if platform:
            platform_slug = slugify(platform)
            if platform_slug != "unknown":
                platforms.add(platform_slug)
                tags.append(f"social/platform/{platform_slug}")
                if handle_name:
                    handle_slug = slugify(handle_name)
                    if handle_slug != "unknown":
                        tags.append(f"social/handle/{platform_slug}/{handle_slug}")

    tags = sorted(set(tags))
    tags_block = "\n".join(f"  - {t}" for t in tags)
    header = f"---\ntags:\n{tags_block}\n---\n\n"
    
    # Get primary and secondary handles
    primary_handle = top_handles[0] if len(top_handles) > 0 else None
    secondary_handle = top_handles[1] if len(top_handles) > 1 else None
    
    content = header
    content += get_navigation_bar(data, name, 'identity')
    content += f"> [!info] Snapshot\n"
    content += f"> - **Name:** {name}  \n"
    content += f"> - **Email:** `{email}`  \n"
    content += f"> - **Location (scored):** {location} ({location_confidence} confidence)  \n"
    
    if primary_handle:
        content += f"> - **Primary handle:** {primary_handle['platform']} — `{primary_handle['handle']}` ({primary_handle.get('confidence', 'medium')} confidence)  \n"
    
    if secondary_handle:
        content += f"> - **Secondary handle:** {secondary_handle['platform']} — `{secondary_handle['handle']}` ({secondary_handle.get('confidence', 'medium')} confidence)\n"
    
    content += "\n\n"
    content += "\n---\n\n"
    content += "## Identity Confirmation\n\n"
    content += "> [!note] Rationale\n"
    content += f"> {scored.get('rationale', 'No rationale provided')}\n\n"
    
    # Grounding metadata section (below Rationale)
    grounding_metadata = data.get('grounding_metadata', {})
    grounding_sources = grounding_metadata.get('grounding_sources', [])
    search_queries = grounding_metadata.get('search_queries', [])
    
    if search_queries:
        content += "### Grounding Searches\n\n"
        content += "The following searches were performed by the AI to verify identity:\n\n"
        for query in search_queries:
            encoded_query = quote_plus(query)
            google_url = f"https://www.google.com/search?q={encoded_query}"
            content += f"- [{query}]({google_url})\n"
        content += "\n"
    
    content += "---\n\n"
    
    # Social handles section
    content += "## Social handles\n\n"
    for handle in top_handles or []:
        platform = handle['platform']
        handle_name = handle['handle']
        handle_url = handle['url']
        confidence = handle.get('confidence', 'medium')
        
        
        
        content += f"- **{platform}**  \n"
        content += f"  - Handle: `{handle_name}`  \n"
        content += f"  - Confidence: **{confidence}**  \n"
        content += f"  - URL: <{handle_url}>  \n"
        whatsmyname_url = f"https://whatsmyname.app/?q={quote_plus(handle_name)}"
        content += f"  - [🔍 Search handle on 500 sites]({whatsmyname_url})  \n"

        # Try to find a snippet for this handle from the queries
        snippet = None
        for query in data.get('queries', []):
            for hit in query.get('hits', []):
                hit_url = hit.get('url', '').lower()
                if handle_url.lower() == hit_url or (hit_url and hit_url.rstrip('/') == handle_url.lower().rstrip('/')):
                    snippet = hit.get('snippet', '')
                    break
            if snippet:
                break

        if snippet:
            content += f"  - Snippet:  \n"
            content += f"    > {snippet}\n"

        content += "\n"

    content += "---\n\n"

    # Data Breaches section
    content += "## Data Breaches\n\n"
    if breaches and len(breaches) > 0:
        # Sort breaches chronologically by date (oldest first)
        # Breaches without dates go to the end
        def sort_key(breach):
            date_str = breach.get('date')
            if date_str:
                try:
                    # Parse YYYY-MM-DD format
                    return datetime.strptime(date_str, "%Y-%m-%d")
                except (ValueError, TypeError):
                    # Invalid date format, put at end
                    return datetime.max
            else:
                # No date, put at end
                return datetime.max
        
        sorted_breaches = sorted(breaches, key=sort_key)
        
        # Display as a table for better alignment
        content += "| Breach Name | Date |\n"
        content += "|-------------|------|\n"
        
        for breach in sorted_breaches:
            breach_name = breach.get('name', 'Unknown')
            breach_date = breach.get('date', '')
            if breach_date:
                content += f"| {breach_name} | {breach_date} |\n"
            else:
                content += f"| {breach_name} | *(Unknown)* |\n"
    else:
        content += "*(None)*\n"
    
    content += f"Source: [Have I Been Pwned](https://haveibeenpwned.com) ·Timestamp: {report_timestamp}\n\n"
    
    # Add alert if no breaches detected (potential risk indicator)
    if num_breaches == 0 or (not breaches or len(breaches) == 0):
        content += "\n---\n\n"
        content += "> [!warning] No Breach History Detected – Possible Identity Risk\n\n"
        content += "This email address has no known public breach exposure, which is sometimes seen with newly created or application-specific emails used in first-party fraud or synthetic identities.\n\n"
        content += "Operational impact: Reduces confidence in long-term email usage and post-funding reachability, particularly if claimed employment or business tenure is longer.\n\n"
        content += "Suggested action: Validate against claimed tenure and corroborate with alternate contact and identity signals.\n\n"
    
    # Add disposable email alert if applicable
    if is_disposable:
        content += "\n---\n\n"
        content += "> [!danger] Disposable Email Detected – High Identity Risk\n\n"
        content += "This email address uses a known disposable email domain, which is designed for temporary or anonymous use and is commonly associated with first-party fraud and synthetic identities.\n\n"
        content += "Operational impact: Significantly reduces confidence in identity stability and post-funding contactability.\n\n"
        content += "Suggested action: Require a non-disposable email and corroborate identity using alternate contact and verification signals.\n\n"
    
    # Check LinkedIn profile for connection count (check all LinkedIn profiles)
    linkedin_snippets = get_all_linkedin_snippets(top_handles, queries)
    linkedin_connections = None
    linkedin_alert_level = None
    
    # Check all LinkedIn profiles - alert if ANY meet the threshold
    for snippet in linkedin_snippets:
        connections = extract_linkedin_connections(snippet)
        if connections is not None:
            # Use the lowest connection count (most conservative) for display
            if linkedin_connections is None or connections < linkedin_connections:
                linkedin_connections = connections
            
            # Set alert level based on most severe threshold met
            if 1 <= connections <= 10:
                linkedin_alert_level = "danger"  # Most severe
            elif 10 < connections <= 100 and linkedin_alert_level != "danger":
                linkedin_alert_level = "warning"
    
    # Add LinkedIn connections alert if applicable
    if linkedin_alert_level and linkedin_connections is not None:
        # Use same format for both danger and warning levels
        callout_type = "danger" if linkedin_alert_level == "danger" else "warning"
        content += "\n---\n\n"
        content += f"> [!{callout_type}] Very Low LinkedIn Connectivity – Identity Credibility Risk\n\n"
        content += f"This LinkedIn profile shows {linkedin_connections} connections, which is unusually low for someone claiming established employment or business activity and is sometimes seen with newly created or minimally used profiles (including those tied to first-party fraud or synthetic identities).\n\n"
        content += "Operational impact: Reduces confidence in the claimed professional history and employment stability.\n\n"
        content += "Suggested action: Verify employment using independent sources and corroborate with non-social identity and contact signals.\n\n"
    
    content += "\n---\n\n"
    
    # Contact-ability section
    content += "## Contact-ability\n\n"
    content += "> [!note]\n"
    content += f"> **Score:** {score}  \n"
    content += f"> **Reason:** {reason}  \n"
    content += ">  \n"
    content += f"> - **Social accounts detected:** {num_social}  \n"
    content += f"> - **Known breaches:** {num_breaches}  \n"
    if earliest_breach_date:
        content += f"> - **Earliest breach:** {earliest_breach_date}  \n"
    content += f"> - **Footprint bucket:** `{footprint_bucket}`  \n"
    content += f"> - **Breach bucket:** `{breach_bucket}`  \n"
    content += f"> - **Disposable email domain:** {str(is_disposable).lower()}\n"
    
    # Check if Gravatar appears in breach list and Gravatar API returned no results
    has_gravatar_breach = False
    gravatar_breach_date = None
    if breaches:
        for breach in breaches:
            breach_name = breach.get('name', '').lower()
            if 'gravatar' in breach_name:
                has_gravatar_breach = True
                gravatar_breach_date = breach.get('date', '')
                break
    
    # Add Digital footprint hygiene field if conditions are met
    if has_gravatar_breach and gravatar_result and not gravatar_result.get('success'):
        if gravatar_breach_date:
            content += f"> - **Digital footprint hygiene:** High (User deleted Gravatar profile after breach of {gravatar_breach_date})  \n"
        else:
            content += f"> - **Digital footprint hygiene:** High (User deleted Gravatar profile after breach)  \n"
    
    content += "\n\n"
    
    # Gravatar profile section (if available)
    if gravatar_result and gravatar_result.get('success'):
        content += "### Gravatar Profile\n\n"
        content += f"![Gravatar Avatar]({gravatar_result['thumbnail_url']})  \n"
        content += f"[View Full Profile →]({gravatar_result['profile_url']})  \n"
        content += "\n"
    
    content += "---\n\n"

    # ------------------------------
    # Domain Registration
    # ------------------------------
    if whois_result and whois_result.get('success') and whois_result.get('registration_date'):
        reg_date = whois_result['registration_date']
        domain_callout = get_domain_age_callout(reg_date)
        
        # Calculate age for display (datetime is imported at module level)
        try:
            reg_date_obj = datetime.strptime(reg_date, "%Y-%m-%d")
            today = datetime.now()
            age_days = (today - reg_date_obj).days
            if age_days < 30:
                age_text = f"{age_days} days"
            elif age_days < 365:
                age_text = f"{age_days // 30} months"
            else:
                age_text = f"{age_days // 365} years"
        except Exception:
            age_text = "unknown"
        
        content += "## Domain Registration\n\n"
        content += f"> [!{domain_callout}] Domain Registration\n"
        content += f"> **Domain:** {domain}  \n"
        content += f"> **Registration Date:** {reg_date}  \n"
        content += f"> **Domain Age:** {age_text}  \n"
        content += ">  \n"
        
        # Add contextual message based on age
        if domain_callout == "danger":
            content += "> ⚠️ **High Risk:** This domain was registered less than 90 days ago. This is a strong indicator that the business email may be newly created, which contradicts claims of long-term employment.\n\n"
        elif domain_callout == "warning":
            content += "> ⚠️ **Caution:** This domain was registered less than 1 year ago. Verify employment duration claims against this registration date.\n\n"
        else:
            content += "> *This domain registration date may indicate whether the business email is newly created, which could be relevant for employment verification.*\n\n"
        
        content += "---\n\n"

    # ------------------------------
    # MX Record Analysis
    # ------------------------------
    if domain and not is_personal_email_domain(domain):
        mx_callout = get_mx_callout(mx_result) if mx_result else "warning"
        
        content += "## Email Infrastructure (MX Records)\n\n"
        content += f"> [!{mx_callout}] Email Infrastructure\n"
        content += f"> **Domain:** {domain}  \n"
        
        if mx_result and mx_result.get("success"):
            primary_mx = mx_result.get("mx_records", [""])[0] if mx_result.get("mx_records") else "N/A"
            provider = mx_result.get("provider_detected", "Unknown")
            risk_level = mx_result.get("risk_level", "UNKNOWN")
            status = mx_result.get("status", "Unknown")
            
            content += f"> **Primary MX Record:** `{primary_mx}`  \n"
            content += f"> **Provider:** {provider}  \n"
            content += f"> **Risk Level:** {risk_level}  \n"
            content += f"> **Status:** {status}  \n"
            content += ">  \n"
            
            # Add contextual message based on risk level
            if risk_level == "HIGH":
                content += f"> [!{mx_callout}] Default Registrar Email Services – Business Email Not Deliverable\n\n"
                content += "This domain uses default registrar email services (forwarding/parking only), meaning it cannot reliably receive email and the business email may be inactive or misrepresented.\n\n"
                content += "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                content += "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.\n\n"
            elif risk_level == "MEDIUM":
                content += f"> [!{mx_callout}] Self-Hosted Email Infrastructure – Business Email Verification Needed\n\n"
                content += "This domain uses self-hosted or less common email infrastructure, which may reduce confidence in the reliability and legitimacy of the business email.\n\n"
                content += "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                content += "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.\n\n"
            elif risk_level == "LOW/MEDIUM":
                content += "> ⚠️ **Caution:** This domain uses standard business email services. Verify employment claims against this infrastructure.\n\n"
            else:  # LOW
                content += "> ✓ **Legitimate:** This domain uses established business email infrastructure, indicating a legitimate business operation.\n\n"
        else:
            # Show error/warning card if lookup failed
            error_msg = mx_result.get("error", "Unknown error") if mx_result else "MX lookup not performed"
            status = mx_result.get("status", "Lookup Failed") if mx_result else "Lookup Failed"
            risk_level = mx_result.get("risk_level", "UNKNOWN") if mx_result else "UNKNOWN"
            
            content += f"> **Status:** {status}  \n"
            content += f"> **Risk Level:** {risk_level}  \n"
            if error_msg:
                content += f"> **Error:** {error_msg}  \n"
            content += ">  \n"
            
            # Differentiate messages based on specific failure types
            if risk_level == "CRITICAL" and status == "No Email Configured":
                content += f"> [!{mx_callout}] No MX Records – Business Email Not Deliverable\n\n"
                content += "This domain has no MX records configured, meaning it cannot receive email and the business email may be inactive or misrepresented.\n\n"
                content += "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                content += "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.\n\n"
            elif risk_level == "CRITICAL" and status == "Domain Not Found":
                content += f"> [!{mx_callout}] Domain Not Found – Business Email Invalid\n\n"
                content += "This domain does not exist (NXDOMAIN). The business email address is invalid or fraudulent.\n\n"
                content += "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                content += "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.\n\n"
            else:
                # Generic failure case
                content += f"> [!{mx_callout}] Email Infrastructure Verification Failed\n\n"
                content += "Unable to verify email infrastructure. All legitimate business emails must be able to receive email. This inability to verify is a strong indicator that the business email address may be invalid, inactive, or fraudulent.\n\n"
                content += "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                content += "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.\n\n"
        
        content += "---\n\n"

    # ------------------------------
    # Company Domain Registration
    # ------------------------------
    print(f"[Identity Report] DEBUG: Evaluating Company Domain Registration section")
    print(f"[Identity Report] DEBUG: company_whois_result={company_whois_result}")
    print(f"[Identity Report] DEBUG: company_whois_result.get('success')={company_whois_result.get('success') if company_whois_result else None}")
    print(f"[Identity Report] DEBUG: company_whois_result.get('registration_date')={company_whois_result.get('registration_date') if company_whois_result else None}")
    if company_whois_result and company_whois_result.get('success') and company_whois_result.get('registration_date'):
        print(f"[Identity Report] DEBUG: Company Domain Registration section WILL BE SHOWN")
        reg_date = company_whois_result['registration_date']
        domain_callout = get_domain_age_callout(reg_date)
        
        # Calculate age for display (datetime is imported at module level)
        try:
            reg_date_obj = datetime.strptime(reg_date, "%Y-%m-%d")
            today = datetime.now()
            age_days = (today - reg_date_obj).days
            if age_days < 30:
                age_text = f"{age_days} days"
            elif age_days < 365:
                age_text = f"{age_days // 30} months"
            else:
                age_text = f"{age_days // 365} years"
        except Exception:
            age_text = "unknown"
        
        content += "## Company Domain Registration\n\n"
        content += f"> [!{domain_callout}] Company Domain Registration\n"
        content += f"> **Domain:** {company_domain}  \n"
        content += f"> **Registration Date:** {reg_date}  \n"
        content += f"> **Domain Age:** {age_text}  \n"
        content += ">  \n"
        
        # Add contextual message based on age
        if domain_callout == "danger":
            content += f"> [!{domain_callout}] Recently Registered Company Domain – Business Tenure Mismatch Risk\n\n"
            content += f"This company domain was registered less than 90 days ago (domain age: {age_text}), which may conflict with claims of long-term business operations.\n\n"
            content += "Operational impact: Reduces confidence in employer/business tenure and increases the likelihood the business is newly formed or misrepresented.\n\n"
            content += "Suggested action: Validate business tenure via corporate registry filings, operating history, and independent business presence checks.\n\n"
        elif domain_callout == "warning":
            content += f"> [!{domain_callout}] Recently Registered Company Domain – Business Tenure Mismatch Risk\n\n"
            content += f"This company domain was registered less than 1 year ago (domain age: {age_text}), which may conflict with claims of long-term business operations.\n\n"
            content += "Operational impact: Reduces confidence in employer/business tenure and increases the likelihood the business is newly formed or misrepresented.\n\n"
            content += "Suggested action: Validate business tenure via corporate registry filings, operating history, and independent business presence checks.\n\n"
        else:
            content += "> *This company domain registration date may indicate whether the business is newly created, which could be relevant for business verification.*\n\n"
        
        content += "---\n\n"

    # ------------------------------
    # Company Email Infrastructure (MX Records)
    # ------------------------------
    if company_domain:
        mx_callout = get_mx_callout(company_mx_result) if company_mx_result else "warning"
        
        content += "## Company Email Infrastructure (MX Records)\n\n"
        content += f"> [!{mx_callout}] Company Email Infrastructure\n"
        content += f"> **Domain:** {company_domain}  \n"
        
        if company_mx_result and company_mx_result.get("success"):
            primary_mx = company_mx_result.get("mx_records", [""])[0] if company_mx_result.get("mx_records") else "N/A"
            provider = company_mx_result.get("provider_detected", "Unknown")
            risk_level = company_mx_result.get("risk_level", "UNKNOWN")
            status = company_mx_result.get("status", "Unknown")
            
            content += f"> **Primary MX Record:** `{primary_mx}`  \n"
            content += f"> **Provider:** {provider}  \n"
            content += f"> **Risk Level:** {risk_level}  \n"
            content += f"> **Status:** {status}  \n"
            content += ">  \n"
            
            # Add contextual message based on risk level
            if risk_level == "HIGH":
                content += f"> [!{mx_callout}] Default Registrar Email Services – Business Email Not Deliverable\n\n"
                content += "This company domain uses default registrar email services (forwarding/parking only), meaning it cannot reliably receive email and the business email may be inactive or misrepresented.\n\n"
                content += "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                content += "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.\n\n"
            elif risk_level == "MEDIUM":
                content += f"> [!{mx_callout}] Self-Hosted Email Infrastructure – Business Email Verification Needed\n\n"
                content += "This company domain uses self-hosted or less common email infrastructure, which may reduce confidence in the reliability and legitimacy of the business email.\n\n"
                content += "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                content += "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.\n\n"
            elif risk_level == "LOW/MEDIUM":
                content += "> ⚠️ **Caution:** This company domain uses standard business email services. Verify employment claims against this infrastructure.\n\n"
            else:  # LOW
                content += "> ✓ **Legitimate:** This company domain uses established business email infrastructure, indicating a legitimate business operation.\n\n"
        else:
            # Show error/warning card if lookup failed
            error_msg = company_mx_result.get("error", "Unknown error") if company_mx_result else "MX lookup not performed"
            status = company_mx_result.get("status", "Lookup Failed") if company_mx_result else "Lookup Failed"
            risk_level = company_mx_result.get("risk_level", "UNKNOWN") if company_mx_result else "UNKNOWN"
            
            content += f"> **Status:** {status}  \n"
            content += f"> **Risk Level:** {risk_level}  \n"
            if error_msg:
                content += f"> **Error:** {error_msg}  \n"
            content += ">  \n"
            
            # Differentiate messages based on specific failure types
            if risk_level == "CRITICAL" and status == "No Email Configured":
                content += f"> [!{mx_callout}] No MX Records – Business Email Not Deliverable\n\n"
                content += "This company domain has no MX records configured, meaning it cannot receive email and the business email may be inactive or misrepresented.\n\n"
                content += "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                content += "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.\n\n"
            elif risk_level == "CRITICAL" and status == "Domain Not Found":
                content += f"> [!{mx_callout}] Domain Not Found – Business Email Invalid\n\n"
                content += "This company domain does not exist (NXDOMAIN). The business email address is invalid or fraudulent.\n\n"
                content += "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                content += "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.\n\n"
            else:
                # Generic failure case
                content += f"> [!{mx_callout}] Email Infrastructure Verification Failed\n\n"
                content += "Unable to verify company email infrastructure. All legitimate business emails must be able to receive email. This inability to verify is a strong indicator that the business email address may be invalid, inactive, or fraudulent.\n\n"
                content += "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                content += "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.\n\n"
        
        content += "---\n\n"
    else:
        print(f"[Identity Report] DEBUG: Company Email Infrastructure section WILL NOT BE SHOWN (company_domain is falsy)")

    # ------------------------------
    # Possible Phone Number(s)
    # ------------------------------
    # Get contact info from enrichment data (extracted during Phase 2)
    contact_info = {}
    if enrichment_data and enrichment_data.get('contacts'):
        contact_info = enrichment_data.get('contacts', {})
    else:
        # Fallback to empty structure if enrichment data not available
        contact_info = {"phones": [], "emails": [], "addresses": []}

    phones = contact_info.get('phones', [])

    if phones:
        content += "## Phone Number(s) of interest\n\n"
        content += "> [!info]\n"
        content += "> Automatically detected phone numbers from open-web sources.\n\n"

        for i, p in enumerate(phones, start=1):
            content += f"{i}. **{p['number_raw']}**  \n"
            google_url = generate_google_search_url_for_phone(p)
            content += f"   - [🔍 Search Google]({google_url})  \n"
            if p.get("source_url"):
                content += f"   - **Source:** {p['source_url']}  \n"
            if p.get("snippet"):
                content += "   - **Snippet:**  \n"
                content += f"     > {p['snippet']}\n"
            content += "\n"

        content += "---\n\n"
    # ------------------------------
    # Possible Email Address(es)
    # ------------------------------
    emails = contact_info.get('emails', [])

    if emails:
        content += "## Email Address(es) of interest\n\n"
        content += "> [!info]\n"
        content += "> Automatically detected email addresses from open-web sources.\n\n"

        for i, e in enumerate(emails, start=1):
            google_url = generate_google_search_url_for_email(e['email'])
            content += f"{i}. **[{e['email']}]({google_url})**  \n"
            if e.get("source_url"):
                content += f"   - **Source:** {e['source_url']}  \n"
            if e.get("snippet"):
                content += "   - **Snippet:**  \n"
                content += f"     > {e['snippet']}\n"
            content += "\n"

        content += "---\n\n"
    # ------------------------------
    # Possible Address(es)
    # ------------------------------
    addresses = contact_info.get('addresses', [])

    if addresses:
        content += "## Address(es) of interest\n\n"
        content += "> [!info]\n"
        content += "> Automatically detected addresses from open-web sources.\n\n"

        # Get geocoding data from enrichment if available
        geocoding_data = {}
        if enrichment_data and enrichment_data.get('addresses'):
            geocoding_data = enrichment_data['addresses']

        # Build normalized lookup for fuzzy address matching (contact_extraction and address_geocoding use separate LLM calls)
        geocoding_normalized = {normalize_address(k): v for k, v in geocoding_data.items()}

        for i, a in enumerate(addresses, start=1):
            raw_addr = a['address_raw']
            cleaned_addr = clean_address_for_geocoding(raw_addr)
            print(f"  [{i}/{len(addresses)}] Raw: {raw_addr[:60]}...")
            if raw_addr != cleaned_addr:
                print(f"       Cleaned: {cleaned_addr[:60]}...")

            # Use cached coordinates if available (normalized matching)
            cached_coords = None
            normalized_key = normalize_address(raw_addr)
            if normalized_key in geocoding_normalized:
                geocode_result = geocoding_normalized[normalized_key]
                if geocode_result.get('lat') and geocode_result.get('lon'):
                    cached_coords = {
                        'lat': geocode_result['lat'],
                        'lon': geocode_result['lon']
                    }

            # Never geocode inline during report generation - only use cached coordinates or fall back to search URL
            street_view_url = generate_street_view_url(raw_addr, geocode=False, cached_coords=cached_coords)
            content += f"{i}. **{raw_addr}**  \n"
            content += f"   - [📍 View Property]({street_view_url})  \n"
            google_url = generate_google_search_url(a)
            content += f"   - [🔍 Search Google]({google_url})  \n"
            if a.get("source_url"):
                content += f"   - **Source:** {a['source_url']}  \n"
            if a.get("snippet"):
                content += "   - **Snippet:**  \n"
                content += f"     > {a['snippet']}\n"
            content += "\n"

        content += "---\n\n"

    # ------------------------------
    # Ontario Public Sector Employment
    # ------------------------------
    ontario_salaries = data.get("ontario_salaries", {})
    matches = ontario_salaries.get("ontario_salary_matches", [])
    
    if ontario_salaries.get("search_executed"):
        content += "## Public Sector Employment (Ontario)\n\n"
        
        if matches:
            if len(matches) > 1:
                content += "> [!warning] Multiple Potential Matches\n"
                content += "> Multiple individuals found with similar names. Verify correct person using employer or location details.\n\n"
            
            for match in matches:
                matched_name = match.get("matched_name", "")
                confidence = match.get("confidence", "").capitalize()
                match_score = match.get("match_score", 0)
                city_match = match.get("city_match", False)
                records = match.get("records", [])
                years_span = match.get("years_span", "")
                salary_prog = match.get("salary_progression", {})
                
                content += f"### {matched_name} ({confidence} confidence match - {match_score}%)\n\n"
                
                if city_match:
                    content += "**City alignment:** Matched (employer location aligns with known city)  \n\n"
                else:
                    content += "**City alignment:** No city data or no match  \n\n"
                
                if records:
                    # Table header
                    content += "| Year | Employer | Job Title | Sector | Total Compensation |\n"
                    content += "|------|----------|-----------|--------|--------------------|\n"
                    
                    # Table rows (already sorted newest first from app)
                    for record in records:
                        year = record.get("year", "")
                        employer = record.get("employer", "")[:40]  # Truncate long employer names
                        job_title = record.get("job_title", "")[:35]  # Truncate long titles
                        sector = record.get("sector", "")
                        total_comp = record.get("total_comp_formatted", "")
                        
                        content += f"| {year} | {employer} | {job_title} | {sector} | {total_comp} |\n"
                    
                    content += "\n"
                    
                    # Summary info
                    content += f"**Employment span:** {years_span} ({len(records)} years on record)  \n"
                    
                    # Salary progression
                    if salary_prog:
                        oldest_total = salary_prog.get("oldest_total", 0)
                        newest_total = salary_prog.get("newest_total", 0)
                        change_amount = salary_prog.get("change_amount", 0)
                        change_percent = salary_prog.get("change_percent", 0)
                        
                        content += f"**Salary progression:** ${oldest_total:,.2f} → ${newest_total:,.2f} "
                        content += f"({change_amount:+,.2f}, {change_percent:+.1f}%)\n\n"
                
                if len(matches) > 1:
                    content += "---\n\n"
        else:
            content += "> [!info] No Records Found\n"
            content += "> No public sector employment records found for this name in Ontario (2021-2024).\n"
            content += ">\n"
            content += "> *Note: Only includes public sector employees earning $100k+. Private sector, federal, or sub-$100k employment would not appear.*\n\n"
        
        content += "---\n\n"

    # Sources section
    content += "## Sources\n\n"
    
    for query in data.get('queries', []):
        query_id = query.get('id', '')
        query_type = query.get('type', '').replace('_', ' ').title()
        query_text = query.get('query', '')
        hits = query.get('hits', [])
        
        section_name = query_id.replace('_', ' ').title() if query_id else query_type
        
        content += f"### {section_name}\n\n"
        # Determine source from hits (check first hit's source field, or default to google_search)
        source = "google_search"
        if hits and len(hits) > 0:
            source = hits[0].get('source', 'google_search')
            # Debug logging for LinkedIn source detection
            if query_id == "company_name_linkedin":
                all_sources = [h.get('source', 'MISSING') for h in hits]
                print(f"[Report Generator] LinkedIn query: query_id={query_id}, hits_count={len(hits)}, first_hit_source={source}, all_sources={all_sources}")
        if source == "vertex_ai_linkedin":
            source_label = "Vertex AI Search (LinkedIn)"
            source_url = ""
        elif source == "google_search":
            source_label = "Google"
            source_url = f"https://www.google.com/search?q={quote_plus(query_text)}"
        else:
            source_label = source.replace('_', ' ').title()
            source_url = ""
        if source_url:
            content += f"Source: [{source_label}]({source_url}) ·Timestamp: {report_timestamp}\n\n"
        else:
            content += f"Source: {source_label} ·Timestamp: {report_timestamp}\n\n"
        content += "**Query**\n\n"
        content += f"`{query_text}`\n\n"
        content += "**Hits**\n\n"
        
        if hits:
            for i, hit in enumerate(hits, 1):
                title = hit.get('title', 'Untitled')
                url = hit.get('url', '')
                snippet = hit.get('snippet', '')
                
                content += f"{i}. [{title}]({url})  \n"
                content += f"   - **URL:** {url}  \n"
                content += f"   > {snippet}\n\n"
        else:
            content += "*(None)*\n\n"
        
        content += "---\n\n"
    
    output_path = output_dir / f"Identity___{name.replace(' ', '_')}.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Generated: {output_path}")


# ------------------------------
# Corporate report
# ------------------------------
def clean_address(addr: str) -> str:
    """
    Remove leading junk before the real civic number.
    Handles both US (State + ZIP) and Canadian (Province + Postal) addresses.
    E.g. '6 Marvin Igelman 148 Arnold Avenue Vaughan ON L4J 1B7 Canada'
         -> '148 Arnold Avenue Vaughan ON L4J 1B7'
    """
    # Try Canadian province + postal code pattern first
    province_postal = re.search(
        r'\b(AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\s+[A-Z]\d[A-Z]\s?\d[A-Z]\d',
        addr,
        re.IGNORECASE
    )
    
    if province_postal:
        end_pos = province_postal.end()
        address_part = addr[:end_pos]
        
        # Find the rightmost civic number
        civic_matches = list(re.finditer(r'\b(\d{1,6})\s+([A-Za-z][\w\s.-]*?)\s+(?=[A-Z][a-z])', address_part))
        
        if civic_matches:
            last_match = civic_matches[-1]
            return addr[last_match.start():end_pos].strip()
    
    # Try US state + ZIP pattern
    state_zip = re.search(
        r'\b([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\b',
        addr
    )
    
    if state_zip:
        end_pos = state_zip.end()
        address_part = addr[:end_pos]
        
        # Find the rightmost civic number before state
        civic_matches = list(re.finditer(r'\b(\d{1,6})\s+([A-Za-z][\w\s.-]*?)\s*,', address_part))
        
        if civic_matches:
            last_match = civic_matches[-1]
            return addr[last_match.start():end_pos].strip()
    
    # Fallback to original behavior
    m = re.search(r"\b\d{1,6}\s+.*", addr)
    return m.group(0).strip() if m else addr.strip()

def extract_address_components(addr_data: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Extract structured address components from address data dictionary.
    Returns dict with: street, city, province (or state), postal_code (or zip_code)
    """
    street_number = addr_data.get('street_number')
    street_name = addr_data.get('street_name')
    
    # Build street from components if available
    if street_number and street_name:
        street = f"{street_number} {street_name}".strip()
    elif street_name:
        street = street_name
    elif street_number:
        street = str(street_number)
    else:
        # Fallback: try to extract from address_raw using regex
        street = None
        addr_raw = addr_data.get('address_raw', '')
        # Try to extract street (number + name) from raw address
        street_match = re.search(r'^(\d{1,6}\s+[A-Za-z0-9.\-\s]+?)(?:\s*,\s*|\s+)(?=[A-Z][a-z])', addr_raw)
        if street_match:
            street = street_match.group(1).strip()
    
    city = addr_data.get('city')
    province = addr_data.get('province')
    state = addr_data.get('state')
    postal_code = addr_data.get('postal_code')
    zip_code = addr_data.get('zip_code')
    
    # If structured components not available, try parsing from address_raw
    if not city or not (province or state) or not (postal_code or zip_code):
        addr_raw = addr_data.get('address_raw', '')
        
        # Try Canadian format
        ca_match = re.search(
            r'\b([A-Za-z.\- ]{2,40})\s+(AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\s+([A-Z]\d[A-Z]\s?\d[A-Z]\d)',
            addr_raw,
            re.IGNORECASE
        )
        if ca_match:
            if not city:
                city = ca_match.group(1).strip()
            if not province:
                province = ca_match.group(2).upper()
            if not postal_code:
                postal_code = ca_match.group(3).upper().replace(' ', '')
        
        # Try US format if Canadian didn't match
        if not (province or state) or not (postal_code or zip_code):
            us_match = re.search(
                r'\b([A-Za-z.\- ]{2,40})\s*,\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)',
                addr_raw
            )
            if us_match:
                if not city:
                    city = us_match.group(1).strip()
                if not state:
                    state = us_match.group(2).upper()
                if not zip_code:
                    zip_code = us_match.group(3)
    
    return {
        "street": street,
        "city": city,
        "province": province,
        "state": state,
        "postal_code": postal_code,
        "zip_code": zip_code,
    }


def generate_canada411_url(addr_data: Dict[str, Any]) -> str:
    """
    Generate Canada411 reverse address search URL.
    Format: https://mobile.canada411.ca/search/?stype=ad&st={street}&ci={city}&pv={province}&pc={postal_code}
    """
    components = extract_address_components(addr_data)
    
    street = components.get('street') or ''
    city = components.get('city') or ''
    province = components.get('province') or ''
    postal_code = components.get('postal_code') or ''
    
    # If no structured components, use address_raw as fallback
    if not street and not city:
        addr_raw = addr_data.get('address_raw', '')
        # Try to extract basic components from raw address
        parts = addr_raw.split(',')
        if len(parts) >= 2:
            street = parts[0].strip()
            city = parts[1].strip()
    
    # Build URL with available components
    params = []
    if street:
        params.append(f"st={quote_plus(street)}")
    if city:
        params.append(f"ci={quote_plus(city)}")
    if province:
        params.append(f"pv={quote_plus(province)}")
    if postal_code:
        params.append(f"pc={quote_plus(postal_code)}")
    
    if params:
        return f"https://mobile.canada411.ca/search/?stype=ad&{'&'.join(params)}"
    else:
        # Fallback: use full address_raw
        return f"https://mobile.canada411.ca/search/?stype=ad&st={quote_plus(addr_data.get('address_raw', ''))}"


def generate_google_search_url(addr_data: Dict[str, Any]) -> str:
    """
    Generate standard Google search URL with address as query.
    """
    address = addr_data.get('address_raw', '')
    return f"https://www.google.com/search?q={quote_plus(address)}"


def generate_google_doc_search_url(addr_data: Dict[str, Any]) -> str:
    """
    Generate Google search URL with filetype operators for document search.
    """
    address = addr_data.get('address_raw', '')
    query = f"{address} filetype:pdf OR filetype:doc OR filetype:docx"
    return f"https://www.google.com/search?q={quote_plus(query)}"


def normalize_address(address: str) -> str:
    """
    Normalize address for deduplication purposes.
    - Lowercase
    - Remove commas and extra whitespace
    - Standardize abbreviations (St->street, Ave->avenue, etc.)
    """
    addr = address.lower().strip()
    
    # Remove commas and collapse whitespace
    addr = re.sub(r',', ' ', addr)
    addr = re.sub(r'\s+', ' ', addr)
    
    # Standardize common abbreviations
    replacements = {
        r'\bst\b': 'street',
        r'\bave\b': 'avenue',
        r'\bavenue\b': 'avenue',
        r'\brd\b': 'road',
        r'\bdr\b': 'drive',
        r'\bblvd\b': 'boulevard',
        r'\bln\b': 'lane',
        r'\bct\b': 'court',
        r'\bpl\b': 'place',
        r'\bter\b': 'terrace',
        r'\bpkwy\b': 'parkway',
        r'\bcir\b': 'circle',
    }
    
    for pattern, replacement in replacements.items():
        addr = re.sub(pattern, replacement, addr)
    
    return addr.strip()


# ------------------------------
# Phone Number Helper Functions
# ------------------------------

def generate_phone_variations(phone_data: Dict[str, str]) -> List[str]:
    """
    Generate common phone number format variations for search.
    Takes phone data with number_digits and returns list of formatted variations.
    """
    digits = phone_data.get('number_digits', '')
    if not digits:
        return []
    
    # Remove any leading 1 (country code) if present - we'll handle it separately
    if len(digits) == 11 and digits[0] == '1':
        digits = digits[1:]
    
    # Must be 10 digits
    if len(digits) != 10:
        return []
    
    area_code = digits[0:3]
    prefix = digits[3:6]
    line_number = digits[6:10]
    
    variations = [
        f"{area_code}-{prefix}-{line_number}",  # 123-456-7890
        f"({area_code}) {prefix}-{line_number}",  # (123) 456-7890
        f"{area_code}.{prefix}.{line_number}",  # 123.456.7890
        f"+1 {area_code} {prefix} {line_number}",  # +1 123 456 7890
    ]
    
    return variations


def generate_google_search_url_for_phone(phone_data: Dict[str, str]) -> str:
    """
    Generate Google search URL with phone number variations using OR operator.
    Format: "123-456-7890" | "(123) 456-7890" | "123.456.7890" | "+1 123 456 7890"
    """
    variations = generate_phone_variations(phone_data)
    if not variations:
        # Fallback to raw number if variations can't be generated
        number_raw = phone_data.get('number_raw', '')
        return f"https://www.google.com/search?q={quote_plus(number_raw)}"
    
    # Join variations with OR operator
    query = " | ".join(f'"{v}"' for v in variations)
    return f"https://www.google.com/search?q={quote_plus(query)}"


def generate_google_doc_search_url_for_phone(phone_data: Dict[str, str]) -> str:
    """
    Generate Google search URL with phone number variations and filetype restrictions.
    Format: (phone variations) (filetype:pdf OR filetype:xls OR filetype:xlsx OR filetype:csv)
    """
    variations = generate_phone_variations(phone_data)
    if not variations:
        # Fallback to raw number if variations can't be generated
        number_raw = phone_data.get('number_raw', '')
        query = f"{number_raw} (filetype:pdf OR filetype:xls OR filetype:xlsx OR filetype:csv)"
        return f"https://www.google.com/search?q={quote_plus(query)}"
    
    # Join variations with OR operator
    phone_query = " | ".join(f'"{v}"' for v in variations)
    query = f"({phone_query}) (filetype:pdf OR filetype:xls OR filetype:xlsx OR filetype:csv)"
    return f"https://www.google.com/search?q={quote_plus(query)}"


# ------------------------------
# Email Helper Functions
# ------------------------------

def extract_email_handle(email: str) -> str:
    """
    Extract handle from email (everything before @).
    Example: "john.doe@example.com" -> "john.doe"
    """
    if not email or '@' not in email:
        return ''
    try:
        return email.split('@')[0].strip()
    except Exception:
        return ''


def generate_google_search_url_for_email(email: str) -> str:
    """
    Generate Google search URL with email as query.
    """
    return f"https://www.google.com/search?q={quote_plus(email)}"


def generate_google_doc_search_url_for_email(email: str) -> str:
    """
    Generate Google search URL with email and filetype restrictions.
    Format: email (filetype:pdf OR filetype:xls OR filetype:xlsx OR filetype:csv)
    """
    query = f"{email} (filetype:pdf OR filetype:xls OR filetype:xlsx OR filetype:csv)"
    return f"https://www.google.com/search?q={quote_plus(query)}"


def generate_corporate_report(data: Dict[str, Any], name: str, output_dir: Path, enrichment_data: Dict[str, Any] = None, corporate_contact_info: Optional[Dict[str, Any]] = None) -> None:
    """Generate the Corporate markdown file.
    
    Args:
        data: Investigation data
        name: Borrower name
        output_dir: Output directory for markdown file
        enrichment_data: Optional pre-fetched enrichment data with 'addresses' key for geocoding
        corporate_contact_info: Optional pre-extracted corporate contact info (phones, emails, addresses).
                               If provided, uses this instead of extracting. If None, extracts from corporate_debug.
    """
    
    corporate = data['corporate_debug']
    report_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    num_direct = corporate['num_direct']
    num_family = corporate['num_family']
    num_total = num_direct + num_family

    # Get dynamic call-out
    corporate_callout = get_corporate_callout(num_total)
    corporate_risk = get_corporate_risk_level(num_total)

    # Mirror Identity: use the same query structure as input
    corporate_queries = [
        {"hits": corporate.get("full_hits_raw", [])},
        {"hits": corporate.get("last_hits_raw", [])},
    ]

    # Extract seed info for context (only needed if we're extracting)
    seed = data.get('seed', {})
    
    # Use provided corporate_contact_info or extract it
    if corporate_contact_info is not None:
        print("[Corporate Report] Using pre-extracted corporate contact info")
        corp_phones = corporate_contact_info.get('phones', [])
        corp_addresses_raw = corporate_contact_info.get('addresses', [])
    else:
        # Single LLM extraction call for corporate queries (backward compatibility)
        print("[Corporate Report] Extracting corporate contact info from queries")
        corporate_contact_info = extract_contact_info_llm(corporate_queries, seed)
        corp_phones = corporate_contact_info.get('phones', [])
        corp_addresses_raw = corporate_contact_info.get('addresses', [])

    # Use first detected address (if any) as the primary address in the snapshot,
    # otherwise fall back to existing borrower_addresses, then generic text.
    if corp_addresses_raw:
        primary_addr = clean_address(corp_addresses_raw[0]['address_raw'])
    else:
        borrower_addresses = corporate.get('borrower_addresses') or []
        primary_addr = borrower_addresses[0] if borrower_addresses else "the primary household address"

    # Collect all address tags (cleaned)
    address_tags = set()
    for a in corp_addresses_raw or []:
        cleaned = clean_address(a['address_raw'])
        addr_slug = slugify(cleaned)
        if addr_slug != "unknown":
            address_tags.add(f"address/{addr_slug}")

    for addr in corporate.get('borrower_addresses') or []:
        cleaned = addr.strip()
        if cleaned:
            addr_slug = slugify(cleaned)
            if addr_slug != "unknown":
                address_tags.add(f"address/{addr_slug}")

    # Corporation tags inferred from registry hits
    corp_tags = set()
    for hit in corporate.get('full_hits_raw', []) or []:
        title = hit.get('title')
        if title:
            corp_slug = slugify(title)
            if corp_slug != "unknown":
                corp_tags.add(f"corp/direct/{corp_slug}")
    for hit in corporate.get('last_hits_raw', []) or []:
        title = hit.get('title')
        if title:
            corp_slug = slugify(title)
            if corp_slug != "unknown":
                corp_tags.add(f"corp/family/{corp_slug}")

    # --- Front matter tags ---
    borrower_slug = slugify(name)
    tags = [
        f"borrower/{borrower_slug}",
        "note/corporate",
        f"risk/corporate/{corporate_risk}",
    ]
    tags.extend(sorted(address_tags))
    tags.extend(sorted(corp_tags))

    tags = sorted(set(tags))
    tags_block = "\n".join(f"  - {t}" for t in tags)
    header = f"---\ntags:\n{tags_block}\n---\n\n"

    # Build contextual content based on number of corporations
    # Build contextual content based on number of corporations
    nav_bar = get_navigation_bar(data, name, 'corporate')
    
    if num_total == 0:
        content = header + nav_bar + f"""> [!{corporate_callout}] Snapshot
> - **Number of direct corporate records:** 0  
> - **Number of family / related-party records:** 0  

> [!{corporate_callout}] Corporate Pattern Summary
> **No corporate directorships detected.**  
> No corporations list this borrower as a director in public registries.

"""
    else:
        severity_text = "Extensive corporate network" if num_total >= 8 else "Corporate directorship and household-linked activity"
        additional_context = ""
        if num_total >= 8:
            additional_context = "This extensive corporate footprint may indicate complex business structures, shared household assets, or related-party holdings not visible in standard employer disclosures."
        else:
            additional_context = "Patterns like these may indicate shared household assets, potential related-party holdings, or business activity not visible in standard employer fields."
        
        content = header + nav_bar + f"""> [!{corporate_callout}] Snapshot
> - **Number of direct corporate records:** {num_direct}  
> - **Number of family / related-party records:** {num_family}  

> [!{corporate_callout}] Corporate Pattern Summary
> **{severity_text} detected.**  
> - **{num_direct} corporation(s)** list this borrower as a director at **{primary_addr}**.  
> - **{num_family} corporation(s)** list relatives with the same surname at the same address.  
>  
> {additional_context}

"""
        
        # Add household-linked corporate activity alert if applicable
        if num_family > 0 or (num_direct > 0 and num_family > 0):
            content += "\n---\n\n"
            content += "> [!warning] Household-Linked Corporate Activity Detected\n\n"
            content += "Multiple corporations list this borrower (and relatives with the same surname) at the same residential address, which may indicate related-party holdings or undisclosed business activity.\n\n"
            content += "Operational impact: Increases the likelihood of hidden financial complexity and potential misalignment with stated employer/income details.\n\n"
            content += "Suggested action: Review the related corporations/directorships for operating status, role, and relevance to the borrower's declared employment and income.\n\n"
    
    # ------------------------------
    # Possible Phone Number(s)
    # (mirror Identity: only show if any detected)
    # ------------------------------
    if corp_phones:
        content += """
---

## Borrower-related phone number(s)

> [!info]
> Automatically detected phone numbers from corporate-related open-web / registry snippets.

"""
        for i, p in enumerate(corp_phones, start=1):
            content += f"{i}. **{p['number_raw']}**  \n"
            if p.get("source_url"):
                content += f"   - **Source:** {p['source_url']}  \n"
            if p.get("snippet"):
                content += "   - **Snippet:**  \n"
                content += f"     > {p['snippet']}\n"
            content += "\n"

    # ------------------------------
    # Possible Address(es)
    # (mirror Identity: only show if any detected)
    # ------------------------------
    if corp_addresses_raw:
        content += """
---

## Borrower-related addresses

> [!info]
> Automatically detected addresses from corporate-related open-web / registry snippets.

"""
        # Get geocoding data from enrichment if available
        geocoding_data = {}
        if enrichment_data and enrichment_data.get('addresses'):
            geocoding_data = enrichment_data['addresses']
        
        # Build normalized lookup for fuzzy address matching
        geocoding_normalized = {normalize_address(k): v for k, v in geocoding_data.items()}
        
        for i, a in enumerate(corp_addresses_raw, start=1):
            cleaned = clean_address(a['address_raw'])
            print(f"  [{i}/{len(corp_addresses_raw)}] Corporate address: {cleaned[:60]}...")
            
            # Use cached coordinates if available (normalized matching)
            cached_coords = None
            lookup_key = a['address_raw']
            normalized_key = normalize_address(lookup_key)
            
            if normalized_key in geocoding_normalized:
                geocode_result = geocoding_normalized[normalized_key]
                if geocode_result.get('lat') and geocode_result.get('lon'):
                    cached_coords = {
                        'lat': geocode_result['lat'],
                        'lon': geocode_result['lon']
                    }
                    print(f"    ✓ Using cached coordinates: {cached_coords['lat']:.6f}, {cached_coords['lon']:.6f}")
                else:
                    print(f"    ⚠️  Cached entry found but no coordinates (error: {geocode_result.get('error', 'unknown')})")
            else:
                print(f"    ⚠️  No cached coordinates found for '{lookup_key[:50]}...' (normalized: '{normalized_key[:50]}...', total cached: {len(geocoding_data)})")
            
            # Never geocode inline during report generation - only use cached coordinates or fall back to search URL
            street_view_url = generate_street_view_url(cleaned, geocode=False, cached_coords=cached_coords)
            content += f"{i}. **{cleaned}**  \n"
            content += f"   - [📍 View Property]({street_view_url})  \n"
            if a.get("source_url"):
                content += f"   - **Source:** {a['source_url']}  \n"
            if a.get("snippet"):
                content += "   - **Snippet:**  \n"
                content += f"     > {a['snippet']}\n"
            content += "\n"
    
    content += "\n---\n\n"
    
    # Add corporate registry hits (results first, like Litigation/Regulator)
    content += "## Corporate registry hits\n\n"
    if corporate['full_hits_raw']:
        for i, hit in enumerate(corporate['full_hits_raw'], 1):
            content += f"{i}. [{hit['title']}]({hit['url']})  \n"
            content += f"   > {hit['snippet']}\n\n"
    else:
        content += "*(None)*\n\n"
    
    content += "---\n\n"
    
    # Add additional registry hits
    content += "## Additional related-party registry hits\n\n"
    if corporate['last_hits_raw']:
        for i, hit in enumerate(corporate['last_hits_raw'], 1):
            content += f"{i}. [{hit['title']}]({hit['url']})  \n"
            content += f"   > {hit['snippet']}\n\n"
    else:
        content += "*(None)*\n\n"
    
    content += "---\n\n"
    
    # Add search queries (queries last, like Litigation/Regulator)
    content += "## Corporate queries run\n\n"
    
    content += "### Full name + address search\n\n"
    google_url_full = f"https://www.google.com/search?q={quote_plus(corporate['full_query'])}"
    content += f"Source: [Google]({google_url_full}) · Timestamp: {report_timestamp}\n\n"
    content += f"- **Query run:**  \n"
    content += f"  `{corporate['full_query']}`  \n"
    content += "\n"
    
    content += "### Last name + address search\n\n"
    google_url_last = f"https://www.google.com/search?q={quote_plus(corporate['last_query'])}"
    content += f"Source: [Google]({google_url_last}) · Timestamp: {report_timestamp}\n\n"
    content += f"- **Query run:**  \n"
    content += f"  `{corporate['last_query']}`  \n"
    content += "\n"         
    
    output_path = output_dir / f"Corporate___{name.replace(' ', '_')}.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Generated: {output_path}")


# ------------------------------
# Litigation report
# ------------------------------

def generate_litigation_report(data: Dict[str, Any], name: str, output_dir: Path) -> None:
    """Generate the Adverse Media markdown file."""
    
    litigation = data.get('litigation_phase2', {})
    report_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    hits = litigation.get('confirmed_litigation_hits', []) or []
    num_events = len(hits)
    
    # Get dynamic call-out
    litigation_callout = get_litigation_callout(num_events)
    litigation_risk = get_litigation_risk_level(num_events)

    # --- Front matter tags ---
    borrower_slug = slugify(name)
    tags = [
        f"borrower/{borrower_slug}",
        "note/adverse_media",
        f"risk/adverse_media/{litigation_risk}",
    ]

    courts = set()
    lit_types = set()
    lit_cases = set()

    for i, hit in enumerate(hits, start=1):
        court_name = hit.get('source')
        if court_name:
            court_slug = slugify(court_name)
            if court_slug != "unknown":
                courts.add(f"court/{court_slug}")
        lit_type = hit.get('type')
        if lit_type:
            type_slug = slugify(lit_type)
            if type_slug != "unknown":
                lit_types.add(f"adverse_media/type/{type_slug}")
        year = hit.get('year')
        case_id_slug = None
        if year:
            case_id_slug = slugify(f"{year}_{i}")
        else:
            case_id_slug = slugify(str(i))
        if case_id_slug != "unknown":
            lit_cases.add(f"lit_case/{case_id_slug}")

    tags.extend(sorted(courts))
    tags.extend(sorted(lit_types))
    tags.extend(sorted(lit_cases))

    tags = sorted(set(tags))
    tags_block = "\n".join(f"  - {t}" for t in tags)
    header = f"---\ntags:\n{tags_block}\n---\n\n"
    
    # Build contextual rationale
    rationale_text = litigation.get('rationale', '')
    if num_events == 0 and not rationale_text:
        rationale_text = "✓ No adverse media or legal complaints found in public records."
    elif num_events > 0 and rationale_text:
        if num_events >= 5:
            rationale_text = f"⚠️ Extensive adverse media history identified. {rationale_text}"
        elif num_events >= 3:
            rationale_text = f"⚠️ Multiple legal matters identified. {rationale_text}"
    
    content = header + get_navigation_bar(data, name, 'litigation') + f"""> [!{litigation_callout}] Snapshot
> - **Confirmed adverse media events:** {num_events}  
> 

---

## Overview

> [!note] Rationale
> {rationale_text if rationale_text else 'No adverse media rationale available.'}

---

## Confirmed adverse media hits

"""
    
    # Add litigation hits
    for i, hit in enumerate(hits, 1):
        content += f"### {i}. {hit.get('title', 'Untitled')}\n\n"
        content += f"- **URL:** <{hit.get('url', '')}>  \n"
        content += f"- **Title:** {hit.get('title', 'Untitled')}  \n"
        content += f"- **Source:** {hit.get('source', 'Unknown')}  \n"
        year = hit.get('year', '')
        if year:
            content += f"- **Year:** {year}  \n"
        content += f"- **Type:** {hit.get('type', 'Adverse Media / complaints')}  \n"
        content += f"- **Summary:**  \n"
        content += f"  {hit.get('summary', 'No summary available')}  \n"
        content += f"- **Confidence:** {hit.get('confidence', 'unknown')}  \n"
        content += "\n---\n\n"
    
    # Add litigation queries
    content += "## Litigation queries run\n\n"
    for query in data.get('litigation_queries', []):
        query_family = query['query_family'].replace('_', ' ').title()
        content += f"### {query_family}\n\n"
        google_url = f"https://www.google.com/search?q={quote_plus(query['query_run'])}"
        content += f"Source: [Google]({google_url}) · Timestamp: {report_timestamp}\n\n"
        content += f"- **Query family:** `{query['query_family']}`  \n"
        content += f"- **Query run:**  \n"
        content += f"  `{query['query_run']}`  \n"
        content += "\n**Hits**\n\n"
        
        if query['hits']:
            for i, hit in enumerate(query['hits'], 1):
                content += f"{i}. [{hit['title']}]({hit['url']})  \n"
                content += f"   > {hit['snippet']}\n\n"
        else:
            content += "*(None)*\n\n"
    
    output_path = output_dir / f"Adverse_Media___{name.replace(' ', '_')}.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Generated: {output_path}")


# ------------------------------
# Regulator report
# ------------------------------

def generate_regulator_report(data: Dict[str, Any], name: str, output_dir: Path) -> None:
    """Generate the Regulator markdown file."""
    
    regulator = data['regulator_phase2']
    report_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    hits = regulator['confirmed_regulator_hits']
    num_events = len(hits)
    
    # Get dynamic call-out
    regulator_callout = get_regulator_callout(num_events)
    regulator_risk = get_regulator_risk_level(num_events)
    
    # Get unique regulators
    regulators = set()
    for hit in hits:
        regulators.add(hit['regulator'])

    # --- Front matter tags ---
    borrower_slug = slugify(name)
    tags = [
        f"borrower/{borrower_slug}",
        "note/regulator",
        f"risk/regulator/{regulator_risk}",
    ]

    regulator_tags = set()
    reg_event_tags = set()
    for i, hit in enumerate(hits, start=1):
        reg_name = hit.get('regulator')
        if reg_name:
            reg_slug = slugify(reg_name)
            if reg_slug != "unknown":
                regulator_tags.add(f"regulator/{reg_slug}")
                event_id_slug = slugify(str(i))
                if event_id_slug != "unknown":
                    reg_event_tags.add(f"reg_event/{reg_slug}/{event_id_slug}")

    tags.extend(sorted(regulator_tags))
    tags.extend(sorted(reg_event_tags))

    tags = sorted(set(tags))
    tags_block = "\n".join(f"  - {t}" for t in tags)
    header = f"---\ntags:\n{tags_block}\n---\n\n"
    
    # Build contextual rationale
    rationale_text = regulator.get('rationale', '')
    if num_events == 0 and rationale_text:
        rationale_text = f"✓ Clean regulatory record. {rationale_text}"
    elif num_events > 0 and rationale_text:
        if num_events >= 3:
            rationale_text = f"⚠️ Significant regulatory history. {rationale_text}"
        else:
            rationale_text = f"⚠️ Regulatory events require review. {rationale_text}"
    
    regulators_str = ', '.join(sorted(regulators)) if regulators else ''
    content = header + get_navigation_bar(data, name, 'regulator') + f"""> [!{regulator_callout}] Snapshot
> - **Confirmed regulator events:** {num_events} ({regulators_str})
> - **Regulators with substantive hits:** {regulators_str if regulators_str else 'None'}
> 

---

## Overview

> [!note] Rationale
> {rationale_text if rationale_text else 'No regulatory rationale available.'}

---

## Confirmed regulator hits

"""
    
    # Add regulator hits
    for i, hit in enumerate(hits, 1):
        content += f"### {i}. {hit['regulator']} — {hit['title']}\n\n"
        content += f"- **Regulator:** {hit['regulator']}  \n"
        content += f"- **URL:** <{hit['url']}>  \n"
        content += f"- **Title:** {hit['title']}  \n"
        content += f"- **Summary:**  \n"
        content += f"  {hit['summary']}  \n"
        content += f"- **Confidence:** {hit['confidence']}  \n"
        content += "\n---\n\n"
    
    # Add regulator queries
    content += "## Regulator queries run\n\n"
    for query in data.get('regulator_queries', []):
        query_family = query['query_family'].replace('_', ' ').title()
        regulator_name = query['regulator']
        
        content += f"### {query_family}\n\n"
        google_url = f"https://www.google.com/search?q={quote_plus(query['query_run'])}"
        content += f"Source: [Google]({google_url}) · Timestamp: {report_timestamp}\n\n"
        content += f"- **Regulator:** {regulator_name}  \n"
        content += f"- **Query family:** `{query['query_family']}`  \n"
        content += f"- **Query run:**  \n"
        content += f"  `{query['query_run']}`  \n"
        
        content += "\n**Hits**\n\n"
        
        if query['hits']:
            for i, hit in enumerate(query['hits'], 1):
                content += f"{i}. [{hit['title']}]({hit['url']})  \n"
                content += f"   > {hit['snippet']}\n\n"
        else:
            content += "*(None)*\n\n"
        
        content += "---\n\n"
    
    output_path = output_dir / f"Regulator___{name.replace(' ', '_')}.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Generated: {output_path}")


# ------------------------------
# Main
# ------------------------------
def main():
    """Main function to generate all reports."""
    if len(sys.argv) < 2:
        print("Usage: python generate_markdown_reports.py <json_file> [output_dir]")
        print("\nExample:")
        print("  python generate_markdown_reports.py data.json ./output")
        sys.exit(1)
    
    json_file = sys.argv[1]
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("./output")
    
    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Load JSON data
    print(f"Loading JSON from: {json_file}")
    data = load_json(json_file)
    
    # Extract name from seed data
    name = format_name(data['seed']['full_name'])
    
    # Generate all reports
    print(f"\nGenerating reports for: {name}")
    print(f"Output directory: {output_dir}\n")
    
    generate_borrower_summary(data, name, output_dir)
    generate_identity_report(data, name, output_dir)
    generate_corporate_report(data, name, output_dir)
    generate_litigation_report(data, name, output_dir)
    generate_regulator_report(data, name, output_dir)
    
    print(f"\n✓ All reports generated successfully in {output_dir}")


# ==========================================
# Skip Trace Versions of Report Functions
# ==========================================

def get_navigation_bar_skiptrace(data: Dict[str, Any], name: str, current_report: str) -> str:
    """
    Generate a navigation bar for skip trace reports.
    """
    wiki_name = name.replace(' ', '_')

    nav_bar = f"""> [!abstract] -
> **[[Identity___{wiki_name}|🟢 Identity]]**

---

"""
    return nav_bar


def generate_identity_report_skiptrace(data: Dict[str, Any], name: str, output_dir: Path, company_domain: str = None, enrichment_data: Dict[str, Any] = None) -> None:
    """
    Generate the Identity markdown file for skip trace (no salaries, no alerts/warnings).
    
    Args:
        data: Investigation data
        name: Borrower name
        output_dir: Output directory for markdown file
        company_domain: Optional company domain from company_domain_lookup
        enrichment_data: Optional pre-fetched enrichment data with 'domains' and 'addresses' keys
    """
    # This is a simplified version that removes:
    # - Public salaries section
    # - All contextual alerts/warnings (no breach warnings, disposable email alerts, LinkedIn alerts, domain/MX warnings)
    
    report_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    scored = data['scored']
    seed = data['seed']
    
    # Build snapshot section
    email = seed['email']
    location = scored.get('location', {}).get('city', 'Unknown')
    location_confidence = scored.get('location', {}).get('confidence', 'unknown')
    
    # Extract domain and perform whois lookup if not personal email
    domain = extract_email_domain(email)
    whois_result = None
    mx_result = None
    
    # Use pre-fetched enrichment data if available
    if enrichment_data and enrichment_data.get('domains'):
        domain_enrichment = enrichment_data['domains'].get(domain, {})
        if domain_enrichment:
            whois_result = domain_enrichment.get('whois')
            mx_result = domain_enrichment.get('mx')
            print(f"[Identity Report SkipTrace] Using pre-fetched enrichment data for domain: {domain}")
    
    # Fallback to inline lookups if enrichment data not available
    if domain and not is_personal_email_domain(domain):
        if not whois_result:
            print(f"[Identity Report SkipTrace] Performing whois lookup for business domain: {domain}")
            whois_result = get_domain_registration_date(domain)
        if not mx_result:
            print(f"[Identity Report SkipTrace] Performing MX record lookup for business domain: {domain}")
            mx_result = check_domain_mx_records(domain)
    
    # Perform company domain checks if provided
    company_whois_result = None
    company_mx_result = None
    if company_domain:
        company_domain = company_domain.strip()
        if company_domain:
            # Use pre-fetched enrichment data if available
            if enrichment_data and enrichment_data.get('domains') and company_domain in enrichment_data['domains']:
                company_enrichment = enrichment_data['domains'][company_domain]
                company_whois_result = company_enrichment.get('whois')
                company_mx_result = company_enrichment.get('mx')
            else:
                company_whois_result = get_domain_registration_date(company_domain)
                company_mx_result = check_domain_mx_records(company_domain)
    
    # Check Gravatar profile if personal email
    gravatar_result = None
    if domain and is_personal_email_domain(domain):
        gravatar_result = get_gravatar_profile(email)
    
    # Load disposable email blocklist and check if email is disposable
    blocklist_path = Path(__file__).parent / "disposable_email_blocklist.conf"
    disposable_blocklist = load_disposable_email_blocklist(blocklist_path)
    is_disposable = is_disposable_email_domain(email, disposable_blocklist)
    
    contactability = data.get('contactability', {})
    score = contactability.get('score', 'unknown')
    reason = contactability.get('reason', 'No information available')
    num_social = contactability.get('num_social', 0)
    num_breaches = contactability.get('num_breaches', 0)
    footprint_bucket = contactability.get('footprint_bucket', 'unknown')
    breach_bucket = contactability.get('breach_bucket', 'unknown')
    
    breaches = data.get('breaches', [])
    top_handles = scored.get('top_handles', [])
    queries = data.get('queries', [])
    
    # Calculate earliest breach date
    earliest_breach_date = None
    if breaches:
        valid_dates = []
        for breach in breaches:
            date_str = breach.get('date')
            if date_str:
                try:
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                    valid_dates.append(date_obj)
                except (ValueError, TypeError):
                    pass
        if valid_dates:
            earliest_breach_date = min(valid_dates).strftime("%Y-%m-%d")
    
    # --- Front matter tags ---
    borrower_slug = slugify(name)
    email_slug = slugify(email)
    location_slug = slugify(location)
    contact_score_slug = slugify(str(score))
    footprint_slug = slugify(str(footprint_bucket))
    breach_bucket_slug = slugify(str(breach_bucket))
    
    tags = [
        f"borrower/{borrower_slug}",
        "note/identity",
        f"email/{email_slug}",
        f"location/{location_slug}",
        f"contactability/score/{contact_score_slug}",
        f"contactability/footprint/{footprint_slug}",
        f"contactability/breach/{breach_bucket_slug}",
    ]
    
    # Breach tags
    for breach in breaches or []:
        breach_name = breach.get('name')
        if breach_name:
            breach_slug = slugify(breach_name)
            if breach_slug != "unknown":
                tags.append(f"breach/{breach_slug}")
    
    # Social platform + handle tags
    platforms = set()
    for handle in top_handles or []:
        platform = handle.get('platform')
        handle_name = handle.get('handle')
        if platform:
            platform_slug = slugify(platform)
            if platform_slug != "unknown":
                platforms.add(platform_slug)
                tags.append(f"social/platform/{platform_slug}")
                if handle_name:
                    handle_slug = slugify(handle_name)
                    if handle_slug != "unknown":
                        tags.append(f"social/handle/{platform_slug}/{handle_slug}")
    
    tags = sorted(set(tags))
    tags_block = "\n".join(f"  - {t}" for t in tags)
    header = f"---\ntags:\n{tags_block}\n---\n\n"
    
    # Get primary and secondary handles
    primary_handle = top_handles[0] if len(top_handles) > 0 else None
    secondary_handle = top_handles[1] if len(top_handles) > 1 else None
    
    content = header
    content += get_navigation_bar_skiptrace(data, name, 'identity')
    content += f"> [!info] Snapshot\n"
    content += f"> - **Name:** {name}  \n"
    content += f"> - **Email:** `{email}`  \n"
    content += f"> - **Location (scored):** {location} ({location_confidence} confidence)  \n"
    
    if primary_handle:
        content += f"> - **Primary handle:** {primary_handle['platform']} — `{primary_handle['handle']}` ({primary_handle.get('confidence', 'medium')} confidence)  \n"
    
    if secondary_handle:
        content += f"> - **Secondary handle:** {secondary_handle['platform']} — `{secondary_handle['handle']}` ({secondary_handle.get('confidence', 'medium')} confidence)\n"
    
    content += "\n\n"
    content += "\n---\n\n"
    content += "## Identity Confirmation\n\n"
    content += "> [!note] Rationale\n"
    content += f"> {scored.get('rationale', 'No rationale provided')}\n\n"
    
    # Grounding metadata section (below Rationale)
    grounding_metadata = data.get('grounding_metadata', {})
    grounding_sources = grounding_metadata.get('grounding_sources', [])
    search_queries = grounding_metadata.get('search_queries', [])
    
    if search_queries:
        content += "### Grounding Searches\n\n"
        content += "The following searches were performed by the AI to verify identity:\n\n"
        for query in search_queries:
            encoded_query = quote_plus(query)
            google_url = f"https://www.google.com/search?q={encoded_query}"
            content += f"- [{query}]({google_url})\n"
        content += "\n"
    
    content += "---\n\n"
    
    # Social handles section
    content += "## Social handles\n\n"
    for handle in top_handles or []:
        platform = handle['platform']
        handle_name = handle['handle']
        handle_url = handle['url']
        confidence = handle.get('confidence', 'medium')
        
        content += f"- **{platform}**  \n"
        content += f"  - Handle: `{handle_name}`  \n"
        content += f"  - Confidence: **{confidence}**  \n"
        content += f"  - URL: <{handle_url}>  \n"
        whatsmyname_url = f"https://whatsmyname.app/?q={quote_plus(handle_name)}"
        content += f"  - [🔍 Search handle on 500 sites]({whatsmyname_url})  \n"

        # Try to find a snippet for this handle from the queries
        snippet = None
        for query in data.get('queries', []):
            for hit in query.get('hits', []):
                hit_url = hit.get('url', '').lower()
                if handle_url.lower() == hit_url or (hit_url and hit_url.rstrip('/') == handle_url.lower().rstrip('/')):
                    snippet = hit.get('snippet', '')
                    break
            if snippet:
                break

        if snippet:
            content += f"  - Snippet:  \n"
            content += f"    > {snippet}\n"

        content += "\n"

    content += "---\n\n"

    # Data Breaches section (NO ALERTS - just list the breaches)
    content += "## Data Breaches\n\n"
    if breaches and len(breaches) > 0:
        # Sort breaches chronologically by date (oldest first)
        def sort_key(breach):
            date_str = breach.get('date')
            if date_str:
                try:
                    return datetime.strptime(date_str, "%Y-%m-%d")
                except (ValueError, TypeError):
                    return datetime.max
            else:
                return datetime.max
        
        sorted_breaches = sorted(breaches, key=sort_key)
        
        # Display as a table
        content += "| Breach Name | Date |\n"
        content += "|-------------|------|\n"
        
        for breach in sorted_breaches:
            breach_name = breach.get('name', 'Unknown')
            breach_date = breach.get('date', '')
            if breach_date:
                content += f"| {breach_name} | {breach_date} |\n"
            else:
                content += f"| {breach_name} | *(Unknown)* |\n"
    else:
        content += "*(None)*\n"
    
    content += f"Source: [Have I Been Pwned](https://haveibeenpwned.com) ·Timestamp: {report_timestamp}\n\n"
    # NOTE: NO "No Breach History Detected" warning - removed for skip trace
    
    # NOTE: NO disposable email alert - removed for skip trace
    # NOTE: NO LinkedIn connections alert - removed for skip trace
    
    content += "\n---\n\n"
    
    # Gravatar profile section (if available)
    if gravatar_result and gravatar_result.get('success'):
        content += "### Gravatar Profile\n\n"
        content += f"![Gravatar Avatar]({gravatar_result['thumbnail_url']})  \n"
        content += f"[View Full Profile →]({gravatar_result['profile_url']})  \n"
        content += "\n"
    
    content += "---\n\n"
    
    # Domain Registration section (informational only, NO warnings/alerts)
    if whois_result and whois_result.get('success') and whois_result.get('registration_date'):
        reg_date = whois_result['registration_date']
        
        # Calculate age for display
        try:
            reg_date_obj = datetime.strptime(reg_date, "%Y-%m-%d")
            today = datetime.now()
            age_days = (today - reg_date_obj).days
            if age_days < 30:
                age_text = f"{age_days} days"
            elif age_days < 365:
                age_text = f"{age_days // 30} months"
            else:
                age_text = f"{age_days // 365} years"
        except Exception:
            age_text = "unknown"
        
        content += "## Domain Registration\n\n"
        content += f"> [!info] Domain Registration\n"
        content += f"> **Domain:** {domain}  \n"
        content += f"> **Registration Date:** {reg_date}  \n"
        content += f"> **Domain Age:** {age_text}  \n"
        content += "\n---\n\n"
        # NOTE: NO domain age warnings/alerts - removed for skip trace
    
    # MX Record Analysis section (informational only, NO warnings/alerts)
    if domain and not is_personal_email_domain(domain):
        content += "## Email Infrastructure (MX Records)\n\n"
        content += f"> [!info] Email Infrastructure\n"
        content += f"> **Domain:** {domain}  \n"
        
        if mx_result and mx_result.get("success"):
            primary_mx = mx_result.get("mx_records", [""])[0] if mx_result.get("mx_records") else "N/A"
            provider = mx_result.get("provider_detected", "Unknown")
            risk_level = mx_result.get("risk_level", "UNKNOWN")
            status = mx_result.get("status", "Unknown")
            
            content += f"> **Primary MX Record:** `{primary_mx}`  \n"
            content += f"> **Provider:** {provider}  \n"
            content += f"> **Risk Level:** {risk_level}  \n"
            content += f"> **Status:** {status}  \n"
        else:
            error_msg = mx_result.get("error", "Unknown error") if mx_result else "MX lookup not performed"
            status = mx_result.get("status", "Lookup Failed") if mx_result else "Lookup Failed"
            risk_level = mx_result.get("risk_level", "UNKNOWN") if mx_result else "UNKNOWN"
            
            content += f"> **Status:** {status}  \n"
            content += f"> **Risk Level:** {risk_level}  \n"
            if error_msg:
                content += f"> **Error:** {error_msg}  \n"
        # NOTE: NO MX record warnings/alerts - removed for skip trace
        
        content += "\n---\n\n"
    
    # Company Domain Registration section (informational only, NO warnings/alerts)
    if company_whois_result and company_whois_result.get('success') and company_whois_result.get('registration_date'):
        reg_date = company_whois_result['registration_date']
        
        try:
            reg_date_obj = datetime.strptime(reg_date, "%Y-%m-%d")
            today = datetime.now()
            age_days = (today - reg_date_obj).days
            if age_days < 30:
                age_text = f"{age_days} days"
            elif age_days < 365:
                age_text = f"{age_days // 30} months"
            else:
                age_text = f"{age_days // 365} years"
        except Exception:
            age_text = "unknown"
        
        content += "## Company Domain Registration\n\n"
        content += f"> [!info] Company Domain Registration\n"
        content += f"> **Domain:** {company_domain}  \n"
        content += f"> **Registration Date:** {reg_date}  \n"
        content += f"> **Domain Age:** {age_text}  \n"
        content += "\n---\n\n"
        # NOTE: NO company domain warnings/alerts - removed for skip trace
    
    # Company Email Infrastructure section (informational only, NO warnings/alerts)
    if company_domain:
        content += "## Company Email Infrastructure (MX Records)\n\n"
        content += f"> [!info] Company Email Infrastructure\n"
        content += f"> **Domain:** {company_domain}  \n"
        
        if company_mx_result and company_mx_result.get("success"):
            primary_mx = company_mx_result.get("mx_records", [""])[0] if company_mx_result.get("mx_records") else "N/A"
            provider = company_mx_result.get("provider_detected", "Unknown")
            risk_level = company_mx_result.get("risk_level", "UNKNOWN")
            status = company_mx_result.get("status", "Unknown")
            
            content += f"> **Primary MX Record:** `{primary_mx}`  \n"
            content += f"> **Provider:** {provider}  \n"
            content += f"> **Risk Level:** {risk_level}  \n"
            content += f"> **Status:** {status}  \n"
        else:
            error_msg = company_mx_result.get("error", "Unknown error") if company_mx_result else "MX lookup not performed"
            status = company_mx_result.get("status", "Lookup Failed") if company_mx_result else "Lookup Failed"
            risk_level = company_mx_result.get("risk_level", "UNKNOWN") if company_mx_result else "UNKNOWN"
            
            content += f"> **Status:** {status}  \n"
            content += f"> **Risk Level:** {risk_level}  \n"
            if error_msg:
                content += f"> **Error:** {error_msg}  \n"
        # NOTE: NO company MX warnings/alerts - removed for skip trace
        
        content += "\n---\n\n"
    
    # Possible Phone Number(s) section
    # Get contact info from enrichment data (extracted during Phase 2)
    contact_info = {}
    if enrichment_data and enrichment_data.get('contacts'):
        contact_info = enrichment_data.get('contacts', {})
    else:
        # Fallback to empty structure if enrichment data not available
        contact_info = {"phones": [], "emails": [], "addresses": []}

    phones = contact_info.get('phones', [])
    
    if phones:
        content += "## Phone Number(s) of interest\n\n"
        content += "> [!info]\n"
        content += "> Automatically detected phone numbers from open-web sources.\n\n"
        
        for i, p in enumerate(phones, start=1):
            content += f"{i}. **{p['number_raw']}**  \n"
            google_url = generate_google_search_url_for_phone(p)
            content += f"   - [🔍 Search Google]({google_url})  \n"
            if p.get("source_url"):
                content += f"   - **Source:** {p['source_url']}  \n"
            if p.get("snippet"):
                content += "   - **Snippet:**  \n"
                content += f"     > {p['snippet']}\n"
            content += "\n"

        content += "---\n\n"

    # Possible Email Address(es) section
    emails = contact_info.get('emails', [])
    if emails:
        content += "## Email Address(es) of interest\n\n"
        content += "> [!info]\n"
        content += "> Automatically detected email addresses from open-web sources.\n\n"

        for i, e in enumerate(emails, start=1):
            google_url = generate_google_search_url_for_email(e['email'])
            content += f"{i}. **[{e['email']}]({google_url})**  \n"
            if e.get("source_url"):
                content += f"   - **Source:** {e['source_url']}  \n"
            if e.get("snippet"):
                content += "   - **Snippet:**  \n"
                content += f"     > {e['snippet']}\n"
            content += "\n"

        content += "---\n\n"

    # Possible Address(es) section
    # Get addresses from contact_info (has source_url and snippet)
    # Use enrichment_data only for geocoding coordinates
    addresses = contact_info.get('addresses', [])

    if addresses:
        content += "## Address(es) of interest\n\n"
        content += "> [!info]\n"
        content += "> Automatically detected addresses from open-web sources.\n\n"

        # Get geocoding data from enrichment if available
        geocoding_data = {}
        if enrichment_data and enrichment_data.get('addresses'):
            geocoding_data = enrichment_data['addresses']

        # Build normalized lookup for fuzzy address matching (contact_extraction and address_geocoding use separate LLM calls)
        geocoding_normalized = {normalize_address(k): v for k, v in geocoding_data.items()}

        for i, a in enumerate(addresses, start=1):
            raw_addr = a['address_raw']
            content += f"{i}. **{raw_addr}**  \n"

            # Use cached coordinates if available (normalized matching)
            cached_coords = None
            normalized_key = normalize_address(raw_addr)
            if normalized_key in geocoding_normalized:
                geocode_result = geocoding_normalized[normalized_key]
                if geocode_result.get('lat') and geocode_result.get('lon'):
                    cached_coords = {
                        'lat': geocode_result['lat'],
                        'lon': geocode_result['lon']
                    }

            # Never geocode inline during report generation - only use cached coordinates or fall back to search URL
            street_view_url = generate_street_view_url(raw_addr, geocode=False, cached_coords=cached_coords)
            content += f"   - [📍 View Property]({street_view_url})  \n"
            google_url = generate_google_search_url(a)
            content += f"   - [🔍 Search Google]({google_url})  \n"

            if a.get("source_url"):
                content += f"   - **Source:** {a['source_url']}  \n"
            if a.get("snippet"):
                content += "   - **Snippet:**  \n"
                content += f"     > {a['snippet']}\n"
            content += "\n"

        content += "---\n\n"

    # Sources section
    content += "## Sources\n\n"
    for query in data.get('queries', []):
        query_id = query.get('id', '')
        query_type = query.get('type', '').replace('_', ' ').title()
        query_text = query.get('query', '')
        hits = query.get('hits', [])
        
        section_name = query_id.replace('_', ' ').title() if query_id else query_type
        
        content += f"### {section_name}\n\n"
        # Determine source from hits (check first hit's source field, or default to google_search)
        source = "google_search"
        if hits and len(hits) > 0:
            source = hits[0].get('source', 'google_search')
            # Debug logging for LinkedIn source detection
            if query_id == "company_name_linkedin":
                all_sources = [h.get('source', 'MISSING') for h in hits]
                print(f"[Report Generator] LinkedIn query: query_id={query_id}, hits_count={len(hits)}, first_hit_source={source}, all_sources={all_sources}")
        if source == "vertex_ai_linkedin":
            source_label = "Vertex AI Search (LinkedIn)"
            source_url = ""
        elif source == "google_search":
            source_label = "Google"
            source_url = f"https://www.google.com/search?q={quote_plus(query_text)}"
        else:
            source_label = source.replace('_', ' ').title()
            source_url = ""
        if source_url:
            content += f"Source: [{source_label}]({source_url}) ·Timestamp: {report_timestamp}\n\n"
        else:
            content += f"Source: {source_label} ·Timestamp: {report_timestamp}\n\n"
        content += "**Query**\n\n"
        content += f"`{query_text}`\n\n"
        content += "**Hits**\n\n"
        
        if hits:
            for i, hit in enumerate(hits, 1):
                title = hit.get('title', 'Untitled')
                url = hit.get('url', '')
                snippet = hit.get('snippet', '')
                content += f"{i}. [{title}]({url})  \n"
                content += f"   - **URL:** {url}  \n"
                content += f"   > {snippet}\n\n"
        else:
            content += "*(None)*\n\n"
        
        content += "---\n\n"
    
    # Write file
    wiki_name = name.replace(' ', '_')
    output_file = output_dir / f"Identity___{wiki_name}.md"
    output_file.write_text(content, encoding='utf-8')
    print(f"[Identity Report SkipTrace] Generated: {output_file}")


if __name__ == "__main__":
    main()
