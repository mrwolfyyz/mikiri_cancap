"""
Aggregator Cloud Function

Combines results from phase2 enrichment functions (domain_enrichment,
address_geocoding, contact_extraction) with identity data and computes
final summaries. Handles partial failures gracefully - if a phase2
function fails, its result is set to null and an error is recorded,
but aggregation continues.
"""

import traceback
from typing import Any

import functions_framework


def sanitize_for_json(obj: Any) -> Any:
    """
    Recursively sanitize data structure to ensure all values are JSON-serializable.
    Converts bytes to strings and handles other non-serializable types.

    Note: Input is JSON from Cloud Workflows HTTP calls, so bytes are unlikely.
    This is retained as a defensive safety net.
    """
    if obj is None:
        return None
    elif isinstance(obj, bytes):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return str(obj)
    elif isinstance(obj, bytearray):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return str(obj)
    elif isinstance(obj, dict):
        return {sanitize_for_json(k): sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, (str, int, float, bool)):
        return obj
    elif hasattr(obj, "__dict__"):
        return sanitize_for_json(obj.__dict__)
    else:
        try:
            if hasattr(obj, "decode") and callable(obj.decode):
                return obj.decode("utf-8", errors="replace")
        except Exception:
            pass
        try:
            return str(obj)
        except Exception:
            return None


def compute_result_summary(
    identity: dict[str, Any],
    errors: dict[str, str],
) -> dict[str, Any]:
    """
    Compute high-level result summary from identity and enrichment results.

    Returns a summary dict with:
    - overall_status: "clear" or "partial_failure"
    - headline: High-level summary text
    - bullets: List of key findings
    """
    bullets: list[str] = []
    has_partial_failure = bool(errors)

    # Check identity status
    identity_location = identity.get("location", {})
    if isinstance(identity_location, dict) and identity_location.get("confidence") == "high":
        bullets.append("High-confidence identity and location verified")

    # Note enrichment errors
    for source, error_msg in errors.items():
        bullets.append(f"{source}: Error - {error_msg}")

    # Determine overall status
    if has_partial_failure:
        overall_status = "partial_failure"
        headline = "Investigation completed with partial data (some sources unavailable)"
    else:
        overall_status = "clear"
        headline = "Investigation complete - no significant risk signals detected"

    return {
        "overall_status": overall_status,
        "headline": headline,
        "bullets": bullets[:5],
        "partial_failure": has_partial_failure,
    }


def aggregate(
    identity: dict[str, Any],
    domain_enrichment: dict[str, Any] | None = None,
    address_geocoding: dict[str, Any] | None = None,
    contact_extraction: dict[str, Any] | None = None,
    errors: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Aggregate identity and phase2 enrichment results into final result.

    Handles null results gracefully - if a phase2 function failed,
    its data structure is set to empty/default values.
    """
    errors = errors or {}
    final_errors = {k: v for k, v in errors.items() if v is not None and v != ""}

    print(f"[Aggregator] {len(errors)} error keys, {len(final_errors)} actual errors")

    # Build final result structure
    result: dict[str, Any] = {
        "identity": identity,
    }

    # Build enrichment structure
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
        enrichment["contacts"] = {"phones": [], "emails": [], "addresses": []}

    result["enrichment"] = enrichment

    result["result_summary"] = compute_result_summary(
        identity=identity,
        errors=final_errors,
    )

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
        "identity": {...},             // from phase1_identity
        "domain_enrichment": {...},    // from domain_enrichment (may be null)
        "address_geocoding": {...},    // from address_geocoding (may be null)
        "contact_extraction": {...},   // from contact_extraction (may be null)
        "errors": {                    // dict of phase2 errors
            "domain_enrichment": "timeout",
            "address_geocoding": null,
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

    # Defensive sanitization - sanitize request data immediately after parsing.
    # Input is JSON from Cloud Workflows so bytes are unlikely, but this is a safety net.
    req_data = sanitize_for_json(req_data)

    job_id = req_data.get("job_id", "")
    identity = req_data.get("identity", {})
    domain_enrichment = req_data.get("domain_enrichment")
    address_geocoding = req_data.get("address_geocoding")
    contact_extraction = req_data.get("contact_extraction")
    errors = req_data.get("errors", {})

    if not identity:
        return {"error": "identity is required"}, 400

    print(f"[Aggregator] Starting for job {job_id}")
    print(f"[Aggregator] Identity: {identity.get('golden_name', 'N/A')}")
    print(f"[Aggregator] DomainEnrichment: {'present' if domain_enrichment else 'null'}")
    print(f"[Aggregator] AddressGeocoding: {'present' if address_geocoding else 'null'}")
    print(f"[Aggregator] ContactExtraction: {'present' if contact_extraction else 'null'}")
    actual_error_count = len([v for v in errors.values() if v is not None and v != ""])
    print(f"[Aggregator] Errors: {actual_error_count} actual phase2 errors")

    try:
        result_dict = aggregate(
            identity=identity,
            domain_enrichment=domain_enrichment,
            address_geocoding=address_geocoding,
            contact_extraction=contact_extraction,
            errors=errors,
        )

        print("[Aggregator] Complete - returning JSON object")
        return result_dict, 200, {"Content-Type": "application/json"}

    except Exception as e:
        error_msg = str(e)
        error_type = type(e).__name__
        print(f"[Aggregator] Error ({error_type}): {error_msg}")
        traceback.print_exc()
        return {"error": error_msg, "error_type": error_type}, 500
