"""
Query Constructor Cloud Function

Performs:
- LLM-based name variation generation using Gemini 2.5 Flash Lite
- Precision search query construction for Vertex AI Search
- Returns structured JSON with original name, generated names, and vertex_query
"""

import json
import os
import traceback
import unicodedata
from typing import Any

import functions_framework

# Vertex AI imports
import vertexai
from retry_utils import EmptyLLMResponseError, RetryConfig, retry_with_backoff
from vertexai.generative_models import GenerationConfig, GenerativeModel

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
# Max lengths after NFKC normalize + whitespace collapse (security / LLM prompt bounds)
_MAX_FULL_NAME_LEN = 200
_MAX_CITY_LEN = 120
_MAX_PROVINCE_LEN = 40

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
    "NU": "Nunavut",
}


def _is_allowed_llm_input_char(c: str) -> bool:
    """Allow Unicode letters, whitespace, and a small punctuation set (audit allow-list)."""
    if c in " '-.":
        return True
    if c.isspace():
        return True
    cat = unicodedata.category(c)
    return cat.startswith("L")


def _normalize_and_validate_allowlist_text(raw: str, max_len: int) -> str | None:
    """
    NFKC-normalize, collapse whitespace, enforce per-character allow-list and max length.
    Returns normalized string or None if invalid.
    """
    if not raw:
        return None
    t = unicodedata.normalize("NFKC", raw).strip()
    if not t:
        return None
    collapsed = " ".join(t.split())
    if len(collapsed) > max_len:
        return None
    for ch in collapsed:
        if not _is_allowed_llm_input_char(ch):
            return None
    return collapsed


def _normalize_province_for_query(province: str) -> tuple[str | None, str | None]:
    """
    Returns (normalized_province_token, error_message).
    Accepts a 2-letter code in PROVINCE_NAMES or a full-name string matching the allow-list.
    """
    if not province:
        return "", None
    p = unicodedata.normalize("NFKC", province).strip()
    if not p:
        return "", None
    if len(p) == 2 and p.isalpha():
        code = p.upper()
        if code in PROVINCE_NAMES:
            return code, None
        return None, "Invalid province code"
    validated = _normalize_and_validate_allowlist_text(p, _MAX_PROVINCE_LEN)
    if validated is None:
        return None, "Invalid province"
    return validated, None


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


def generate_precision_query(full_name: str, city: str, province: str = "") -> dict[str, Any]:
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
            "generated_names": {"type": "array", "items": {"type": "string"}},
            "vertex_query": {"type": "string"},
        },
        "required": ["original_name", "generated_names", "vertex_query"],
    }

    def _call_vertex_ai():
        try:
            print("[Vertex AI] Calling Gemini 2.5 Flash Lite for query construction...")

            # Combine system prompt and user prompt
            full_prompt = f"{SYSTEM_PROMPT}\n\n{user_prompt}"
            response = _MODEL.generate_content(
                full_prompt,
                generation_config=GenerationConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )

            # Check for empty response
            if not response:
                raise EmptyLLMResponseError("Empty response from Vertex AI")

            try:
                response_text = response.text
            except AttributeError as e:
                raise EmptyLLMResponseError("Response object missing text attribute") from e

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
                    raise EmptyLLMResponseError(f"JSON decode error (likely empty response): {e}") from e
                # For other JSON decode errors (malformed JSON), still retry as it might be transient
                raise EmptyLLMResponseError(f"JSON decode error (malformed response): {e}") from e

            # Validate required fields (defensive guard against model/SDK behavior changes)
            if "original_name" not in result or not isinstance(result.get("original_name"), str):
                result["original_name"] = full_name
            if "generated_names" not in result:
                result["generated_names"] = []
            if "vertex_query" not in result or not isinstance(result.get("vertex_query"), str):
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
        operation_name="Vertex AI query construction",
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

    full_name_raw = req_data.get("full_name") or ""
    city_raw = req_data.get("city") or ""
    province_raw = req_data.get("province") or ""

    fn_stripped = str(full_name_raw).strip()
    city_stripped = str(city_raw).strip()
    if not fn_stripped or not city_stripped:
        return {"error": "full_name and city are required"}, 400

    full_name = _normalize_and_validate_allowlist_text(fn_stripped, _MAX_FULL_NAME_LEN)
    city = _normalize_and_validate_allowlist_text(city_stripped, _MAX_CITY_LEN)
    province_norm, province_err = _normalize_province_for_query(str(province_raw).strip())

    if full_name is None or city is None:
        return {"error": "full_name and city must contain only letters, spaces, and limited punctuation"}, 400

    if province_err:
        return {"error": province_err}, 400

    province = province_norm or ""

    print(f"[QueryConstructor] Constructing query for: {full_name}, {city}, {province}")

    # Generate precision query
    try:
        result = generate_precision_query(full_name, city, province)
    except Exception as e:
        print(f"[QueryConstructor] Error: {e}")
        return {"error": str(e)}, 500

    print("[QueryConstructor] Successfully generated query")
    return result, 200
