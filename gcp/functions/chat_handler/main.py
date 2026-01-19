"""
Chat Handler Cloud Function

Handles interactive chat for skip tracing investigations using Gemini 3 Flash Preview
with Google Search grounding. Allows skip tracers to ask follow-up questions about
completed investigations.
"""

import functions_framework
import os
import json
from typing import Dict, Any, List, Optional
from flask import Request, jsonify

# Google Gen AI SDK imports (official SDK for Gemini 3 with grounding support)
from google import genai
from google.genai.types import GenerateContentConfig, Tool, GoogleSearch

# -------------------------
# Config
# -------------------------
GCP_PROJECT = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
# Use 'global' endpoint for Gemini models - routes to any supported region
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")

# System prompt for skip tracing assistant
SYSTEM_PROMPT = """You are a skip tracing assistant helping locate individuals. You have access to investigation reports (identity clues and skip tracing checklist). Use the provided markdown context to understand what's already been found. The skip tracer may provide additional context in their messages - use this information to guide your searches and responses.

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


def format_conversation_history(history: List[Dict[str, str]]) -> str:
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


def build_prompt(
    message: str,
    conversation_history: List[Dict[str, str]],
    markdown_context: Optional[Dict[str, str]] = None
) -> str:
    """Build the full prompt for the LLM."""
    prompt_parts = [SYSTEM_PROMPT]
    
    # Add markdown context if this is the first message
    if markdown_context and not conversation_history:
        prompt_parts.append("\n## Investigation Reports\n")
        if markdown_context.get("identity"):
            prompt_parts.append("### Identity Report\n")
            prompt_parts.append(markdown_context["identity"])
            prompt_parts.append("\n")
        if markdown_context.get("skiptrace"):
            prompt_parts.append("### Skip Trace Checklist\n")
            prompt_parts.append(markdown_context["skiptrace"])
            prompt_parts.append("\n")
    
    # Add conversation history if present
    if conversation_history:
        prompt_parts.append("\n## Previous Conversation\n")
        prompt_parts.append(format_conversation_history(conversation_history))
        prompt_parts.append("\n")
    
    # Add current message
    prompt_parts.append(f"\n## Current Question\nUser: {message}\n\nAssistant:")
    
    return "\n".join(prompt_parts)


def extract_grounding_metadata(response) -> Dict[str, Any]:
    """Extract grounding metadata from Google Gen AI SDK response."""
    metadata = {
        "search_entry_point": "",
        "web_search_queries": [],
        "grounding_chunks": []
    }
    
    try:
        # Google Gen AI SDK response structure
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            
            # Extract grounding metadata if available
            if hasattr(candidate, 'grounding_metadata'):
                grounding = candidate.grounding_metadata
                
                # Extract search queries
                if hasattr(grounding, 'web_search_queries'):
                    metadata["web_search_queries"] = list(grounding.web_search_queries)
                
                # Extract grounding chunks
                if hasattr(grounding, 'grounding_chunks'):
                    chunks = []
                    for chunk in grounding.grounding_chunks:
                        chunk_data = {}
                        if hasattr(chunk, 'web'):
                            chunk_data["web"] = {
                                "uri": getattr(chunk.web, 'uri', ''),
                                "title": getattr(chunk.web, 'title', '')
                            }
                        chunks.append(chunk_data)
                    metadata["grounding_chunks"] = chunks
                
                # Extract search entry point (HTML/CSS for display)
                if hasattr(grounding, 'search_entry_point'):
                    entry_point = grounding.search_entry_point
                    if hasattr(entry_point, 'rendered_content'):
                        metadata["search_entry_point"] = entry_point.rendered_content
                    elif hasattr(entry_point, 'html'):
                        metadata["search_entry_point"] = entry_point.html
        
        # Log usage metadata for debugging
        if hasattr(response, 'usage_metadata'):
            print(f"[ChatHandler] Usage metadata: {response.usage_metadata}")
    
    except Exception as e:
        print(f"[ChatHandler] Warning: Could not extract grounding metadata: {e}")
        import traceback
        traceback.print_exc()
    
    return metadata


@functions_framework.http
def main(request: Request):
    """Main HTTP handler for chat requests."""
    # Enable CORS
    if request.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
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
    
    # Extract request data
    message = (data.get("message") or "").strip()
    conversation_history = data.get("conversation_history", [])
    markdown_context = data.get("markdown_context")
    
    # Validate required fields
    if not message:
        return jsonify({"error": "message is required"}), 400, headers
    
    if not isinstance(conversation_history, list):
        return jsonify({"error": "conversation_history must be a list"}), 400, headers
    
    # Validate conversation history format
    for msg in conversation_history:
        if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
            return jsonify({"error": "Invalid conversation_history format"}), 400, headers
    
    # Initialize Google Gen AI client with Vertex AI
    if not GCP_PROJECT:
        return jsonify({"error": "GCP_PROJECT not configured"}), 500, headers
    
    try:
        # Initialize client with Vertex AI (official SDK pattern)
        client = genai.Client(
            vertexai=True,
            project=GCP_PROJECT,
            location=GCP_LOCATION
        )
        print(f"[ChatHandler] Initialized Google Gen AI client for Vertex AI")
    except Exception as e:
        print(f"[ChatHandler] Google Gen AI client initialization error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Client initialization failed: {str(e)}"}), 500, headers
    
    # Build prompt
    try:
        prompt = build_prompt(message, conversation_history, markdown_context)
    except Exception as e:
        print(f"[ChatHandler] Error building prompt: {e}")
        return jsonify({"error": f"Failed to build prompt: {str(e)}"}), 500, headers
    
    # Call Gemini 3 Flash Preview with Google Search grounding
    try:
        # Configure Google Search grounding tool (official SDK pattern)
        google_search_tool = Tool(google_search=GoogleSearch())
        
        # Create config with grounding tool
        # Conservative settings for skip tracing accuracy
        config = GenerateContentConfig(
            tools=[google_search_tool],
            temperature=0.7,  # Lower for more deterministic, accurate responses
            max_output_tokens=2048,  # Ensure complete responses
            top_p=0.9,  # Slightly constrain sampling for consistency
        )
        
        print(f"[ChatHandler] Calling Gemini 3 Flash Preview with Google Search grounding...")
        
        # Generate response using official SDK pattern
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
            config=config
        )
        
        if not response or not hasattr(response, 'text') or not response.text:
            return jsonify({
                "error": "Empty response from LLM",
                "response": "I apologize, but I couldn't generate a response. Please try again."
            }), 500, headers
        
        # Extract response text
        response_text = response.text.strip()
        
        # Log full response structure for debugging
        print(f"[ChatHandler] Full response type: {type(response)}")
        print(f"[ChatHandler] Response attributes: {dir(response)}")
        if hasattr(response, 'candidates'):
            print(f"[ChatHandler] Response has {len(response.candidates) if response.candidates else 0} candidates")
            if response.candidates:
                candidate = response.candidates[0]
                print(f"[ChatHandler] Candidate type: {type(candidate)}")
                print(f"[ChatHandler] Candidate attributes: {dir(candidate)}")
                if hasattr(candidate, 'grounding_metadata'):
                    print(f"[ChatHandler] Candidate has grounding_metadata: {type(candidate.grounding_metadata)}")
                    print(f"[ChatHandler] Grounding metadata attributes: {dir(candidate.grounding_metadata)}")
                    if hasattr(candidate.grounding_metadata, 'search_entry_point'):
                        sep = candidate.grounding_metadata.search_entry_point
                        print(f"[ChatHandler] search_entry_point type: {type(sep)}")
                        print(f"[ChatHandler] search_entry_point attributes: {dir(sep)}")
                        if hasattr(sep, 'rendered_content'):
                            rc = sep.rendered_content
                            print(f"[ChatHandler] rendered_content type: {type(rc)}")
                            print(f"[ChatHandler] rendered_content length: {len(rc) if rc else 0}")
                            print(f"[ChatHandler] rendered_content preview (first 500 chars): {str(rc)[:500] if rc else 'None'}")
        
        # Extract grounding metadata
        grounding_metadata = extract_grounding_metadata(response)
        
        # Log extracted metadata structure
        print(f"[ChatHandler] ========== EXTRACTED GROUNDING METADATA ==========")
        print(f"[ChatHandler] Extracted grounding_metadata keys: {list(grounding_metadata.keys())}")
        print(f"[ChatHandler] search_entry_point type: {type(grounding_metadata.get('search_entry_point'))}")
        if grounding_metadata.get('search_entry_point'):
            sep_content = grounding_metadata['search_entry_point']
            print(f"[ChatHandler] search_entry_point length: {len(sep_content)}")
            print(f"[ChatHandler] search_entry_point FULL CONTENT:")
            print(f"{sep_content}")
            print(f"[ChatHandler] ===============================================")
        else:
            print(f"[ChatHandler] WARNING: search_entry_point is empty or None")
            print(f"[ChatHandler] Full grounding_metadata: {json.dumps(grounding_metadata, indent=2, default=str)}")
        
        # Log the exact structure for debugging
        print(f"[ChatHandler] Response structure:")
        print(f"  - response type: {type(response)}")
        print(f"  - response.text: {response.text[:200] if response.text else 'None'}...")
        print(f"  - has candidates: {hasattr(response, 'candidates')}")
        if hasattr(response, 'candidates') and response.candidates:
            print(f"  - candidates count: {len(response.candidates)}")
            candidate = response.candidates[0]
            print(f"  - candidate type: {type(candidate)}")
            print(f"  - has grounding_metadata: {hasattr(candidate, 'grounding_metadata')}")
            if hasattr(candidate, 'grounding_metadata'):
                grounding = candidate.grounding_metadata
                print(f"  - grounding_metadata type: {type(grounding)}")
                print(f"  - grounding_metadata dir: {dir(grounding)}")
                if hasattr(grounding, 'search_entry_point'):
                    entry_point = grounding.search_entry_point
                    print(f"  - search_entry_point type: {type(entry_point)}")
                    print(f"  - search_entry_point dir: {dir(entry_point)}")
                    if hasattr(entry_point, 'rendered_content'):
                        rendered = entry_point.rendered_content
                        print(f"  - rendered_content type: {type(rendered)}")
                        print(f"  - rendered_content length: {len(rendered) if rendered else 0}")
                        print(f"  - rendered_content preview: {rendered[:500] if rendered else 'None'}...")
        
        print(f"[ChatHandler] Extracted grounding_metadata: {json.dumps(grounding_metadata, indent=2, default=str)}")
        
        # Build updated conversation history
        updated_history = conversation_history.copy()
        updated_history.append({"role": "user", "content": message})
        updated_history.append({"role": "assistant", "content": response_text})
        
        # Return response
        return jsonify({
            "response": response_text,
            "conversation_history": updated_history,
            "grounding_metadata": grounding_metadata
        }), 200, headers
    
    except Exception as e:
        print(f"[ChatHandler] Error calling Gemini: {e}")
        import traceback
        traceback.print_exc()
        
        # Return error in response field (as per design)
        return jsonify({
            "error": f"Chat processing failed: {str(e)}",
            "response": f"I encountered an error while processing your request: {str(e)}. Please try again."
        }), 500, headers
