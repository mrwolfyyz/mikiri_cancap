"""
Chat Handler Cloud Function

Handles interactive chat for skip tracing investigations using Gemini 3 Flash Preview
with Google Search grounding. Allows skip tracers to ask follow-up questions about
completed investigations.
"""

import functions_framework
from typing import Dict, List, Optional
from flask import Request

from chat_handler_base import (
    ChatHandlerConfig,
    format_conversation_history,
    handle_chat_request,
)

# -------------------------
# System prompt
# -------------------------
SYSTEM_PROMPT = """You are a skip tracing assistant helping locate individuals. You have access to investigation reports (identity clues). Use the provided markdown context to understand what's already been found. The skip tracer may provide additional context in their messages - use this information to guide your searches and responses.

When to Search:
- Search when the user asks for updated information
- Search when looking for addresses, phone numbers, or contact details
- Search when verifying or expanding on information
- Use your judgment about when searches would be helpful

Response Style:
- Be direct and actionable - skip tracers need clear next steps
- Focus on contact information: addresses, phone numbers, social profiles, employment
- If you find multiple leads, prioritize by confidence/recency
- Include specifics: full addresses, complete phone numbers, profile URLs

What NOT to do:
- Do not make up contact information - only provide what you find
- Do not provide general skip tracing advice unless asked
- Do not apologize for limitations - just indicate what you found or didn't find

Format:
- Keep responses concise (2-4 paragraphs max unless user asks for detail)
- Use natural language, not bullet points or lists unless specifically helpful
- Include URLs when referencing sources"""


# -------------------------
# Prompt builder
# -------------------------
def build_prompt(
    message: str,
    conversation_history: List[Dict[str, str]],
    markdown_context: Optional[Dict[str, str]] = None
) -> str:
    """Build the full prompt for the skip tracing LLM."""
    prompt_parts = [SYSTEM_PROMPT]

    # Add markdown context if this is the first message
    if markdown_context and not conversation_history:
        prompt_parts.append("\n## Investigation Reports\n")
        if markdown_context.get("identity"):
            prompt_parts.append("### Identity Report\n")
            prompt_parts.append(markdown_context["identity"])
            prompt_parts.append("\n")

    # Add conversation history if present
    if conversation_history:
        prompt_parts.append("\n## Previous Conversation\n")
        prompt_parts.append(format_conversation_history(conversation_history))
        prompt_parts.append("\n")

    # Add current message
    prompt_parts.append(f"\n## Current Question\nUser: {message}\n\nAssistant:")

    return "\n".join(prompt_parts)


# -------------------------
# Handler configuration
# -------------------------
_config = ChatHandlerConfig(
    build_prompt_fn=build_prompt,
    temperature=0.7,
    log_prefix="[ChatHandler]",
)


# -------------------------
# Entry point
# -------------------------
@functions_framework.http
def main(request: Request):
    """Main HTTP handler for skip tracing chat requests."""
    return handle_chat_request(request, _config)
