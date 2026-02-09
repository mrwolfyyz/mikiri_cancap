"""
Query Constructor Cloud Function

Performs:
- LLM-based name variation generation using Gemini 2.5 Flash Lite
- Precision search query construction for Vertex AI Search
- Returns structured JSON with original name, generated names, and vertex_query
"""

import functions_framework
import os
import json
import traceback
from typing import Dict, Any
from retry_utils import retry_with_backoff, RetryConfig, EmptyLLMResponseError

# Vertex AI imports
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

# -------------------------
# Config
# -------------------------
GCP_PROJECT = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
# "global" uses the Vertex AI global endpoint, which auto-routes to the nearest region.
# This is set via the GCP_LOCATION environment variable in Terraform (functions.tf).
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")

# -------------------------
# Vertex AI initialization (once per cold start)
# -------------------------
if GCP_PROJECT:
    vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
    _MODEL = GenerativeModel(model_name="gemini-2.5-flash-lite")
else:
    _MODEL = None

# Province code to full name mapping
PROVINCE_NAMES = {
    "ON": "Ontario",
    "BC": "British Columbia",
    "AB": "Alberta",
    "QC": "Quebec",
    "MB": "Manitoba",
    "SK": "Saskatchewan",
    "NS": "Nova Scotia",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "PE": "Prince Edward Island",
    "NT": "Northwest Territories",
    "YT": "Yukon",
    "NU": "Nunavut"
}


# -------------------------
# LLM Prompt (provided by user)
# -------------------------
SYSTEM_PROMPT = """You are an OSINT Query Construction Engine.
Task: Convert a Name and Location into a structured JSON object containing a Vertex AI search query.

RULES:
1. ANALYZE the Name to identify common variations.
2. CONSTRUCT a "flat" boolean query string: "Full Name 1" OR "Full Name 2" "Location".
3. DO NOT use parentheses or brackets within the query string itself.
4. OUTPUT strictly valid JSON.

JSON SCHEMA:
{
  "original_name": "string",
  "generated_names": ["string", "string"],
  "vertex_query": "string"
}

EXAMPLES:
Input: Name="Alexander MacKay", City="Halifax", Province="Nova Scotia"
Output:
{
  "original_name": "Alexander MacKay",
  "generated_names": ["Alex MacKay", "Sandy MacKay"],
  "vertex_query": "\\"Alexander MacKay\\" OR \\"Alex MacKay\\" OR \\"Sandy MacKay\\" \\"Halifax, Nova Scotia\\""
}

Input: Name="Elizabeth Tremblay", City="Montreal", Province="Quebec"
Output:
{
  "original_name": "Elizabeth Tremblay",
  "generated_names": ["Liz Tremblay", "Beth Tremblay"],
  "vertex_query": "\\"Elizabeth Tremblay\\" OR \\"Liz Tremblay\\" OR \\"Beth Tremblay\\" \\"Montreal, Quebec\\""
}
"""


def generate_precision_query(full_name: str, city: str, province: str = "") -> Dict[str, Any]:
    """
    Use Gemini 2.5 Flash Lite to generate name variations and construct precision query.

    Args:
        full_name: Full name of the person
        city: City name
        province: Province name (optional, defaults to empty string)

    Returns:
        Dict with keys: original_name, generated_names, vertex_query

    Raises:
        RuntimeError: If GCP_PROJECT is not set or Vertex AI is not initialized
        Exception: If all retry attempts are exhausted
    """
    if not GCP_PROJECT or _MODEL is None:
        raise RuntimeError("GCP_PROJECT not set or Vertex AI not initialized")

    # Convert province code to full name if provided
    province_full = PROVINCE_NAMES.get(province, province) if province else ""

    # Build location string (used as fallback if vertex_query is missing from LLM response)
    location = f"{city}, {province_full}" if province_full else city

    # User prompt with actual inputs
    user_prompt = f"""Input: Name="{full_name}", City="{city}", Province="{province_full}"

Return valid JSON only."""

    # JSON schema for structured output
    schema = {
        "type": "object",
        "properties": {
            "original_name": {"type": "string"},
            "generated_names": {
                "type": "array",
                "items": {"type": "string"}
            },
            "vertex_query": {"type": "string"}
        },
        "required": ["original_name", "generated_names", "vertex_query"]
    }

    def _call_vertex_ai():
        try:
            print(f"[Vertex AI] Calling Gemini 2.5 Flash Lite for query construction...")

            # Combine system prompt and user prompt
            full_prompt = f"{SYSTEM_PROMPT}\n\n{user_prompt}"
            response = _MODEL.generate_content(
                full_prompt,
                generation_config=GenerationConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=schema,
                )
            )

            # Check for empty response
            if not response:
                raise EmptyLLMResponseError("Empty response from Vertex AI")

            try:
                response_text = response.text
            except AttributeError:
                raise EmptyLLMResponseError("Response object missing text attribute")

            if not response_text:
                raise EmptyLLMResponseError("Empty response from Vertex AI")

            # Parse JSON response
            content = response_text.strip()

            # Strip markdown code blocks if present (defensive guard against SDK/model changes)
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

            # Validate required fields (defensive guard against model/SDK behavior changes)
            if "original_name" not in result:
                result["original_name"] = full_name
            if "generated_names" not in result:
                result["generated_names"] = []
            if "vertex_query" not in result:
                # Fallback: construct basic query
                result["vertex_query"] = f'"{full_name}" "{location}"'

            # Ensure generated_names is a list
            if not isinstance(result.get("generated_names"), list):
                result["generated_names"] = []

            print(f"[Vertex AI] Successfully generated query: {result['vertex_query'][:100]}...")
            return result

        except Exception as e:
            print(f"[Vertex AI] Error: {e}")
            traceback.print_exc()
            raise

    return retry_with_backoff(
        _call_vertex_ai,
        RetryConfig(max_attempts=3, base_delay_seconds=2.0, max_delay_seconds=60.0),
        operation_name="Vertex AI query construction"
    )


# -------------------------
# Main function
# -------------------------
@functions_framework.http
def main(request):
    """
    HTTP Cloud Function entry point.

    Expects JSON body:
    {
        "full_name": "Timothy S Lewis",
        "city": "Cobourg",
        "province": "Ontario"  // optional
    }

    Returns JSON:
    {
        "original_name": "Timothy S Lewis",
        "generated_names": ["Tim Lewis", "Tim S Lewis"],
        "vertex_query": "\"Timothy S Lewis\" OR \"Tim Lewis\" OR \"Tim S Lewis\" \"Cobourg, Ontario\""
    }
    """
    # Parse request
    try:
        req_data = request.get_json(silent=True) or {}
    except Exception:
        return {"error": "Invalid JSON"}, 400

    full_name = req_data.get("full_name", "").strip()
    city = req_data.get("city", "").strip()
    province = req_data.get("province", "").strip()

    if not full_name or not city:
        return {"error": "full_name and city are required"}, 400

    print(f"[QueryConstructor] Constructing query for: {full_name}, {city}, {province}")

    # Generate precision query
    try:
        result = generate_precision_query(full_name, city, province)
    except Exception as e:
        print(f"[QueryConstructor] Error: {e}")
        return {"error": str(e)}, 500

    print(f"[QueryConstructor] Successfully generated query")
    return result, 200
