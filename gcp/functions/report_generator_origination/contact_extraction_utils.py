"""
Shared contact extraction utilities.

Provides LLM-based contact information extraction using Vertex AI Gemini,
used by multiple Cloud Functions:
- contact_extraction
- report_generator_origination
- report_generator_skiptrace

Dependencies:
- google-genai (Google Gen AI SDK)
- retry_utils (shared)
- address_utils (shared)
"""

import json
import os
import re
from typing import Any

from address_utils import clean_address_for_geocoding

# Google Gen AI SDK imports (for Gemini with timeout support)
from google import genai  # type: ignore[attr-defined]
from google.genai.types import GenerateContentConfig, HttpOptions  # type: ignore[attr-defined]

# Import retry utilities (local copy for consistency with other phase2 functions)
from retry_utils import EmptyLLMResponseError, RetryConfig, retry_with_backoff

# -------------------------
# Vertex AI Config
# -------------------------
GCP_PROJECT = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")


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
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "source_url": {"type": "string"},
                    "snippet": {"type": "string"},
                },
                "required": ["number_raw", "number_digits", "confidence", "source_url"],
            },
        },
        "emails": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "source_url": {"type": "string"},
                    "snippet": {"type": "string"},
                },
                "required": ["email", "confidence", "source_url"],
            },
        },
        "addresses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "address_raw": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "source_url": {"type": "string"},
                    "snippet": {"type": "string"},
                },
                "required": ["address_raw", "confidence", "source_url"],
            },
        },
    },
    "required": ["phones", "emails", "addresses"],
}


def extract_contact_info_llm(
    queries: list[dict[str, Any]], seed: dict[str, Any], exclude_email: str | None = None
) -> dict[str, list[dict[str, Any]]]:
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
- Name: {seed.get("full_name", "")}
- Email: {seed.get("email", "")}
- City: {seed.get("last_known_city", "N/A")}
- Company: {seed.get("company_name", "N/A") if seed.get("company_name") else "N/A"}

Search Results ({len(queries)} queries, {total_hits} total hits):
{json.dumps(queries, indent=2)}

Return valid JSON with phones, emails, and addresses arrays. Each item should have confidence, source_url, and snippet fields."""

    # Initialize Google Gen AI client once, outside retry closure.
    # This avoids re-creating the client (and its gRPC channel) on every retry attempt.
    # Timeout of 60s ensures slow/degraded Gemini responses fail fast so retry logic
    # can try again, rather than burning the entire function timeout on one hung call.
    try:
        gemini_client = genai.Client(
            vertexai=True,
            project=GCP_PROJECT,
            location=GCP_LOCATION,
            http_options=HttpOptions(timeout=60 * 1000),  # 60 seconds in milliseconds
        )
    except Exception as e:
        print(f"[LLM Extraction] Gen AI client init error: {e}")
        return {"phones": [], "emails": [], "addresses": []}

    def _call_vertex_ai():
        try:
            print(f"[LLM Extraction] Calling Gemini 2.5 Flash for {total_hits} hits...")

            full_prompt = f"{system_prompt}\n\n{user_prompt}"
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=full_prompt,
                config=GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=EXTRACTION_SCHEMA,
                ),
            )

            if not response:
                raise EmptyLLMResponseError("Empty response from Vertex AI")

            response_text = response.text
            if not response_text:
                raise EmptyLLMResponseError("Empty response text")

            # Parse and validate
            content = response_text.strip()

            # Strip markdown code blocks if present (defensive: response_mime_type
            # should guarantee raw JSON, but LLM APIs can behave unexpectedly)
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

            # Validate structure - ensure all required keys exist and are lists
            for key in ("phones", "emails", "addresses"):
                if not isinstance(result.get(key), list):
                    result[key] = []

            # Validate and normalize phone numbers
            normalized_phones = []
            seen_digits = set()
            for phone in result["phones"]:
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

                normalized_phones.append(
                    {
                        "number_raw": number_raw,
                        "number_digits": number_digits,
                        "confidence": confidence,
                        "source_url": phone.get("source_url", ""),
                        "snippet": phone.get("snippet", "").strip(),
                    }
                )

            # Filter excluded email and normalize emails
            normalized_emails = []
            seen_emails = set()
            exclude_lower = exclude_email.lower().strip() if exclude_email else None

            for email_obj in result["emails"]:
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

                normalized_emails.append(
                    {
                        "email": email,
                        "confidence": confidence,
                        "source_url": email_obj.get("source_url", ""),
                        "snippet": email_obj.get("snippet", "").strip(),
                    }
                )

            # Normalize addresses
            normalized_addresses = []
            seen_addresses = set()

            for addr_obj in result["addresses"]:
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
                    print(f"[LLM Extraction] Filtered out city-only address: {addr_cleaned}")
                    continue

                addr_normalized = addr_cleaned.lower().strip()
                addr_normalized = re.sub(r",", " ", addr_normalized)
                addr_normalized = re.sub(r"\s+", " ", addr_normalized)

                if addr_normalized in seen_addresses:
                    continue
                seen_addresses.add(addr_normalized)

                # Validate confidence
                confidence = addr_obj.get("confidence", "medium")
                if confidence not in ["high", "medium", "low"]:
                    confidence = "medium"

                normalized_addresses.append(
                    {
                        "address_raw": addr_cleaned,
                        "confidence": confidence,
                        "source_url": addr_obj.get("source_url", ""),
                        "snippet": addr_obj.get("snippet", "").strip(),
                    }
                )

            result["phones"] = normalized_phones
            result["emails"] = normalized_emails
            result["addresses"] = normalized_addresses

            print(
                f"[LLM Extraction] Extracted {len(normalized_phones)} phones, {len(normalized_emails)} emails, {len(normalized_addresses)} addresses"
            )
            return result

        except Exception as e:
            print(f"[LLM Extraction] Error: {e}")
            raise

    try:
        return retry_with_backoff(
            _call_vertex_ai,
            RetryConfig(max_attempts=3, base_delay_seconds=2.0, max_delay_seconds=60.0),
            operation_name="LLM contact info extraction",
        )
    except Exception as e:
        print(f"[LLM Extraction] Error after retries: {e}")
        return {"phones": [], "emails": [], "addresses": []}
