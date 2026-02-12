#!/usr/bin/env python3
"""
Contact Extraction Cloud Function

Extracts phone numbers, emails, and addresses from web search query results
using Vertex AI Gemini 2.5 Flash with structured output and confidence scoring.

This function is called during Phase 2 of both skip trace and origination workflows,
running in parallel with domain_enrichment and address_geocoding.
"""

import traceback

import functions_framework
from contact_extraction_utils import extract_contact_info_llm


@functions_framework.http
def main(request):
    """
    HTTP Cloud Function entry point for contact extraction.

    Expects JSON body:
    {
        "job_id": "abc123",
        "identity": {
            "seed": {...},
            "queries": [...]
        }
    }

    Returns:
    {
        "contacts": {
            "phones": [...],
            "emails": [...],
            "addresses": [...]
        }
    }
    """
    try:
        req_data = request.get_json(silent=True) or {}
    except Exception:
        return {"error": "Invalid JSON"}, 400

    job_id = req_data.get("job_id", "")
    identity = req_data.get("identity", {})

    if not identity:
        return {"error": "identity is required"}, 400

    queries = identity.get("queries", [])
    seed = identity.get("seed", {})
    exclude_email = seed.get("email")

    print(f"[ContactExtraction] Starting for job {job_id}")
    print(f"[ContactExtraction] Target: {seed.get('full_name', 'N/A')}")
    print(f"[ContactExtraction] Queries: {len(queries)}")

    try:
        contacts = extract_contact_info_llm(queries, seed, exclude_email)
        print(
            f"[ContactExtraction] Complete - returning {len(contacts.get('phones', []))} phones, {len(contacts.get('emails', []))} emails, {len(contacts.get('addresses', []))} addresses"
        )

        return {"contacts": contacts}, 200, {"Content-Type": "application/json"}

    except Exception as e:
        error_msg = str(e)
        error_type = type(e).__name__
        print(f"[ContactExtraction] Error ({error_type}): {error_msg}")
        traceback.print_exc()
        return {"error": error_msg, "error_type": error_type}, 500
