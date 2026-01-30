"""
Aggregator Cloud Function

Combines results from all phase2 functions and computes final summaries.
Handles partial failures gracefully - if a phase2 function fails, its result
is set to null and an error is recorded, but aggregation continues.
"""

import functions_framework
import json
from typing import Dict, Any, List, Optional


def sanitize_for_json(obj: Any) -> Any:
    """
    Recursively sanitize data structure to ensure all values are JSON-serializable.
    Converts bytes to strings and handles other non-serializable types.
    
    CRITICAL: This function must catch ALL byte-like objects, as Cloud Workflows'
    json.encode() will fail if it encounters ANY bytes in the data structure.
    """
    if obj is None:
        return None
    elif isinstance(obj, bytes):
        # Convert bytes to string - this is critical for Cloud Workflows compatibility
        try:
            return obj.decode('utf-8', errors='replace')
        except Exception:
            return str(obj)
    elif isinstance(obj, bytearray):
        # Convert bytearray to string
        try:
            return obj.decode('utf-8', errors='replace')
        except Exception:
            return str(obj)
    elif isinstance(obj, dict):
        # Recursively sanitize dict keys and values
        return {sanitize_for_json(k): sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        # Recursively sanitize list/tuple items
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, (str, int, float, bool)):
        # These are already JSON-serializable
        return obj
    elif hasattr(obj, '__dict__'):
        # Handle objects with __dict__ by converting to dict
        return sanitize_for_json(obj.__dict__)
    else:
        # Fallback: check if it's a byte-like object by trying to decode
        # This catches edge cases where isinstance() might not work
        try:
            if hasattr(obj, 'decode') and callable(getattr(obj, 'decode')):
                # It has a decode method, try to decode it
                return obj.decode('utf-8', errors='replace')
        except Exception:
            pass
        
        # Final fallback: convert to string
        try:
            return str(obj)
        except Exception:
            return None


def compute_result_summary(
    identity: Dict[str, Any],
    regulator: Optional[Dict[str, Any]],
    litigation: Optional[Dict[str, Any]],
    corporate: Optional[Dict[str, Any]],
    salaries: Optional[Dict[str, Any]],
    errors: Dict[str, str],
) -> Dict[str, Any]:
    """
    Compute high-level result summary from all phase2 results.
    
    Returns a summary dict with:
    - overall_status: "clear", "elevated", "high", or "partial_failure"
    - headline: High-level summary text
    - bullets: List of key findings
    """
    bullets: List[str] = []
    statuses: List[str] = []
    has_partial_failure = bool(errors)
    
    # Check identity status
    identity_location = identity.get("location", {})
    if isinstance(identity_location, dict) and identity_location.get("confidence") == "high":
        bullets.append("High-confidence identity and location verified")
    
    # Check regulator status
    if regulator:
        reg_summary = regulator.get("regulator_summary", {})
        reg_status = str(reg_summary.get("status", "clear"))
        if reg_status != "clear":
            statuses.append(reg_status)
            headline = reg_summary.get('headline', 'Records detected')
            bullets.append(f"Regulator: {str(headline) if headline else 'Records detected'}")
    elif errors.get("regulator"):
        bullets.append(f"Regulator: Error - {str(errors['regulator'])}")
    
    # Check litigation status
    if litigation:
        lit_summary = litigation.get("litigation_summary", {})
        lit_status = str(lit_summary.get("status", "clear"))
        if lit_status != "clear":
            statuses.append(lit_status)
            headline = lit_summary.get('headline', 'Records detected')
            bullets.append(f"Litigation: {str(headline) if headline else 'Records detected'}")
    elif errors.get("litigation"):
        bullets.append(f"Litigation: Error - {str(errors['litigation'])}")
    
    # Check corporate status
    if corporate:
        corp_summary = corporate.get("corporate_summary", {})
        corp_status = str(corp_summary.get("status", "clear"))
        if corp_status not in ("clear", "none"):
            statuses.append(corp_status)
            headline = corp_summary.get('headline', 'Records detected')
            bullets.append(f"Corporate: {str(headline) if headline else 'Records detected'}")
    elif errors.get("corporate"):
        bullets.append(f"Corporate: Error - {str(errors['corporate'])}")
    
    # Check salaries
    if salaries:
        num_matches = salaries.get("num_people_matched", 0)
        if num_matches > 0:
            bullets.append(f"Salaries: {num_matches} public sector salary match(es) found")
    elif errors.get("salaries"):
        bullets.append(f"Salaries: Error - {str(errors['salaries'])}")
    
    # Determine overall status
    if has_partial_failure:
        overall_status = "partial_failure"
    elif "high" in statuses:
        overall_status = "high"
    elif "elevated" in statuses:
        overall_status = "elevated"
    else:
        overall_status = "clear"
    
    # Build headline
    if has_partial_failure:
        headline = "Investigation completed with partial data (some sources unavailable)"
    elif overall_status == "high":
        headline = "Multiple risk signals detected - review required"
    elif overall_status == "elevated":
        headline = "Some risk signals detected - review recommended"
    else:
        headline = "Investigation complete - no significant risk signals detected"
    
    # Ensure all values are strings (not bytes) before returning
    sanitized_bullets = [str(b) for b in bullets[:5]]
    
    return {
        "overall_status": str(overall_status),
        "headline": str(headline),
        "bullets": sanitized_bullets,
        "partial_failure": bool(has_partial_failure),
    }


def aggregate(
    identity: Dict[str, Any],
    regulator: Optional[Dict[str, Any]] = None,
    litigation: Optional[Dict[str, Any]] = None,
    corporate: Optional[Dict[str, Any]] = None,
    salaries: Optional[Dict[str, Any]] = None,
    domain_enrichment: Optional[Dict[str, Any]] = None,
    address_geocoding: Optional[Dict[str, Any]] = None,
    contact_extraction: Optional[Dict[str, Any]] = None,
    errors: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Aggregate results from all phase2 functions into final result.
    
    Handles null results gracefully - if a phase2 function failed,
    its data structure is set to empty/default values.
    """
    print("[Aggregator] Sanitizing phase2 inputs...")
    identity = sanitize_for_json(identity) if identity else {}
    regulator = sanitize_for_json(regulator) if regulator else None
    litigation = sanitize_for_json(litigation) if litigation else None
    corporate = sanitize_for_json(corporate) if corporate else None
    salaries = sanitize_for_json(salaries) if salaries else None
    domain_enrichment = sanitize_for_json(domain_enrichment) if domain_enrichment else None
    address_geocoding = sanitize_for_json(address_geocoding) if address_geocoding else None
    contact_extraction = sanitize_for_json(contact_extraction) if contact_extraction else None
    
    errors = errors or {}
    sanitized_errors = sanitize_for_json(errors)
    if not isinstance(sanitized_errors, dict):
        sanitized_errors = {}
    
    final_errors = {k: v for k, v in sanitized_errors.items() if v is not None and v != ""}
    
    print(f"[Aggregator] Sanitized {len(sanitized_errors)} error keys, {len(final_errors)} actual errors")
    
    # Build final result structure
    result: Dict[str, Any] = {
        "identity": identity,
    }
    
    # Handle regulator (may be null)
    if regulator:
        result["regulator"] = regulator
    else:
        result["regulator"] = {
            "confirmed_regulator_hits": [],
            "regulator_summary": {
                "status": "none",
                "headline": "Regulator checks unavailable",
                "bullets": [f"Error: {sanitized_errors.get('regulator') or 'Unknown error'}"],
            },
            "regulator_hits": [],
        }
    
    # Handle litigation (may be null)
    if litigation:
        result["litigation"] = litigation
    else:
        result["litigation"] = {
            "confirmed_litigation_hits": [],
            "litigation_summary": {
                "status": "none",
                "headline": "Litigation checks unavailable",
                "bullets": [f"Error: {sanitized_errors.get('litigation') or 'Unknown error'}"],
            },
            "litigation_hits": [],
        }
    
    # Handle corporate (may be null)
    if corporate:
        result["corporate"] = corporate
    else:
        result["corporate"] = {
            "direct_corporations": [],
            "family_corporations": [],
            "corporate_summary": {
                "status": "none",
                "headline": "Corporate checks unavailable",
                "bullets": [f"Error: {sanitized_errors.get('corporate') or 'Unknown error'}"],
            },
            "corporate_hits": [],
        }
    
    # Handle salaries (may be null)
    if salaries:
        result["salaries"] = salaries
    else:
        result["salaries"] = {
            "ontario_salary_matches": [],
            "search_executed": False,
            "num_people_matched": 0,
            "total_records_found": 0,
            "error": sanitized_errors.get("salaries") or "Unknown error",
        }
    
    # Handle enrichment (may be null)
    enrichment = {}
    if domain_enrichment:
        enrichment["domains"] = domain_enrichment.get("domains", {})
    else:
        enrichment["domains"] = {}

    if address_geocoding:
        enrichment["addresses"] = address_geocoding.get("addresses", {})
    else:
        enrichment["addresses"] = {}

    if contact_extraction:
        enrichment["contacts"] = contact_extraction.get("contacts", {})
    else:
        enrichment["contacts"] = {
            "phones": [],
            "emails": [],
            "addresses": []
        }

    result["enrichment"] = enrichment
    
    result_summary_raw = compute_result_summary(
        identity=identity,
        regulator=regulator,
        litigation=litigation,
        corporate=corporate,
        salaries=salaries,
        errors=final_errors,
    )
    result["result_summary"] = sanitize_for_json(result_summary_raw)
    
    result["partial_failure"] = bool(final_errors)
    result["errors"] = final_errors if final_errors else {}
    
    return result


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
        "identity": {...},      // from phase1_identity
        "regulator": {...},     // from phase2_regulator (may be null)
        "litigation": {...},    // from phase2_litigation (may be null)
        "corporate": {...},     // from phase2_corporate (may be null)
        "salaries": {...},      // from phase2_salaries (may be null)
        "domain_enrichment": {...},  // from domain_enrichment (may be null)
        "address_geocoding": {...},  // from address_geocoding (may be null)
        "contact_extraction": {...},  // from contact_extraction (may be null)
        "errors": {             // dict of phase2 errors
            "regulator": "timeout",
            "litigation": null,
            ...
        }
    }

    Returns aggregated result JSON object.
    """
    # Parse request
    try:
        req_data = request.get_json(silent=True) or {}
    except Exception:
        return {"error": "Invalid JSON"}, 400
    
    # STEP 4: Defensive sanitization - sanitize request data immediately after parsing
    # This catches any bytes from Cloud Workflows HTTP parsing before processing
    print("[Aggregator] Sanitizing request data...")
    req_data = sanitize_for_json(req_data)
    
    job_id = req_data.get("job_id", "")
    identity = req_data.get("identity", {})
    regulator = req_data.get("regulator")
    litigation = req_data.get("litigation")
    corporate = req_data.get("corporate")
    salaries = req_data.get("salaries")
    domain_enrichment = req_data.get("domain_enrichment")
    address_geocoding = req_data.get("address_geocoding")
    contact_extraction = req_data.get("contact_extraction")
    errors = req_data.get("errors", {})
    
    if not identity:
        return {"error": "identity is required"}, 400
    
    print(f"[Aggregator] Starting for job {job_id}")
    print(f"[Aggregator] Identity: {identity.get('golden_name', 'N/A')}")
    print(f"[Aggregator] Regulator: {'present' if regulator else 'null'}")
    print(f"[Aggregator] Litigation: {'present' if litigation else 'null'}")
    print(f"[Aggregator] Corporate: {'present' if corporate else 'null'}")
    print(f"[Aggregator] Salaries: {'present' if salaries else 'null'}")
    print(f"[Aggregator] DomainEnrichment: {'present' if domain_enrichment else 'null'}")
    print(f"[Aggregator] AddressGeocoding: {'present' if address_geocoding else 'null'}")
    print(f"[Aggregator] ContactExtraction: {'present' if contact_extraction else 'null'}")
    actual_error_count = len([v for v in errors.values() if v is not None])
    print(f"[Aggregator] Errors: {actual_error_count} actual phase2 errors (out of {len(errors)} total keys)")
    
    try:
        # Aggregate all results - returns Dict
        result_dict = aggregate(
            identity=identity,
            regulator=regulator,
            litigation=litigation,
            corporate=corporate,
            salaries=salaries,
            domain_enrichment=domain_enrichment,
            address_geocoding=address_geocoding,
            contact_extraction=contact_extraction,
            errors=errors,
        )
        
        print(f"[Aggregator] Complete - returning JSON object")
        return result_dict, 200, {'Content-Type': 'application/json'}
        
    except Exception as e:
        error_msg = str(e)
        error_type = type(e).__name__
        print(f"[Aggregator] Error ({error_type}): {error_msg}")
        import traceback
        traceback.print_exc()
        # Return error as Dict
        return {"error": error_msg, "error_type": error_type}, 500

