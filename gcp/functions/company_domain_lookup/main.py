"""
Company Domain Lookup Cloud Function

Performs:
- LLM-based domain resolution using Gemini 2.5 Flash with Google Search grounding
- Updates Firestore job document with company domain
"""

import json
import os
import traceback
from typing import Any

import functions_framework
from flask import jsonify

# Google Gen AI SDK imports (for Gemini 2.5 Flash with grounding support)
from google import genai
from google.cloud import firestore
from google.genai.types import GenerateContentConfig, GoogleSearch, HttpOptions, Tool
from retry_utils import EmptyLLMResponseError, RetryConfig, retry_with_backoff

# -------------------------
# Config
# -------------------------
GCP_PROJECT = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
# Use global endpoint for Gemini models (Terraform sets GCP_LOCATION for deployment)
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")

# Initialize clients
db = firestore.Client()


# -------------------------
# Vertex AI Gemini Integration with Google Search Grounding
# -------------------------
def vertex_ai_domain_resolution_grounded(company_name: str) -> dict[str, Any]:
    """
    Use Gemini 2.5 Flash with Google Search grounding to determine official company domain.
    The model performs its own searches and returns grounded domain resolution.
    """
    if not GCP_PROJECT:
        return {"error": "GCP_PROJECT not set"}

    system_prompt = (
        "You are a domain resolution expert. Your task is to identify the official company domain "
        "from web search results.\n\n"
        "You MUST always use the search tool to look up the company before responding. Do not skip the search step. "
        "Every request requires a web search. Always include , <Company Name> contact, <Company Name>, and "
        "<Company Name> official site in your searches.\n\n"
        "Prefer the first official company result; main domain only, not subdomains. Cross-reference multiple "
        "snippets when possible to confirm.\n\n"
        "Do not infer or construct domains from the company name (e.g. companyname.com). Leaving domain empty "
        "when the domain is not explicitly visible is correct.\n\n"
        "Return STRICT JSON only with domain, confidence, and rationale."
    )

    user_prompt = f"""Search the web to find the official company domain for:

Company Name: {company_name}

Return valid JSON with these fields:
{{
  "domain": string (e.g., "example.com" without protocol or www),
  "confidence": "high" | "medium" | "low",
  "rationale": string (brief explanation of why this domain was selected)
}}

Return JSON only."""

    # Initialize Google Gen AI client once, outside retry closure.
    # This avoids re-creating the client (and its gRPC channel) on every retry attempt.
    # Timeout of 60s ensures slow/degraded Gemini responses fail fast so retry logic
    # can try again, rather than burning the entire function timeout on one hung call.
    gemini_client = genai.Client(
        vertexai=True,
        project=GCP_PROJECT,
        location=GCP_LOCATION,
        http_options=HttpOptions(timeout=60 * 1000),  # 60 seconds in milliseconds
    )

    def _call_vertex_ai_grounded():
        try:
            # Configure Google Search grounding tool
            google_search_tool = Tool(google_search=GoogleSearch())

            print("[Vertex AI] Calling gemini-2.5-flash with Google Search grounding...")

            # Generate response with grounding
            # NOTE: Structured output with grounding may be available in Gemini 3;
            # we parse JSON manually from text response for compatibility.
            # Use system_instruction parameter to match Google AI Studio behavior
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_prompt,
                config=GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=[google_search_tool],
                    temperature=0.1,
                ),
            )

            if not response or not hasattr(response, "text") or not response.text:
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

            # Parse JSON - if it fails due to empty/invalid content, treat as retryable
            try:
                result = json.loads(content)
            except json.JSONDecodeError as e:
                raise EmptyLLMResponseError(f"JSON decode error: {e}") from e

            # Validate and provide defaults for required fields
            if not result.get("domain"):
                result["domain"] = ""
            if "confidence" not in result:
                result["confidence"] = "low"
            if "rationale" not in result:
                result["rationale"] = "Domain resolution completed but rationale was missing from response."

            # Validate confidence enum values
            if result.get("confidence") not in ["high", "medium", "low"]:
                result["confidence"] = "medium"
                print("[Vertex AI] ⚠️  Invalid confidence value, defaulting to 'medium'")

            print("[Vertex AI] ✅ Successfully resolved domain with grounding")
            return result

        except Exception as e:
            print(f"[Vertex AI] Error: {e}")
            traceback.print_exc()
            raise

    try:
        return retry_with_backoff(
            _call_vertex_ai_grounded,
            RetryConfig(max_attempts=3, base_delay_seconds=2.0, max_delay_seconds=60.0),
            operation_name="Vertex AI grounded domain resolution",
        )
    except Exception as e:
        return {"error": str(e)}


# -------------------------
# Main function
# -------------------------
@functions_framework.http
def main(request):
    """HTTP handler for company domain lookup."""
    # Enable CORS
    if request.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "3600",
        }
        return ("", 204, headers)

    headers = {"Access-Control-Allow-Origin": "*"}

    if request.method != "POST":
        return jsonify({"error": "Method not allowed"}), 405, headers

    try:
        data = request.get_json() or {}
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400, headers

    company_name = (data.get("company_name") or "").strip()
    job_id = (data.get("job_id") or "").strip()

    # Validate inputs
    if not company_name:
        return jsonify({"error": "company_name is required"}), 400, headers

    if not job_id:
        return jsonify({"error": "job_id is required"}), 400, headers

    print(f"[CompanyDomainLookup] Processing company_name='{company_name}', job_id='{job_id}'")

    try:
        # Validate job exists before expensive LLM call
        job_ref = db.collection("jobs").document(job_id)
        job_doc = job_ref.get()

        if not job_doc.exists:
            print(f"[CompanyDomainLookup] Job {job_id} not found in Firestore")
            return jsonify({"status": "error", "error": "Job not found"}), 404, headers

        # Use Vertex AI Gemini with Google Search grounding to determine domain
        print("[CompanyDomainLookup] Calling Vertex AI with Google Search grounding for domain resolution")
        llm_result = vertex_ai_domain_resolution_grounded(company_name)

        if "error" in llm_result:
            print(f"[CompanyDomainLookup] LLM error: {llm_result['error']}")
            return jsonify({"status": "error", "error": llm_result["error"]}), 200, headers

        domain = (llm_result.get("domain") or "").strip()
        confidence = llm_result["confidence"]
        rationale = llm_result["rationale"]

        if not domain:
            print("[CompanyDomainLookup] No domain determined by LLM")
            return jsonify({"status": "no_domain", "message": "LLM could not determine domain"}), 200, headers

        # Clean domain (remove protocol, www, trailing slashes)
        domain = domain.replace("https://", "").replace("http://", "").replace("www.", "")
        domain = domain.split("/")[0].strip()

        print(f"[CompanyDomainLookup] Determined domain: {domain} (confidence: {confidence})")

        # Update Firestore job document with resolved domain
        job_ref.update(
            {
                "input.company_domain": domain,
                "input.company_domain_confidence": confidence,
            }
        )

        print(f"[CompanyDomainLookup] Successfully updated job {job_id} with domain: {domain}")

        return (
            jsonify(
                {
                    "status": "success",
                    "domain": domain,
                    "confidence": confidence,
                    "rationale": rationale,
                }
            ),
            200,
            headers,
        )

    except Exception as e:
        print(f"[CompanyDomainLookup] Unexpected error: {e}")
        traceback.print_exc()
        # Don't fail the job - domain lookup is optional
        return (
            jsonify({"status": "error", "error": str(e)}),
            200,
            headers,
        )  # Return 200 so it doesn't appear as a failure
