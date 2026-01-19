"""
Company Domain Lookup Cloud Function

Performs:
- Google Custom Search API (PSE) for company name
- LLM-based domain resolution from search results using Vertex AI Gemini
- Updates Firestore job document with company domain
"""

import functions_framework
import os
import json
import requests
from typing import Dict, Any, List
from flask import jsonify
from google.cloud import firestore
from retry_utils import retry_with_backoff, RetryConfig, EmptyLLMResponseError

# Vertex AI imports
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

# Initialize clients
db = firestore.Client()

# -------------------------
# Config
# -------------------------
GOOGLE_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
GOOGLE_SEARCH_CX = os.environ.get("GOOGLE_SEARCH_CX", "")
GCP_PROJECT = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
# Use 'global' endpoint for Gemini models - routes to any supported region
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")


# -------------------------
# Google Programmable Search Engine (PSE) Integration
# -------------------------
def google_search(query: str, num: int = 10) -> List[Dict[str, str]]:
    """Call Google Custom Search API (PSE) with retry logic."""
    if not GOOGLE_SEARCH_API_KEY:
        print("[Google Search] No API key set")
        return []
    if not GOOGLE_SEARCH_CX:
        print("[Google Search] No Search Engine ID (CX) set")
        return []
    
    # Custom Search API max is 10 results per request, so we may need pagination
    # For now, we'll request up to 10 results (the API limit)
    num = min(num, 10)
    
    endpoint = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": GOOGLE_SEARCH_CX,
        "q": query,
        "num": num
    }
    
    def _call_google_search():
        r = requests.get(endpoint, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in (data.get("items", []) or [])[:num]:
            results.append({
                "url": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            })
        return results
    
    try:
        return retry_with_backoff(
            _call_google_search,
            RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=30.0),
            operation_name=f"Google Search: {query[:50]}"
        )
    except Exception as e:
        print(f"[Google Search] Error after retries: {e}")
        return []


def vertex_ai_domain_resolution(company_name: str, search_results: List[Dict[str, str]]) -> Dict[str, Any]:
    """Use Vertex AI Gemini to determine official company domain from search results."""
    if not GCP_PROJECT:
        return {"error": "GCP_PROJECT not set"}
    
    # Initialize Vertex AI
    try:
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
    except Exception as e:
        print(f"[Vertex AI] Initialization error: {e}")
        return {"error": f"Vertex AI initialization failed: {str(e)}"}
    
    system_prompt = (
        "You are a domain resolution expert. Your task is to identify the official company domain "
        "from search results.\n\n"
        "Given a company name and search results, determine the official domain (e.g., example.com).\n"
        "Consider:\n"
        "- Official company websites (not third-party listings)\n"
        "- Main domain (not subdomains like blog.example.com)\n"
        "- Most authoritative result from search\n"
        "- Confidence based on how clear the match is\n\n"
        "Return STRICT JSON only with domain, confidence, and rationale."
    )
    
    schema = {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "The official company domain (e.g., 'example.com' without protocol or www)"
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "Confidence level in the domain determination"
            },
            "rationale": {
                "type": "string",
                "description": "Brief explanation of why this domain was selected"
            },
        },
        "required": ["domain", "confidence", "rationale"],
    }
    
    user_prompt = f"""Analyze the following search results to determine the official company domain:

Company Name: {company_name}

Search Results:
{json.dumps(search_results, indent=2)}

Return valid JSON with all required fields."""
    
    def _call_vertex_ai():
        try:
            # Use gemini-2.5-flash with global endpoint (stable model, avoiding preview model hang issues)
            model = GenerativeModel(model_name="gemini-2.5-flash")
            print(f"[Vertex AI] Calling Gemini 2.5 Flash...")
            
            # Generate response
            # Combine system prompt and user prompt since system_instruction may not be available
            full_prompt = f"{system_prompt}\n\n{user_prompt}"
            response = model.generate_content(
                full_prompt,
                generation_config=GenerationConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=schema,
                )
            )
            
            if not response or not response.text:
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
            
            # Check if content is empty after stripping
            if not content.strip():
                raise EmptyLLMResponseError("Empty content after stripping markdown")
            
            result = json.loads(content)
            
            # Validate required fields
            if "domain" not in result:
                result["domain"] = ""
            if "confidence" not in result:
                result["confidence"] = "low"
            if "rationale" not in result:
                result["rationale"] = "Domain resolution completed but rationale was missing from response."
            
            # Validate confidence enum values
            if result.get("confidence") not in ["high", "medium", "low"]:
                result["confidence"] = "medium"
                print(f"[Vertex AI] ⚠️  Invalid confidence value, defaulting to 'medium'")
            
            print(f"[Vertex AI] ✅ Successfully resolved domain")
            return result
            
        except Exception as e:
            print(f"[Vertex AI] Error: {e}")
            raise
    
    try:
        return retry_with_backoff(
            _call_vertex_ai,
            RetryConfig(max_attempts=3, base_delay_seconds=2.0, max_delay_seconds=60.0),
            operation_name="Vertex AI domain resolution"
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
        # Perform Google Custom Search
        search_query = f"{company_name} official website"
        print(f"[CompanyDomainLookup] Performing Google Search: {search_query}")
        search_results = google_search(search_query, num=10)
        
        if not search_results:
            print(f"[CompanyDomainLookup] No search results found")
            return jsonify({"status": "no_results", "message": "No search results found"}), 200, headers
        
        print(f"[CompanyDomainLookup] Found {len(search_results)} search results")
        print(f"[CompanyDomainLookup] DEBUG: Search results: {json.dumps(search_results, indent=2)}")
        
        # Use Vertex AI to determine domain
        print(f"[CompanyDomainLookup] Calling Vertex AI for domain resolution")
        llm_result = vertex_ai_domain_resolution(company_name, search_results)
        print(f"[CompanyDomainLookup] DEBUG: LLM result: {json.dumps(llm_result, indent=2)}")
        
        if "error" in llm_result:
            print(f"[CompanyDomainLookup] LLM error: {llm_result['error']}")
            return jsonify({"status": "error", "error": llm_result["error"]}), 200, headers
        
        domain = llm_result.get("domain", "").strip()
        confidence = llm_result.get("confidence", "low")
        rationale = llm_result.get("rationale", "")
        
        if not domain:
            print(f"[CompanyDomainLookup] No domain determined by LLM")
            return jsonify({"status": "no_domain", "message": "LLM could not determine domain"}), 200, headers
        
        # Clean domain (remove protocol, www, trailing slashes)
        domain = domain.replace("https://", "").replace("http://", "").replace("www.", "")
        domain = domain.split("/")[0].strip()
        
        print(f"[CompanyDomainLookup] Determined domain: {domain} (confidence: {confidence})")
        
        # Update Firestore job document
        job_ref = db.collection("jobs").document(job_id)
        job_doc = job_ref.get()
        
        if not job_doc.exists:
            print(f"[CompanyDomainLookup] Job {job_id} not found in Firestore")
            return jsonify({"status": "error", "error": "Job not found"}), 404, headers
        
        # Update input.company_domain and input.company_domain_confidence
        job_ref.update({
            "input.company_domain": domain,
            "input.company_domain_confidence": confidence,
        })
        
        print(f"[CompanyDomainLookup] Successfully updated job {job_id} with domain: {domain}")
        
        return jsonify({
            "status": "success",
            "domain": domain,
            "confidence": confidence,
            "rationale": rationale,
        }), 200, headers
        
    except Exception as e:
        print(f"[CompanyDomainLookup] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        # Don't fail the job - domain lookup is optional
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 200, headers  # Return 200 so it doesn't appear as a failure
