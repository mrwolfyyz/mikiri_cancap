#!/usr/bin/env python3
"""
Generate Markdown reports from borrower investigation JSON data.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pyap

# Shared utilities (copied by prepare-functions.sh from gcp/shared/)
from address_utils import clean_address_for_geocoding
from domain_utils import extract_email_domain, is_personal_email_domain
from report_utils import (
    check_domain_mx_records,
    generate_google_search_url,
    generate_google_search_url_for_email,
    generate_google_search_url_for_phone,
    generate_phone_variations,
    generate_street_view_url,
    get_domain_registration_date,
    get_gravatar_profile,
    is_disposable_email_domain,
    load_disposable_email_blocklist,
    normalize_address,
    slugify,
)


def format_name(name: str) -> str:
    """Format name to title case for display."""
    return name.title()


def get_mx_callout(mx_result: dict[str, Any]) -> str:
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


def extract_linkedin_connections(snippet: str) -> int | None:
    """
    Extract LinkedIn connection count from snippet text.
    Handles patterns like "500+ connections", "1K+ connections", "connected to 500", etc.
    Returns integer count or None if not found.
    """
    if not snippet:
        return None

    import re

    # Pattern 1: "500+ connections" or "10 connections" or "500+ connection"
    pattern1 = r"(\d+)\s*\+?\s*connections?"
    match1 = re.search(pattern1, snippet, re.IGNORECASE)
    if match1:
        count = int(match1.group(1))
        return count

    # Pattern 2: "1K+ connections" or "5K connections"
    pattern2 = r"(\d+)K\s*\+?\s*connections?"
    match2 = re.search(pattern2, snippet, re.IGNORECASE)
    if match2:
        count = int(match2.group(1)) * 1000
        return count

    # Pattern 3: "connected to 500" or "connected with 500"
    pattern3 = r"connected\s+(?:to|with)\s+(\d+)"
    match3 = re.search(pattern3, snippet, re.IGNORECASE)
    if match3:
        count = int(match3.group(1))
        return count

    # Pattern 4: "500 connections" (without +)
    pattern4 = r"(\d+)\s+connections?"
    match4 = re.search(pattern4, snippet, re.IGNORECASE)
    if match4:
        count = int(match4.group(1))
        return count

    return None


def get_all_linkedin_snippets(top_handles: list[dict[str, Any]], queries: list[dict[str, Any]]) -> list[str]:
    """
    Get snippets for all LinkedIn profiles from top_handles and queries.
    Returns list of snippet strings (may be empty if no LinkedIn profiles found).
    """
    if not top_handles:
        return []

    # Find all LinkedIn profiles in top_handles
    linkedin_handles = []
    for handle in top_handles:
        platform = handle.get("platform", "").lower()
        url = handle.get("url", "").lower()
        if platform == "linkedin" or "linkedin.com" in url:
            linkedin_handles.append(handle)

    if not linkedin_handles:
        return []

    # Extract snippets for all LinkedIn profiles
    snippets = []
    for linkedin_handle in linkedin_handles:
        url = linkedin_handle.get("url", "")
        snippet = None
        for query in queries or []:
            for hit in query.get("hits", []):
                hit_url = hit.get("url", "").lower()
                if url.lower() == hit_url or (hit_url and hit_url.rstrip("/") == url.lower().rstrip("/")):
                    snippet = hit.get("snippet", "")
                    break
            if snippet:
                break
        if snippet:
            snippets.append(snippet)

    return snippets


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


def extract_1st_addresses_fallback(text: str) -> list[str]:
    """
    Fallback regex to extract US addresses with '1st' or 'First' that pyap cannot parse.
    Returns list of address strings.
    """
    # Pattern for US addresses with 1st/First in street name
    # Matches: street_number + (1st|First) + street_type + optional_direction + city + state + zip
    pattern = re.compile(
        r"\b(\d{1,6})\s+(1st|First)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)\s+((?:NW|NE|SW|SE|North|South|East|West|N|S|E|W)\s*)?,\s*([A-Za-z\s]+?),\s*([A-Z]{2})\s*,\s*(\d{5}(?:-\d{4})?)\b",
        re.IGNORECASE,
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


def extract_addresses_from_queries(queries: list[dict[str, Any]]) -> list[dict[str, str]]:
    """
    Scan query hits for address-like patterns using pyap.
    Falls back to regex for addresses with '1st' that pyap cannot parse.
    """
    results = []
    seen = set()

    for q in queries or []:
        for hit in q.get("hits", []):
            text = f"{hit.get('title', '')} {hit.get('snippet', '')}"
            source = hit.get("url", "")
            snippet = hit.get("snippet", "").strip()

            # Try US addresses
            addresses = pyap.parse(text, country="US")
            # Add Canadian addresses
            addresses.extend(pyap.parse(text, country="CA"))

            # Fallback: if pyap found nothing, check for "1st" addresses
            if len(addresses) == 0 and ("1st" in text or "First" in text):
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
                    has_street_number = bool(re.search(r"^\d{1,6}\s+[A-Za-z]", addr_cleaned))
                    # Check for common street name patterns (Avenue, Street, Road, etc. preceded by text)
                    has_street_name = bool(
                        re.search(
                            r"\b([A-Za-z0-9.\-\s]+?(?:Avenue|Street|Road|Lane|Drive|Boulevard|Way|Court|Place|Crescent|Circle|Terrace|Parkway|Highway|Ave|St|Rd|Dr|Blvd|Ln|Way|Ct|Pl|Cres|Cir|Terr|Pkwy|Hwy))\b",
                            addr_cleaned,
                            re.IGNORECASE,
                        )
                    )

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
                    street_number = getattr(addr_obj, "street_number", None)
                    street_name = getattr(addr_obj, "street_name", None)
                    city = getattr(addr_obj, "city", None)

                    # Canadian addresses have province and postal_code
                    province = getattr(addr_obj, "province", None)
                    postal_code = getattr(addr_obj, "postal_code", None)

                    # US addresses have state and zip_code
                    state = getattr(addr_obj, "state", None)
                    zip_code = getattr(addr_obj, "zip_code", None)

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


def generate_identity_report(
    data: dict[str, Any],
    name: str,
    output_dir: Path,
    company_domain: str = None,
    enrichment_data: dict[str, Any] = None,
    simplified: bool = False,
) -> None:
    """Generate the Identity markdown file.

    Args:
        data: Investigation data
        name: Borrower name
        output_dir: Output directory for markdown file
        company_domain: Optional company domain from company_domain_lookup
        enrichment_data: Optional pre-fetched enrichment data with 'domains' and 'addresses' keys
        simplified: If True, excludes Contactability and Public Sector Employment sections, and removes navigation bar
    """

    report_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    # Identity report generation
    scored = data["scored"]
    seed = data["seed"]

    # Build snapshot section
    email = seed["email"]
    location = scored.get("location", {}).get("city", "Unknown")
    location_confidence = scored.get("location", {}).get("confidence", "unknown")

    # Extract domain and perform whois lookup if not personal email
    domain = extract_email_domain(email)
    whois_result = None
    mx_result = None

    # Use pre-fetched enrichment data if available
    if enrichment_data and enrichment_data.get("domains"):
        domain_enrichment = enrichment_data["domains"].get(domain, {})
        if domain_enrichment:
            whois_result = domain_enrichment.get("whois")
            mx_result = domain_enrichment.get("mx")
            print(f"[Identity Report] Using pre-fetched enrichment data for domain: {domain}")

    # Fallback to inline lookups if enrichment data not available (parallelized)
    if domain and not is_personal_email_domain(domain):
        need_whois = not whois_result
        need_mx = not mx_result
        if need_whois or need_mx:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {}
                if need_whois:
                    print(
                        f"[Identity Report] WARNING: Falling back to inline whois lookup for {domain} (domain_enrichment data missing)"
                    )
                    futures[executor.submit(get_domain_registration_date, domain)] = "whois"
                if need_mx:
                    print(
                        f"[Identity Report] WARNING: Falling back to inline MX lookup for {domain} (domain_enrichment data missing)"
                    )
                    futures[executor.submit(check_domain_mx_records, domain)] = "mx"
                for future in as_completed(futures):
                    lookup_type = futures[future]
                    try:
                        result = future.result()
                        if lookup_type == "whois":
                            whois_result = result
                        else:
                            mx_result = result
                    except Exception as e:
                        print(f"[Identity Report] WARNING: Inline {lookup_type} lookup failed: {e}")

    # Perform company domain checks if provided
    print(
        f"[Identity Report] DEBUG: generate_identity_report called with company_domain={repr(company_domain)} (type: {type(company_domain)})"
    )
    company_whois_result = None
    company_mx_result = None
    if company_domain:
        print("[Identity Report] DEBUG: company_domain is truthy, stripping...")
        company_domain = company_domain.strip()
        print(f"[Identity Report] DEBUG: After strip: {repr(company_domain)}")
        if company_domain:
            # Use pre-fetched enrichment data if available
            if enrichment_data and enrichment_data.get("domains") and company_domain in enrichment_data["domains"]:
                company_enrichment = enrichment_data["domains"][company_domain]
                company_whois_result = company_enrichment.get("whois")
                company_mx_result = company_enrichment.get("mx")
                print(f"[Identity Report] Using pre-fetched enrichment data for company domain: {company_domain}")
            else:
                # Parallelize company domain WHOIS and MX lookups
                from concurrent.futures import ThreadPoolExecutor, as_completed

                print(
                    f"[Identity Report] WARNING: Falling back to inline lookups for company domain {company_domain} (domain_enrichment data missing)"
                )
                with ThreadPoolExecutor(max_workers=2) as executor:
                    whois_future = executor.submit(get_domain_registration_date, company_domain)
                    mx_future = executor.submit(check_domain_mx_records, company_domain)
                    try:
                        company_whois_result = whois_future.result()
                    except Exception as e:
                        print(f"[Identity Report] WARNING: Company domain whois lookup failed: {e}")
                    try:
                        company_mx_result = mx_future.result()
                    except Exception as e:
                        print(f"[Identity Report] WARNING: Company domain MX lookup failed: {e}")
        else:
            print("[Identity Report] DEBUG: company_domain is empty after strip")
    else:
        print("[Identity Report] DEBUG: company_domain is falsy, skipping lookups")

    # Check Gravatar profile if personal email
    gravatar_result = None
    if domain and is_personal_email_domain(domain):
        print(f"[Identity Report] Checking Gravatar profile for personal email: {email}")
        gravatar_result = get_gravatar_profile(email)

    # Load disposable email blocklist and check if email is disposable
    blocklist_path = Path(__file__).parent / "disposable_email_blocklist.conf"
    disposable_blocklist = load_disposable_email_blocklist(blocklist_path)
    is_disposable = is_disposable_email_domain(email, disposable_blocklist)

    contactability = data.get("contactability", {})
    score = contactability.get("score", "unknown")
    reason = contactability.get("reason", "No information available")
    num_social = contactability.get("num_social", 0)
    num_breaches = contactability.get("num_breaches", 0)
    footprint_bucket = contactability.get("footprint_bucket", "unknown")
    breach_bucket = contactability.get("breach_bucket", "unknown")

    breaches = data.get("breaches", [])
    top_handles = scored.get("top_handles", [])
    queries = data.get("queries", [])

    # Check LinkedIn profile for connection count early (for alerts collection)
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

    # Collect all alerts/warnings to display at top of report
    alerts = []  # List of (severity, message) tuples where severity is "danger" or "warning"

    # No breach history alert
    if num_breaches == 0 or (not breaches or len(breaches) == 0):
        alerts.append(
            (
                "warning",
                "No Breach History Detected – Possible Identity Risk",
                "This email address has no known public breach exposure, which is sometimes seen with newly created or application-specific emails used in first-party fraud or synthetic identities.\n\n"
                "Operational impact: Reduces confidence in long-term email usage and post-funding reachability, particularly if claimed employment or business tenure is longer.\n\n"
                "Suggested action: Validate against claimed tenure and corroborate with alternate contact and identity signals.",
            )
        )

    # Disposable email alert
    if is_disposable:
        alerts.append(
            (
                "danger",
                "Disposable Email Detected – High Identity Risk",
                "This email address uses a known disposable email domain, which is designed for temporary or anonymous use and is commonly associated with first-party fraud and synthetic identities.\n\n"
                "Operational impact: Significantly reduces confidence in identity stability and post-funding contactability.\n\n"
                "Suggested action: Require a non-disposable email and corroborate identity using alternate contact and verification signals.",
            )
        )

    # LinkedIn connections alert
    if linkedin_alert_level and linkedin_connections is not None:
        alerts.append(
            (
                linkedin_alert_level,
                "Very Low LinkedIn Connectivity – Identity Credibility Risk",
                f"This LinkedIn profile shows {linkedin_connections} connections, which is unusually low for someone claiming established employment or business activity and is sometimes seen with newly created or minimally used profiles (including those tied to first-party fraud or synthetic identities).\n\n"
                "Operational impact: Reduces confidence in the claimed professional history and employment stability.\n\n"
                "Suggested action: Verify employment using independent sources and corroborate with non-social identity and contact signals.",
            )
        )

    # Domain registration age alert (for email domain)
    if whois_result and whois_result.get("success") and whois_result.get("registration_date"):
        reg_date = whois_result["registration_date"]
        domain_callout = get_domain_age_callout(reg_date)
        if domain_callout in ["danger", "warning"]:
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
                alerts.append(
                    (
                        "danger",
                        "Recently Registered Domain – Business Tenure Mismatch Risk",
                        f"This domain was registered less than 90 days ago (domain age: {age_text}), which may conflict with claims of long-term business operations.\n\n"
                        "Operational impact: Reduces confidence in employer/business tenure and increases the likelihood the business is newly formed or misrepresented.\n\n"
                        "Suggested action: Validate business tenure via corporate registry filings, operating history, and independent business presence checks.",
                    )
                )
            elif domain_callout == "warning":
                alerts.append(
                    (
                        "warning",
                        "Recently Registered Domain – Business Tenure Mismatch Risk",
                        f"This domain was registered less than 1 year ago (domain age: {age_text}), which may conflict with claims of long-term business operations.\n\n"
                        "Operational impact: Reduces confidence in employer/business tenure and increases the likelihood the business is newly formed or misrepresented.\n\n"
                        "Suggested action: Validate business tenure via corporate registry filings, operating history, and independent business presence checks.",
                    )
                )

    # MX record alerts (for email domain)
    if domain and not is_personal_email_domain(domain) and mx_result:
        mx_callout = get_mx_callout(mx_result)
        if mx_callout in ["danger", "warning"]:
            if mx_result.get("success"):
                risk_level = mx_result.get("risk_level", "UNKNOWN")
                if risk_level == "HIGH":
                    alerts.append(
                        (
                            mx_callout,
                            "Default Registrar Email Services – Business Email Not Deliverable",
                            "This domain uses default registrar email services (forwarding/parking only), meaning it cannot reliably receive email and the business email may be inactive or misrepresented.\n\n"
                            "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                            "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.",
                        )
                    )
                elif risk_level == "MEDIUM":
                    alerts.append(
                        (
                            mx_callout,
                            "Self-Hosted Email Infrastructure – Business Email Verification Needed",
                            "This domain uses self-hosted or less common email infrastructure, which may reduce confidence in the reliability and legitimacy of the business email.\n\n"
                            "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                            "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.",
                        )
                    )
            else:
                status = mx_result.get("status", "Lookup Failed")
                risk_level = mx_result.get("risk_level", "UNKNOWN")
                if risk_level == "CRITICAL" and status == "No Email Configured":
                    alerts.append(
                        (
                            mx_callout,
                            "No MX Records – Business Email Not Deliverable",
                            "This domain has no MX records configured, meaning it cannot receive email and the business email may be inactive or misrepresented.\n\n"
                            "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                            "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.",
                        )
                    )
                elif risk_level == "CRITICAL" and status == "Domain Not Found":
                    alerts.append(
                        (
                            mx_callout,
                            "Domain Not Found – Business Email Invalid",
                            "This domain does not exist (NXDOMAIN). The business email address is invalid or fraudulent.\n\n"
                            "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                            "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.",
                        )
                    )
                elif mx_callout in ["danger", "warning"]:
                    alerts.append(
                        (
                            mx_callout,
                            "Email Infrastructure Verification Failed",
                            "Unable to verify email infrastructure. All legitimate business emails must be able to receive email. This inability to verify is a strong indicator that the business email address may be invalid, inactive, or fraudulent.\n\n"
                            "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                            "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.",
                        )
                    )

    # Company domain registration age alert
    if company_whois_result and company_whois_result.get("success") and company_whois_result.get("registration_date"):
        reg_date = company_whois_result["registration_date"]
        domain_callout = get_domain_age_callout(reg_date)
        if domain_callout in ["danger", "warning"]:
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

            alerts.append(
                (
                    domain_callout,
                    "Recently Registered Company Domain – Business Tenure Mismatch Risk",
                    f"This company domain was registered less than {'90 days' if domain_callout == 'danger' else '1 year'} ago (domain age: {age_text}), which may conflict with claims of long-term business operations.\n\n"
                    "Operational impact: Reduces confidence in employer/business tenure and increases the likelihood the business is newly formed or misrepresented.\n\n"
                    "Suggested action: Validate business tenure via corporate registry filings, operating history, and independent business presence checks.",
                )
            )

    # Company MX record alerts
    if company_domain and company_mx_result:
        mx_callout = get_mx_callout(company_mx_result)
        if mx_callout in ["danger", "warning"]:
            if company_mx_result.get("success"):
                risk_level = company_mx_result.get("risk_level", "UNKNOWN")
                if risk_level == "HIGH":
                    alerts.append(
                        (
                            mx_callout,
                            "Default Registrar Email Services – Business Email Not Deliverable",
                            "This company domain uses default registrar email services (forwarding/parking only), meaning it cannot reliably receive email and the business email may be inactive or misrepresented.\n\n"
                            "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                            "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.",
                        )
                    )
                elif risk_level == "MEDIUM":
                    alerts.append(
                        (
                            mx_callout,
                            "Self-Hosted Email Infrastructure – Business Email Verification Needed",
                            "This company domain uses self-hosted or less common email infrastructure, which may reduce confidence in the reliability and legitimacy of the business email.\n\n"
                            "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                            "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.",
                        )
                    )
            else:
                status = company_mx_result.get("status", "Lookup Failed")
                risk_level = company_mx_result.get("risk_level", "UNKNOWN")
                if risk_level == "CRITICAL" and status == "No Email Configured":
                    alerts.append(
                        (
                            mx_callout,
                            "No MX Records – Business Email Not Deliverable",
                            "This company domain has no MX records configured, meaning it cannot receive email and the business email may be inactive or misrepresented.\n\n"
                            "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                            "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.",
                        )
                    )
                elif risk_level == "CRITICAL" and status == "Domain Not Found":
                    alerts.append(
                        (
                            mx_callout,
                            "Domain Not Found – Business Email Invalid",
                            "This company domain does not exist (NXDOMAIN). The business email address is invalid or fraudulent.\n\n"
                            "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                            "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.",
                        )
                    )
                elif mx_callout in ["danger", "warning"]:
                    alerts.append(
                        (
                            mx_callout,
                            "Email Infrastructure Verification Failed",
                            "Unable to verify company email infrastructure. All legitimate business emails must be able to receive email. This inability to verify is a strong indicator that the business email address may be invalid, inactive, or fraudulent.\n\n"
                            "Operational impact: Reduces confidence in the legitimacy and reachability of the employer/business contact channel.\n\n"
                            "Suggested action: Confirm a working business contact method (valid inbox/website/phone) and corroborate the employer via independent sources.",
                        )
                    )

    # Calculate earliest breach date
    earliest_breach_date = None
    if breaches:
        valid_dates = []
        for breach in breaches:
            date_str = breach.get("date")
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
        breach_name = breach.get("name")
        if breach_name:
            breach_slug = slugify(breach_name)
            if breach_slug != "unknown":
                tags.append(f"breach/{breach_slug}")

    # Social platform + handle tags
    platforms = set()
    for handle in top_handles or []:
        platform = handle.get("platform")
        handle_name = handle.get("handle")
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
    # Navigation bar removed - origination only generates Identity report
    content += "> [!info] Snapshot\n"
    content += f"> - **Name:** {name}  \n"
    content += f"> - **Email:** `{email}`  \n"
    content += f"> - **Location (scored):** {location} ({location_confidence} confidence)  \n"

    if primary_handle:
        content += f"> - **Primary handle:** {primary_handle['platform']} — `{primary_handle['handle']}` ({primary_handle.get('confidence', 'medium')} confidence)  \n"

    if secondary_handle:
        content += f"> - **Secondary handle:** {secondary_handle['platform']} — `{secondary_handle['handle']}` ({secondary_handle.get('confidence', 'medium')} confidence)\n"

    content += "\n\n"

    # Insert all alerts/warnings at the top (sorted by severity: danger first, then warning)
    if alerts:
        content += "\n---\n\n"
        # Sort alerts: danger first, then warning
        sorted_alerts = sorted(alerts, key=lambda x: (0 if x[0] == "danger" else 1, x[1]))
        for severity, title, message in sorted_alerts:
            content += f"> [!{severity}] {title}\n\n"
            content += f"{message}\n\n"
        content += "\n---\n\n"
    else:
        content += "\n---\n\n"

    content += "## Identity Confirmation\n\n"
    content += "> [!note] Rationale\n"
    content += f"> {scored.get('rationale', 'No rationale provided')}\n\n"

    # Grounding metadata section (below Rationale)
    grounding_metadata = data.get("grounding_metadata", {})
    search_queries = grounding_metadata.get("search_queries", [])

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
        platform = handle["platform"]
        handle_name = handle["handle"]
        url = handle["url"]
        confidence = handle.get("confidence", "medium")

        content += f"- **{platform}**  \n"
        if handle_name:
            whatsmyname_url = f"https://whatsmyname.app/?q={quote_plus(handle_name)}"
            content += f"  - Handle: [`{handle_name}`]({whatsmyname_url})  \n"
        content += f"  - Confidence: **{confidence}**  \n"
        content += f"  - URL: <{url}>  \n"

        # Try to find a snippet for this handle from the queries
        snippet = None
        for query in data.get("queries", []):
            for hit in query.get("hits", []):
                hit_url = hit.get("url", "").lower()
                if url.lower() == hit_url or (hit_url and hit_url.rstrip("/") == url.lower().rstrip("/")):
                    snippet = hit.get("snippet", "")
                    break
            if snippet:
                break

        if snippet:
            content += "  - Snippet:  \n"
            content += f"    > {snippet}\n"

        content += "\n"

    content += "---\n\n"

    # Data Breaches section
    content += "## Data Breaches\n\n"
    if breaches and len(breaches) > 0:
        # Sort breaches chronologically by date (oldest first)
        # Breaches without dates go to the end
        def sort_key(breach):
            date_str = breach.get("date")
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
            breach_name = breach.get("name", "Unknown")
            breach_date = breach.get("date", "")
            if breach_date:
                content += f"| {breach_name} | {breach_date} |\n"
            else:
                content += f"| {breach_name} | *(Unknown)* |\n"
    else:
        content += "*(None)*\n"

    content += f"Source: [Have I Been Pwned](https://haveibeenpwned.com) ·Timestamp: {report_timestamp}\n\n"

    content += "\n---\n\n"

    # Contact-ability section (skip in simplified mode - remove entire section)
    if not simplified:
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
                breach_name = breach.get("name", "").lower()
                if "gravatar" in breach_name:
                    has_gravatar_breach = True
                    gravatar_breach_date = breach.get("date", "")
                    break

        # Add Digital footprint hygiene field if conditions are met
        if has_gravatar_breach and gravatar_result and not gravatar_result.get("success"):
            if gravatar_breach_date:
                content += f"> - **Digital footprint hygiene:** High (User deleted Gravatar profile after breach of {gravatar_breach_date})  \n"
            else:
                content += "> - **Digital footprint hygiene:** High (User deleted Gravatar profile after breach)  \n"

        content += "\n\n"

        # Gravatar profile section (if available)
        if gravatar_result and gravatar_result.get("success"):
            content += "### Gravatar Profile\n\n"
            content += f"![Gravatar Avatar]({gravatar_result['thumbnail_url']})  \n"
            content += f"[View Full Profile →]({gravatar_result['profile_url']})  \n"
            content += "\n"

        content += "---\n\n"

    # ------------------------------
    # Domain Registration
    # ------------------------------
    if whois_result and whois_result.get("success") and whois_result.get("registration_date"):
        reg_date = whois_result["registration_date"]
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

        # Note: Alert messages for danger/warning domain age are now shown at top of report
        if domain_callout not in ["danger", "warning"]:
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

            # Note: Alert messages for HIGH/MEDIUM risk levels are now shown at top of report
            if risk_level == "LOW/MEDIUM":
                content += "> ⚠️ **Caution:** This domain uses standard business email services. Verify employment claims against this infrastructure.\n\n"
            elif risk_level == "LOW":
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

            # Note: Alert messages for CRITICAL risk levels and failures are now shown at top of report

        content += "---\n\n"

    # ------------------------------
    # Company Domain Registration
    # ------------------------------
    print("[Identity Report] DEBUG: Evaluating Company Domain Registration section")
    print(f"[Identity Report] DEBUG: company_whois_result={company_whois_result}")
    print(
        f"[Identity Report] DEBUG: company_whois_result.get('success')={company_whois_result.get('success') if company_whois_result else None}"
    )
    print(
        f"[Identity Report] DEBUG: company_whois_result.get('registration_date')={company_whois_result.get('registration_date') if company_whois_result else None}"
    )
    if company_whois_result and company_whois_result.get("success") and company_whois_result.get("registration_date"):
        print("[Identity Report] DEBUG: Company Domain Registration section WILL BE SHOWN")
        reg_date = company_whois_result["registration_date"]
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

        # Note: Alert messages for danger/warning company domain age are now shown at top of report
        if domain_callout not in ["danger", "warning"]:
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

            # Note: Alert messages for HIGH/MEDIUM risk levels are now shown at top of report
            if risk_level == "LOW/MEDIUM":
                content += "> ⚠️ **Caution:** This company domain uses standard business email services. Verify employment claims against this infrastructure.\n\n"
            elif risk_level == "LOW":
                content += "> ✓ **Legitimate:** This company domain uses established business email infrastructure, indicating a legitimate business operation.\n\n"
        else:
            # Show error/warning card if lookup failed
            error_msg = (
                company_mx_result.get("error", "Unknown error") if company_mx_result else "MX lookup not performed"
            )
            status = company_mx_result.get("status", "Lookup Failed") if company_mx_result else "Lookup Failed"
            risk_level = company_mx_result.get("risk_level", "UNKNOWN") if company_mx_result else "UNKNOWN"

            content += f"> **Status:** {status}  \n"
            content += f"> **Risk Level:** {risk_level}  \n"
            if error_msg:
                content += f"> **Error:** {error_msg}  \n"
            content += ">  \n"

            # Note: Alert messages for CRITICAL risk levels and failures are now shown at top of report

        content += "---\n\n"
    else:
        print(
            "[Identity Report] DEBUG: Company Email Infrastructure section WILL NOT BE SHOWN (company_domain is falsy)"
        )

    # ------------------------------
    # Possible Phone Number(s)
    # ------------------------------
    # Get contact info from enrichment data (extracted during Phase 2)
    contact_info = {}
    if enrichment_data and enrichment_data.get("contacts"):
        contact_info = enrichment_data.get("contacts", {})
    else:
        # Fallback to empty structure if enrichment data not available
        contact_info = {"phones": [], "emails": [], "addresses": []}

    phones = contact_info.get("phones", [])

    if phones:
        content += "## Phone Number(s) of interest\n\n"
        content += "> [!info]\n"
        content += "> Automatically detected phone numbers from open-web sources.\n\n"

        for i, p in enumerate(phones, start=1):
            google_url = generate_google_search_url_for_phone(p)
            content += f"{i}. **[{p['number_raw']}]({google_url})**  \n"
            content += f"   - **Confidence:** {p.get('confidence', 'medium')}  \n"
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
    emails = contact_info.get("emails", [])

    if emails:
        content += "## Email Address(es) of interest\n\n"
        content += "> [!info]\n"
        content += "> Automatically detected email addresses from open-web sources.\n\n"

        for i, e in enumerate(emails, start=1):
            google_url = generate_google_search_url_for_email(e["email"])
            content += f"{i}. **[{e['email']}]({google_url})**  \n"
            content += f"   - **Confidence:** {e.get('confidence', 'medium')}  \n"
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
    addresses = contact_info.get("addresses", [])

    if addresses:
        content += "## Address(es) of interest\n\n"
        content += "> [!info]\n"
        content += "> Automatically detected addresses from open-web sources.\n\n"

        # Get geocoding data from enrichment if available
        geocoding_data = {}
        if enrichment_data and enrichment_data.get("addresses"):
            geocoding_data = enrichment_data["addresses"]

        # Build normalized lookup for fuzzy address matching (contact_extraction and address_geocoding use separate LLM calls)
        geocoding_normalized = {normalize_address(k): v for k, v in geocoding_data.items()}

        for i, a in enumerate(addresses, start=1):
            raw_addr = a["address_raw"]
            cleaned_addr = clean_address_for_geocoding(raw_addr)
            print(f"  [{i}/{len(addresses)}] Raw: {raw_addr[:60]}...")
            if raw_addr != cleaned_addr:
                print(f"       Cleaned: {cleaned_addr[:60]}...")

            # Use cached coordinates if available (normalized matching)
            cached_coords = None
            normalized_key = normalize_address(raw_addr)
            if normalized_key in geocoding_normalized:
                geocode_result = geocoding_normalized[normalized_key]
                if geocode_result.get("lat") and geocode_result.get("lon"):
                    cached_coords = {"lat": geocode_result["lat"], "lon": geocode_result["lon"]}

            # Never geocode inline during report generation - only use cached coordinates or fall back to search URL
            street_view_url = generate_street_view_url(raw_addr, geocode=False, cached_coords=cached_coords)
            google_url = generate_google_search_url(a)
            content += f"{i}. **[{raw_addr}]({google_url})**  \n"
            content += f"   - [📍 View Property]({street_view_url})  \n"
            content += f"   - **Confidence:** {a.get('confidence', 'medium')}  \n"
            if a.get("source_url"):
                content += f"   - **Source:** {a['source_url']}  \n"
            if a.get("snippet"):
                content += "   - **Snippet:**  \n"
                content += f"     > {a['snippet']}\n"
            content += "\n"

        content += "---\n\n"

    # ------------------------------
    # Ontario Public Sector Employment (skip in simplified mode)
    # ------------------------------
    if not simplified:
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

    for query in data.get("queries", []):
        query_id = query.get("id", "")
        query_type = query.get("type", "").replace("_", " ").title()
        query_text = query.get("query", "")
        hits = query.get("hits", [])

        section_name = query_id.replace("_", " ").title() if query_id else query_type

        content += f"### {section_name}\n\n"

        # Determine source label from hits (check first hit's source field, or default to google_search)
        source = "google_search"
        if hits and len(hits) > 0:
            source = hits[0].get("source", "google_search")

        # Map source values to display labels
        if source == "vertex_ai_linkedin":
            source_label = "Vertex AI Search (LinkedIn)"
        elif source == "vertex_ai_precision":
            source_label = "Vertex AI Search (Social)"
        elif source == "vertex_ai_recall":
            source_label = "Vertex AI Search (Lifestyle)"
        elif source == "google_search":
            source_label = "Google"
        else:
            source_label = source.replace("_", " ").title()

        # Always generate a Google link; use site:linkedin.com for LinkedIn queries
        is_linkedin_query = "linkedin" in query_id
        if is_linkedin_query:
            source_url = f"https://www.google.com/search?q=site%3Alinkedin.com+{quote_plus(query_text)}"
        else:
            source_url = f"https://www.google.com/search?q={quote_plus(query_text)}"

        content += f"Source: {source_label} · Timestamp: {report_timestamp}\n\n"
        content += "**Query**\n\n"
        content += f"[`{query_text}`]({source_url})\n\n"
        content += "**Hits**\n\n"

        if hits:
            for i, hit in enumerate(hits, 1):
                title = hit.get("title", "Untitled")
                url = hit.get("url", "")
                snippet = hit.get("snippet", "")

                content += f"{i}. [{title}]({url})  \n"
                content += f"   - **URL:** {url}  \n"
                content += f"   > {snippet}\n\n"
        else:
            content += "*(None)*\n\n"

        content += "---\n\n"

    output_path = output_dir / f"Identity___{name.replace(' ', '_')}.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Generated: {output_path}")


# ------------------------------
# Address/URL Helper Functions
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
        r"\b(AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\s+[A-Z]\d[A-Z]\s?\d[A-Z]\d", addr, re.IGNORECASE
    )

    if province_postal:
        end_pos = province_postal.end()
        address_part = addr[:end_pos]

        # Find the rightmost civic number
        civic_matches = list(re.finditer(r"\b(\d{1,6})\s+([A-Za-z][\w\s.-]*?)\s+(?=[A-Z][a-z])", address_part))

        if civic_matches:
            last_match = civic_matches[-1]
            return addr[last_match.start() : end_pos].strip()

    # Try US state + ZIP pattern
    state_zip = re.search(r"\b([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\b", addr)

    if state_zip:
        end_pos = state_zip.end()
        address_part = addr[:end_pos]

        # Find the rightmost civic number before state
        civic_matches = list(re.finditer(r"\b(\d{1,6})\s+([A-Za-z][\w\s.-]*?)\s*,", address_part))

        if civic_matches:
            last_match = civic_matches[-1]
            return addr[last_match.start() : end_pos].strip()

    # Fallback to original behavior
    m = re.search(r"\b\d{1,6}\s+.*", addr)
    return m.group(0).strip() if m else addr.strip()


def extract_address_components(addr_data: dict[str, Any]) -> dict[str, str | None]:
    """
    Extract structured address components from address data dictionary.
    Returns dict with: street, city, province (or state), postal_code (or zip_code)
    """
    street_number = addr_data.get("street_number")
    street_name = addr_data.get("street_name")

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
        addr_raw = addr_data.get("address_raw", "")
        # Try to extract street (number + name) from raw address
        street_match = re.search(r"^(\d{1,6}\s+[A-Za-z0-9.\-\s]+?)(?:\s*,\s*|\s+)(?=[A-Z][a-z])", addr_raw)
        if street_match:
            street = street_match.group(1).strip()

    city = addr_data.get("city")
    province = addr_data.get("province")
    state = addr_data.get("state")
    postal_code = addr_data.get("postal_code")
    zip_code = addr_data.get("zip_code")

    # If structured components not available, try parsing from address_raw
    if not city or not (province or state) or not (postal_code or zip_code):
        addr_raw = addr_data.get("address_raw", "")

        # Try Canadian format
        ca_match = re.search(
            r"\b([A-Za-z.\- ]{2,40})\s+(AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\s+([A-Z]\d[A-Z]\s?\d[A-Z]\d)",
            addr_raw,
            re.IGNORECASE,
        )
        if ca_match:
            if not city:
                city = ca_match.group(1).strip()
            if not province:
                province = ca_match.group(2).upper()
            if not postal_code:
                postal_code = ca_match.group(3).upper().replace(" ", "")

        # Try US format if Canadian didn't match
        if not (province or state) or not (postal_code or zip_code):
            us_match = re.search(r"\b([A-Za-z.\- ]{2,40})\s*,\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", addr_raw)
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


def generate_canada411_url(addr_data: dict[str, Any]) -> str:
    """
    Generate Canada411 reverse address search URL.
    Format: https://mobile.canada411.ca/search/?stype=ad&st={street}&ci={city}&pv={province}&pc={postal_code}
    """
    components = extract_address_components(addr_data)

    street = components.get("street") or ""
    city = components.get("city") or ""
    province = components.get("province") or ""
    postal_code = components.get("postal_code") or ""

    # If no structured components, use address_raw as fallback
    if not street and not city:
        addr_raw = addr_data.get("address_raw", "")
        # Try to extract basic components from raw address
        parts = addr_raw.split(",")
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


def generate_google_doc_search_url(addr_data: dict[str, Any]) -> str:
    """
    Generate Google search URL with filetype operators for document search.
    """
    address = addr_data.get("address_raw", "")
    query = f"{address} filetype:pdf OR filetype:doc OR filetype:docx"
    return f"https://www.google.com/search?q={quote_plus(query)}"


def generate_google_doc_search_url_for_phone(phone_data: dict[str, str]) -> str:
    """
    Generate Google search URL with phone number variations and filetype restrictions.
    Format: (phone variations) (filetype:pdf OR filetype:xls OR filetype:xlsx OR filetype:csv)
    """
    variations = generate_phone_variations(phone_data)
    if not variations:
        # Fallback to raw number if variations can't be generated
        number_raw = phone_data.get("number_raw", "")
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
    if not email or "@" not in email:
        return ""
    try:
        return email.split("@")[0].strip()
    except Exception:
        return ""


def generate_google_doc_search_url_for_email(email: str) -> str:
    """
    Generate Google search URL with email and filetype restrictions.
    Format: email (filetype:pdf OR filetype:xls OR filetype:xlsx OR filetype:csv)
    """
    query = f"{email} (filetype:pdf OR filetype:xls OR filetype:xlsx OR filetype:csv)"
    return f"https://www.google.com/search?q={quote_plus(query)}"
