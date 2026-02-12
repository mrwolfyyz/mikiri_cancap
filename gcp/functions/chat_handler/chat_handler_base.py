"""
Chat Handler Base Module

Shared infrastructure for chat handler Cloud Functions. Provides CORS handling,
request validation, Gemini client management, grounding metadata extraction,
and retry-wrapped LLM calls. Each handler provides only its domain-specific
system prompt, build_prompt function, and temperature.
"""

import os
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flask import Request, jsonify

# Google Gen AI SDK imports
from google import genai
from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

# Retry utilities (copied to function dir by prepare-functions.sh)
from retry_utils import EmptyLLMResponseError, RetryConfig, retry_with_backoff

# -------------------------
# Config
# -------------------------
GCP_PROJECT = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")

# Model configuration
MODEL_NAME = "gemini-3-flash-preview"
MAX_OUTPUT_TOKENS = 2048
TOP_P = 0.9

# Prompt size guard: truncate oldest history entries if prompt exceeds this limit.
# ~28K chars is roughly ~7K tokens, well within Gemini's context window with margin.
MAX_PROMPT_CHARS = 28000

# Retry configuration for Gemini API calls
GEMINI_RETRY_CONFIG = RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=10.0)

# CORS headers - origin set from environment (restrict in production)
_CORS_ORIGIN = os.environ.get("CORS_ALLOWED_ORIGINS", "*")
CORS_PREFLIGHT_HEADERS = {
    "Access-Control-Allow-Origin": _CORS_ORIGIN,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "3600",
}
CORS_HEADERS = {"Access-Control-Allow-Origin": _CORS_ORIGIN}

# -------------------------
# Lazy client singleton
# -------------------------
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Get or create the Gemini client singleton (lazy initialization)."""
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
    return _client


# -------------------------
# Handler configuration
# -------------------------
@dataclass
class ChatHandlerConfig:
    """Configuration for a chat handler instance."""

    build_prompt_fn: Callable[[str, list[dict[str, str]], dict[str, str] | None], str]
    temperature: float
    log_prefix: str


# -------------------------
# Shared utilities
# -------------------------
def format_conversation_history(history: list[dict[str, str]]) -> str:
    """Format conversation history for the prompt."""
    if not history:
        return ""

    formatted = []
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            formatted.append(f"User: {content}")
        elif role == "assistant":
            formatted.append(f"Assistant: {content}")

    return "\n".join(formatted)


def extract_grounding_metadata(response, log_prefix: str = "[ChatHandler]") -> dict[str, Any]:
    """Extract grounding metadata from Google Gen AI SDK response."""
    metadata = {"search_entry_point": "", "web_search_queries": [], "grounding_chunks": []}

    try:
        if hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]

            if hasattr(candidate, "grounding_metadata"):
                grounding = candidate.grounding_metadata

                # Extract search queries
                if hasattr(grounding, "web_search_queries"):
                    metadata["web_search_queries"] = list(grounding.web_search_queries)

                # Extract grounding chunks
                if hasattr(grounding, "grounding_chunks"):
                    chunks = []
                    for chunk in grounding.grounding_chunks:
                        chunk_data = {}
                        if hasattr(chunk, "web"):
                            chunk_data["web"] = {
                                "uri": getattr(chunk.web, "uri", ""),
                                "title": getattr(chunk.web, "title", ""),
                            }
                        chunks.append(chunk_data)
                    metadata["grounding_chunks"] = chunks

                # Extract search entry point (HTML/CSS for display)
                if hasattr(grounding, "search_entry_point"):
                    entry_point = grounding.search_entry_point
                    if hasattr(entry_point, "rendered_content"):
                        metadata["search_entry_point"] = entry_point.rendered_content
                    elif hasattr(entry_point, "html"):
                        metadata["search_entry_point"] = entry_point.html

        # Log usage metadata
        if hasattr(response, "usage_metadata"):
            print(f"{log_prefix} Usage metadata: {response.usage_metadata}")

    except Exception as e:
        print(f"{log_prefix} Warning: Could not extract grounding metadata: {e}")
        traceback.print_exc()

    return metadata


def _truncate_history_if_needed(
    prompt: str,
    message: str,
    conversation_history: list[dict[str, str]],
    markdown_context: dict[str, str] | None,
    build_prompt_fn: Callable,
    log_prefix: str,
) -> str:
    """Truncate oldest conversation history entries if prompt exceeds size limit."""
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt

    # Progressively drop oldest history entries until within limit
    truncated_history = list(conversation_history)
    while len(prompt) > MAX_PROMPT_CHARS and len(truncated_history) > 0:
        # Remove oldest pair (user + assistant) or single entry
        truncated_history.pop(0)
        if truncated_history and truncated_history[0].get("role") == "assistant":
            truncated_history.pop(0)
        prompt = build_prompt_fn(message, truncated_history, markdown_context)

    dropped = len(conversation_history) - len(truncated_history)
    if dropped > 0:
        print(f"{log_prefix} Truncated {dropped} oldest history entries to fit prompt size limit")

    return prompt


# -------------------------
# Main request handler
# -------------------------
def handle_chat_request(request: Request, config: ChatHandlerConfig) -> tuple[Any, int, dict[str, str]]:
    """
    Main orchestrator for chat requests. Handles CORS, validation, client init,
    prompt building, retry-wrapped Gemini call, and response formatting.

    Returns:
        Flask response tuple: (response_body, status_code, headers)
    """
    log_prefix = config.log_prefix

    # CORS preflight
    if request.method == "OPTIONS":
        return ("", 204, CORS_PREFLIGHT_HEADERS)

    if request.method != "POST":
        return jsonify({"error": "Method not allowed"}), 405, CORS_HEADERS

    # Parse JSON body
    try:
        data = request.get_json() or {}
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400, CORS_HEADERS

    # Extract and validate request data
    message = (data.get("message") or "").strip()
    conversation_history = data.get("conversation_history", [])
    markdown_context = data.get("markdown_context")

    if not message:
        return jsonify({"error": "message is required"}), 400, CORS_HEADERS

    if len(message) > 10_000:
        return jsonify({"error": "Message too long"}), 400, CORS_HEADERS

    if not isinstance(conversation_history, list):
        return jsonify({"error": "conversation_history must be a list"}), 400, CORS_HEADERS

    if len(conversation_history) > 50:
        return jsonify({"error": "Conversation history too long"}), 400, CORS_HEADERS

    for msg in conversation_history:
        if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
            return jsonify({"error": "Invalid conversation_history format"}), 400, CORS_HEADERS

    # Get or initialize client
    if not GCP_PROJECT:
        return jsonify({"error": "GCP_PROJECT not configured"}), 500, CORS_HEADERS

    try:
        client = _get_client()
    except Exception as e:
        print(f"{log_prefix} Client initialization error: {e}")
        traceback.print_exc()
        return jsonify({"error": "Service initialization failed"}), 500, CORS_HEADERS

    # Build prompt
    try:
        prompt = config.build_prompt_fn(message, conversation_history, markdown_context)
        prompt = _truncate_history_if_needed(
            prompt, message, conversation_history, markdown_context, config.build_prompt_fn, log_prefix
        )
    except Exception as e:
        print(f"{log_prefix} Error building prompt: {e}")
        traceback.print_exc()
        return jsonify({"error": "Failed to build prompt"}), 500, CORS_HEADERS

    # Call Gemini with Google Search grounding (retry-wrapped)
    try:
        google_search_tool = Tool(google_search=GoogleSearch())
        gen_config = GenerateContentConfig(
            tools=[google_search_tool],
            temperature=config.temperature,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            top_p=TOP_P,
        )

        print(f"{log_prefix} Calling {MODEL_NAME} with Google Search grounding...")

        def _call_gemini():
            resp = client.models.generate_content(model=MODEL_NAME, contents=prompt, config=gen_config)
            if not resp or not hasattr(resp, "text") or not resp.text:
                raise EmptyLLMResponseError(f"Empty response from {MODEL_NAME}")
            return resp

        response = retry_with_backoff(
            _call_gemini, GEMINI_RETRY_CONFIG, operation_name=f"{log_prefix} {MODEL_NAME} API call"
        )

        response_text = response.text.strip()
        grounding_metadata = extract_grounding_metadata(response, log_prefix)

        print(f"{log_prefix} Response generated successfully ({len(response_text)} chars)")

        # Build updated conversation history
        updated_history = conversation_history.copy()
        updated_history.append({"role": "user", "content": message})
        updated_history.append({"role": "assistant", "content": response_text})

        return (
            jsonify(
                {
                    "response": response_text,
                    "conversation_history": updated_history,
                    "grounding_metadata": grounding_metadata,
                }
            ),
            200,
            CORS_HEADERS,
        )

    except EmptyLLMResponseError:
        print(f"{log_prefix} All retry attempts returned empty responses")
        return (
            jsonify(
                {
                    "error": "Empty response from LLM",
                    "response": "I couldn't generate a response after multiple attempts. Please try again.",
                }
            ),
            500,
            CORS_HEADERS,
        )

    except Exception as e:
        print(f"{log_prefix} Error calling Gemini: {e}")
        traceback.print_exc()

        return (
            jsonify(
                {
                    "error": "Chat processing failed",
                    "response": "I encountered an error while processing your request. Please try again.",
                }
            ),
            500,
            CORS_HEADERS,
        )
