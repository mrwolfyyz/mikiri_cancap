"""
Domain Enrichment Cloud Function

Performs WHOIS and MX record lookups for email domains.
Called from workflow as part of phase2 parallel execution.

Returns enrichment data that gets passed to aggregator.
"""

import functions_framework
import os
import json
import sys
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import retry utilities (local copy for consistency with other phase2 functions)
from retry_utils import retry_with_backoff, RetryConfig

# -------------------------
# Config
# -------------------------

# Import enrichment functions from report generator
# We'll copy the necessary functions here
def extract_email_domain(email: str) -> str:
    """Extract domain from email address."""
    try:
        if "@" in email:
            return email.split("@")[1].lower().strip()
        return ""
    except Exception:
        return ""


COMMON_CANADIAN_EMAIL_DOMAINS = [
    "gmail.com", "hotmail.com", "outlook.com", "live.com", "yahoo.com",
    "icloud.com", "bell.net", "sympatico.ca", "rogers.com", "rogers.ca",
    "shaw.ca", "telus.net", "videotron.ca", "mts.net", "eastlink.ca",
    "nb.sympatico.ca", "ns.sympatico.ca", "qc.sympatico.ca", "on.sympatico.ca",
    "primus.ca", "ciaccess.com", "execulink.com", "persona.ca", "nbnet.nb.ca",
    "hotmail.ca", "live.ca", "videotron.qc.ca", "me.com", "mac.com",
    "proton.me", "protonmail.com", "tutanota.com", "pm.me",
]


def is_personal_email_domain(domain: str) -> bool:
    """Check if domain is in the personal email domains list."""
    if not domain:
        return False
    return domain.lower().strip() in COMMON_CANADIAN_EMAIL_DOMAINS


def get_domain_registration_date(domain: str) -> Dict[str, Any]:
    """
    Perform whois lookup and extract registration date.
    Returns dict with 'success', 'registration_date', and 'error' fields.
    """
    try:
        import whois
        from datetime import datetime
        
        # Perform whois lookup with timeout
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
            import re
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
        from datetime import datetime
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


def check_domain_mx_records(domain: str) -> Dict[str, Any]:
    """
    Analyze a domain's MX records to determine if it uses legitimate 
    business email infrastructure or default/parked services.
    Returns dict with 'success', 'status', 'provider_detected', 'mx_records', 'risk_level', and 'error' fields.
    """
    # High Trust: Enterprise-grade services
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

    # Standard Trust: Legitimate paid/pro email services
    STANDARD_TRUST_PROVIDERS = {
        "zoho.com": "Zoho Mail (Standard Business)",
        "protonmail": "ProtonMail (Privacy/Standard)",
        "fastmail.com": "Fastmail (Standard)",
        "rackspace.com": "Rackspace Email (Standard)",
        "intermedia.net": "Intermedia (Standard)",
        "hostedemail.com": "OpenSRS/Tucows (Reseller Email - Likely Legit Small Biz)",
        "dreamhost.com": "DreamHost Email (Hosting Provider - Likely Legit Small Biz)"
    }

    # Low Trust / Risk Flags: Default registrar pages, parking services, or forwarding
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


def enrich_single_domain(domain: str) -> Dict[str, Any]:
    """Enrich a single domain with WHOIS and MX record lookups."""
    result = {
        'domain': domain,
        'whois': None,
        'mx': None,
        'error': None
    }
    
    # WHOIS lookup with retry
    # Note: retry_utils is designed for requests, but we can wrap urllib exceptions
    try:
        whois_result = retry_with_backoff(
            lambda: get_domain_registration_date(domain),
            RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=10.0),
            operation_name=f"WHOIS lookup: {domain}"
        )
        result['whois'] = whois_result
    except Exception as e:
        result['error'] = f"WHOIS lookup failed: {str(e)}"
    
    # MX lookup with retry
    try:
        mx_result = retry_with_backoff(
            lambda: check_domain_mx_records(domain),
            RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=10.0),
            operation_name=f"MX lookup: {domain}"
        )
        result['mx'] = mx_result
    except Exception as e:
        if result['error']:
            result['error'] += f"; MX lookup failed: {str(e)}"
        else:
            result['error'] = f"MX lookup failed: {str(e)}"
    
    return result


@functions_framework.http
def main(request):
    """
    HTTP Cloud Function entry point.
    
    Expects JSON body:
    {
        "email": "user@example.com",
        "company_domain": "example.com"  // optional, from company_domain_lookup
    }
    
    Returns enrichment data dict (consistent with other phase2 functions).
    """
    # Parse request
    try:
        req_data = request.get_json(silent=True) or {}
    except Exception:
        return {"error": "Invalid JSON"}, 400
    
    email = req_data.get('email', '').strip()
    company_domain = req_data.get('company_domain', '').strip()
    
    if not email:
        return {"error": "email is required"}, 400
    
    print(f"[DomainEnrichment] Starting for email: {email}")
    if company_domain:
        print(f"[DomainEnrichment] Company domain: {company_domain}")
    
    # Extract domains to enrich
    domains_to_enrich = []
    domain = extract_email_domain(email)
    if domain and not is_personal_email_domain(domain):
        domains_to_enrich.append(domain)
        print(f"[DomainEnrichment] Will enrich borrower email domain: {domain}")
    
    if company_domain:
        domains_to_enrich.append(company_domain)
        print(f"[DomainEnrichment] Will enrich company domain: {company_domain}")
    
    if not domains_to_enrich:
        print(f"[DomainEnrichment] No domains to enrich (all personal email domains)")
        return {
            'domains': {},
        }, 200
    
    enrichment_results = {}
    
    # Parallel processing for multiple domains
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {}
        for domain in domains_to_enrich:
            future = executor.submit(enrich_single_domain, domain)
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
    
    print(f"[DomainEnrichment] Complete - enriched {len(domains_to_enrich)} domain(s)")
    
    # Return data (like other phase2 functions), not write to Firestore
    return {
        'domains': enrichment_results,
    }, 200



























