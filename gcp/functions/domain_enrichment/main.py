"""
Domain Enrichment Cloud Function

Performs WHOIS and MX record lookups for email domains.
Called from workflow as part of phase2 parallel execution.

Returns enrichment data that gets passed to aggregator.
"""

import logging
import re
import time

import dns.resolver
import functions_framework
import whois
from dateutil import parser as dateutil_parser
from typing import Dict, Any, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# -------------------------
# Constants
# -------------------------

# Import shared domain utilities (copied by prepare-functions.sh from gcp/shared/)
from domain_utils import COMMON_CANADIAN_EMAIL_DOMAINS, extract_email_domain, is_personal_email_domain

# MX Record Provider Classifications
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

# Transient error patterns for result-based retry
_TRANSIENT_ERROR_PATTERNS = [
    "timeout", "timed out", "connection reset", "connection refused",
    "too many requests", "rate limit", "temporarily unavailable",
    "try again", "socket error", "network unreachable",
]

# Retry configuration
_MAX_RETRY_ATTEMPTS = 2
_RETRY_BASE_DELAY = 0.5
_FUNCTION_TIME_BUDGET_SECONDS = 45  # Leave headroom within 60s function timeout


# -------------------------
# Utility Functions
# -------------------------

def _is_transient_error(error_msg: str) -> bool:
    """Check if an error message indicates a transient/retryable failure."""
    if not error_msg:
        return False
    error_lower = error_msg.lower()
    return any(pattern in error_lower for pattern in _TRANSIENT_ERROR_PATTERNS)


# -------------------------
# WHOIS Lookup
# -------------------------

def get_domain_registration_date(domain: str) -> Dict[str, Any]:
    """
    Perform whois lookup and extract registration date.
    Returns dict with 'success', 'registration_date', and 'error' fields.
    """
    try:
        # Perform whois lookup
        w = whois.whois(domain)

        # Try multiple field names for registration date
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

        # For domains that don't parse dates, try parsing raw text
        if not creation_date and hasattr(w, 'text') and w.text:
            date_patterns = [
                r'creation date[:\s]+(\d{4}-\d{2}-\d{2})',
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
                for fmt in ['%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%d-%b-%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ']:
                    try:
                        creation_date = datetime.strptime(creation_date, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    try:
                        creation_date = dateutil_parser.parse(creation_date)
                    except Exception:
                        pass

            # Format as YYYY-MM-DD
            if isinstance(creation_date, datetime):
                reg_date_str = creation_date.strftime("%Y-%m-%d")
                logger.info("WHOIS lookup successful: %s registered %s", domain, reg_date_str)
                return {
                    "success": True,
                    "registration_date": reg_date_str,
                    "error": None
                }

        logger.warning("No registration date found for %s", domain)
        return {
            "success": False,
            "registration_date": None,
            "error": "No registration date in whois data"
        }

    except Exception as e:
        error_msg = str(e)
        logger.warning("WHOIS lookup failed for %s: %s: %s", domain, e.__class__.__name__, error_msg)

        # Try to parse creation date from error message (some whois libraries return data in error)
        date_patterns = [
            r'creation date[:\s]+(\d{4}-\d{2}-\d{2})T?\d*:?\d*:?\d*Z?',
            r'creation date[:\s]+(\d{4}-\d{2}-\d{2})',
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
                    logger.info("Extracted registration date from error message: %s registered %s", domain, reg_date_str)
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


# -------------------------
# MX Record Lookup
# -------------------------

def check_domain_mx_records(domain: str) -> Dict[str, Any]:
    """
    Analyze a domain's MX records to determine if it uses legitimate
    business email infrastructure or default/parked services.
    Returns dict with 'success', 'status', 'provider_detected', 'mx_records', 'risk_level', and 'error' fields.
    """
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
                logger.info("MX lookup: %s uses %s", domain, name)
                return results

        # Check Standard Trust
        for sig, name in STANDARD_TRUST_PROVIDERS.items():
            if sig in primary_mx:
                results["success"] = True
                results["status"] = "Standard Business Email"
                results["provider_detected"] = name
                results["risk_level"] = "LOW/MEDIUM"
                logger.info("MX lookup: %s uses %s", domain, name)
                return results

        # Check Low Trust / Parking
        for sig, name in LOW_TRUST_FLAGS.items():
            if sig in primary_mx:
                results["success"] = True
                results["status"] = "Registrar Default / Parked"
                results["provider_detected"] = name
                results["risk_level"] = "HIGH"
                logger.warning("MX lookup: %s uses %s", domain, name)
                return results

        # If it points to the domain itself (e.g., mail.custom-domain.com)
        if domain in primary_mx:
            results["success"] = True
            results["status"] = "Self-Hosted / Local Hosting"
            results["provider_detected"] = "Private Server (e.g., cPanel/Exchange)"
            results["risk_level"] = "MEDIUM"
            logger.info("MX lookup: %s uses self-hosted email", domain)
            return results

        # Unknown/Unrecognized MX record
        results["success"] = True
        results["status"] = "Unknown Email Provider"
        results["provider_detected"] = f"Unrecognized provider: {primary_mx}"
        results["risk_level"] = "MEDIUM"
        logger.warning("MX lookup: %s uses unrecognized provider: %s", domain, primary_mx)
        return results

    except dns.resolver.NoAnswer:
        results["status"] = "No Email Configured"
        results["risk_level"] = "CRITICAL"
        results["error"] = "Domain has no MX records"
        logger.warning("MX lookup: %s has no MX records", domain)
        return results
    except dns.resolver.NXDOMAIN:
        results["status"] = "Domain Not Found"
        results["risk_level"] = "CRITICAL"
        results["error"] = "Domain does not exist"
        logger.warning("MX lookup failed: %s does not exist", domain)
        return results
    except Exception as e:
        logger.warning("MX lookup failed for %s: %s: %s", domain, e.__class__.__name__, str(e))
        results["error"] = str(e)
        return results


# -------------------------
# Domain Enrichment
# -------------------------

def _retry_lookup(lookup_fn, domain: str, lookup_name: str, start_time: float) -> Dict[str, Any]:
    """
    Result-based retry for WHOIS/MX lookups.

    Retries when result['success'] is False and the error appears transient,
    up to _MAX_RETRY_ATTEMPTS times with exponential backoff. Respects the
    overall function time budget.
    """
    last_result = None

    for attempt in range(_MAX_RETRY_ATTEMPTS + 1):  # +1 for initial attempt
        # Check time budget before retries (not before the first attempt)
        if attempt > 0:
            elapsed = time.time() - start_time
            if elapsed > _FUNCTION_TIME_BUDGET_SECONDS:
                logger.warning("%s for %s: time budget exceeded (%.1fs), returning last result",
                               lookup_name, domain, elapsed)
                break

            delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.info("%s for %s: attempt %d/%d failed with transient error, retrying in %.1fs",
                        lookup_name, domain, attempt, _MAX_RETRY_ATTEMPTS + 1, delay)
            time.sleep(delay)

        result = lookup_fn(domain)
        last_result = result

        # Success or definitive failure - don't retry
        if result.get("success"):
            return result

        error_msg = result.get("error", "")
        if not _is_transient_error(error_msg):
            return result

    # All retries exhausted
    logger.warning("%s for %s: all %d attempts exhausted",
                   lookup_name, domain, _MAX_RETRY_ATTEMPTS + 1)
    return last_result


def enrich_single_domain(domain: str, start_time: float) -> Dict[str, Any]:
    """
    Enrich a single domain with WHOIS and MX record lookups in parallel.

    Uses result-based retry: if a lookup returns success=False with a
    transient error, it will be retried up to _MAX_RETRY_ATTEMPTS times.
    """
    result = {
        'domain': domain,
        'whois': None,
        'mx': None,
        'error': None
    }

    # Run WHOIS and MX lookups in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        whois_future = executor.submit(_retry_lookup, get_domain_registration_date, domain, "WHOIS", start_time)
        mx_future = executor.submit(_retry_lookup, check_domain_mx_records, domain, "MX", start_time)

        try:
            result['whois'] = whois_future.result()
        except Exception as e:
            result['error'] = f"WHOIS lookup failed: {str(e)}"

        try:
            result['mx'] = mx_future.result()
        except Exception as e:
            error_msg = f"MX lookup failed: {str(e)}"
            if result['error']:
                result['error'] += f"; {error_msg}"
            else:
                result['error'] = error_msg

    return result


@functions_framework.http
def main(request) -> Tuple[dict, int]:
    """
    HTTP Cloud Function entry point.

    Expects JSON body:
    {
        "email": "user@example.com",
        "company_domain": "example.com"  // optional, from company_domain_lookup
    }

    Returns enrichment data dict (consistent with other phase2 functions).
    """
    start_time = time.time()

    # Parse request
    try:
        req_data = request.get_json(silent=True) or {}
    except Exception:
        return {"error": "Invalid JSON"}, 400

    email = req_data.get('email', '').strip()
    company_domain = req_data.get('company_domain', '').strip()

    if not email:
        return {"error": "email is required"}, 400

    logger.info("Starting domain enrichment for email: %s", email)
    if company_domain:
        logger.info("Company domain provided: %s", company_domain)

    # Extract domains to enrich (deduplicated)
    domains_to_enrich = []
    seen = set()

    email_domain = extract_email_domain(email)
    if email_domain and not is_personal_email_domain(email_domain):
        domains_to_enrich.append(email_domain)
        seen.add(email_domain.lower())
        logger.info("Will enrich borrower email domain: %s", email_domain)

    if company_domain and company_domain.lower() not in seen:
        domains_to_enrich.append(company_domain)
        seen.add(company_domain.lower())
        logger.info("Will enrich company domain: %s", company_domain)
    elif company_domain:
        logger.info("Company domain %s same as email domain, skipping duplicate", company_domain)

    if not domains_to_enrich:
        logger.info("No domains to enrich (all personal email domains)")
        return {'domains': {}}, 200

    enrichment_results = {}

    # Parallel processing for multiple domains
    with ThreadPoolExecutor(max_workers=len(domains_to_enrich)) as executor:
        futures = {}
        for domain in domains_to_enrich:
            future = executor.submit(enrich_single_domain, domain, start_time)
            futures[future] = domain

        for future in as_completed(futures):
            domain = futures[future]
            try:
                enrichment_results[domain] = future.result()
            except Exception as e:
                enrichment_results[domain] = {
                    'domain': domain,
                    'whois': None,
                    'mx': None,
                    'error': f"Enrichment failed: {str(e)}"
                }

    elapsed = time.time() - start_time
    logger.info("Domain enrichment complete - enriched %d domain(s) in %.1fs", len(domains_to_enrich), elapsed)

    return {'domains': enrichment_results}, 200
