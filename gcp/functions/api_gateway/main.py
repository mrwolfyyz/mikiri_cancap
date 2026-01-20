"""
API Gateway Cloud Function

Handles:
- POST /investigate-skiptrace - Create new skip trace investigation job
- POST /investigate-origination - Create new origination investigation job
- GET /jobs/{job_id} - Poll job status
- GET /get_markdown/{job_id} - Get markdown reports
- POST /address-verification - Address verification for business auto loan applications
- POST /chat_handler - Chat handler for skip trace
- POST /chat_handler_origination - Chat handler for origination
- GET /health - Health check

Note: This version has hardcoded values removed for deployment flexibility.
Configuration is loaded from environment variables set by Terraform.
"""
import json
import os
import re
import uuid
import requests
from datetime import datetime
from typing import Any

import functions_framework
from flask import Request, jsonify
from google.cloud import firestore
from google.cloud import workflows_v1
from google.cloud.workflows import executions_v1
from firebase_admin import initialize_app, auth

# Import retry utilities (local copy for deployment)
from retry_utils import retry_with_backoff, RetryConfig

# Initialize Firebase Admin SDK (same pattern as report_generator functions)
try:
    initialize_app()
except ValueError:
    # Already initialized (can happen in some environments)
    pass

# Initialize clients
db = firestore.Client()

# =============================================================================
# Configuration - Loaded from environment variables (set by Terraform)
# =============================================================================
PROJECT_ID = os.environ.get("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", "northamerica-northeast1")

# Workflow names (must match Terraform-deployed workflow names)
SKIPTRACE_WORKFLOW_NAME = os.environ.get("SKIPTRACE_WORKFLOW_NAME", "investigate-skiptrace")
ORIGINATION_WORKFLOW_NAME = os.environ.get("ORIGINATION_WORKFLOW_NAME", "investigate-origination")

# Function URLs (injected by Terraform from function outputs)
CHAT_HANDLER_URL = os.environ.get("CHAT_HANDLER_URL")
CHAT_HANDLER_ORIGINATION_URL = os.environ.get("CHAT_HANDLER_ORIGINATION_URL")
ADDRESS_VERIFICATION_URL = os.environ.get("ADDRESS_VERIFICATION_URL")

# CORS configuration (restrict in production)
CORS_ALLOWED_ORIGINS = os.environ.get("CORS_ALLOWED_ORIGINS", "*")


def verify_firebase_token(request: Request) -> tuple[str | None, dict | None]:
    """
    Verify Firebase ID token from Authorization header.
    Returns: (user_id, None) on success or (None, error_dict) on failure
    """
    auth_header = request.headers.get("Authorization", "")
    
    if not auth_header.startswith("Bearer "):
        return None, {"error": "Authentication required"}
    
    token = auth_header.split("Bearer ")[1]
    
    try:
        decoded_token = auth.verify_id_token(token)
        user_id = decoded_token.get("uid")
        return user_id, None
    except auth.InvalidIdTokenError:
        print("Invalid token received")  # Log internally
        return None, {"error": "Authentication failed. Please refresh the page."}
    except auth.ExpiredIdTokenError:
        print("Expired token received")  # Log internally
        return None, {"error": "Session expired. Please refresh the page."}
    except Exception as e:
        print(f"Token verification error: {e}")  # Log internally
        return None, {"error": "Authentication failed. Please refresh the page."}


def validate_email(email: str) -> tuple[bool, str]:
    """Validate email format."""
    if not email:
        return False, "Email is required"
    if len(email) < 5 or len(email) > 254:
        return False, "Email must be 5-254 characters"
    pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
    if not re.match(pattern, email):
        return False, "Invalid email format"
    return True, ""


def validate_full_name(name: str) -> tuple[bool, str]:
    """Validate full name."""
    if not name:
        return False, "Full name is required"
    if len(name) < 2 or len(name) > 100:
        return False, "Full name must be 2-100 characters"
    # Must contain at least first and last name (space-separated)
    parts = name.strip().split()
    if len(parts) < 2:
        return False, "Must contain first and last name"
    return True, ""


def validate_city(city: str) -> tuple[bool, str]:
    """Validate city (optional)."""
    if not city:
        return True, ""  # Optional field
    if len(city) < 2 or len(city) > 100:
        return False, "City must be 2-100 characters"
    return True, ""


def create_job(email: str, full_name: str, city: str, drive_folder_id: str = None, company_name: str = None, user_id: str = None) -> str:
    """Create a new job in Firestore."""
    job_id = uuid.uuid4().hex[:12]
    now = datetime.utcnow()
    
    job_data = {
        "status": "pending",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "user_id": user_id,  # Store user_id for ownership verification
        "input": {
            "email": email,
            "full_name": full_name,
            "city": city or None,
            "drive_folder_id": drive_folder_id,
            "company_name": company_name or None,
        },
        "result": None,
        "result_summary": None,
        "partial_failure": False,
        "errors": {},
        "report_urls": {},
        "reports_generated": False,
        "error": None,
    }
    
    db.collection("jobs").document(job_id).set(job_data)
    return job_id


def trigger_workflow(job_id: str, email: str, full_name: str, city: str, company_name: str = None, workflow_name: str = None) -> str:
    """Trigger the investigate workflow."""
    if workflow_name is None:
        workflow_name = SKIPTRACE_WORKFLOW_NAME
    
    if not PROJECT_ID:
        raise ValueError("GCP_PROJECT environment variable not set")
    
    client = executions_v1.ExecutionsClient()
    
    parent = f"projects/{PROJECT_ID}/locations/{LOCATION}/workflows/{workflow_name}"
    
    workflow_input = {
        "job_id": job_id,
        "email": email,
        "full_name": full_name,
        "city": city or "",
    }
    
    # Add company_name if provided
    if company_name:
        workflow_input["company_name"] = company_name
    
    execution = executions_v1.Execution(
        argument=json.dumps(workflow_input)
    )
    
    response = client.create_execution(parent=parent, execution=execution)
    return response.name


def get_job(job_id: str) -> dict[str, Any] | None:
    """Get job from Firestore."""
    doc = db.collection("jobs").document(job_id).get()
    if not doc.exists:
        return None
    return doc.to_dict()


def format_job_response(job_id: str, job: dict[str, Any]) -> dict[str, Any]:
    """Format job data for API response."""
    response = {
        "job_id": job_id,
        "status": job.get("status"),
        "created_at": job.get("created_at").isoformat() + "Z" if job.get("created_at") else None,
    }
    
    if job.get("started_at"):
        response["started_at"] = job["started_at"].isoformat() + "Z"
    
    if job.get("status") == "post_processing":
        response["message"] = "Investigation complete, generating reports..."
    
    if job.get("status") == "complete":
        if job.get("completed_at"):
            response["completed_at"] = job["completed_at"].isoformat() + "Z"
            elapsed = (job["completed_at"] - job["created_at"]).total_seconds()
            response["elapsed_seconds"] = int(elapsed)
        
        response["input"] = job.get("input")
        response["result_summary"] = job.get("result_summary")
        response["partial_failure"] = job.get("partial_failure", False)
        
        if job.get("partial_failure") and job.get("errors"):
            response["errors"] = job["errors"]
        
        if job.get("report_urls"):
            response["report_urls"] = job["report_urls"]
    
    if job.get("status") == "failed":
        response["error"] = job.get("error", "Unknown error")
    
    return response


def get_cors_headers(request: Request):
    """Get CORS headers based on configuration and request origin."""
    origin = request.headers.get("Origin", "")
    
    # If CORS_ALLOWED_ORIGINS is "*", allow all origins
    if CORS_ALLOWED_ORIGINS == "*":
        return {"Access-Control-Allow-Origin": "*"}
    
    # Split comma-separated origins and check if request origin matches
    allowed_origins = [o.strip() for o in CORS_ALLOWED_ORIGINS.split(",")]
    
    # If request origin is in allowed list, return it (browser requires exact match)
    if origin in allowed_origins:
        return {"Access-Control-Allow-Origin": origin}
    
    # If no match and not "*", return first allowed origin as fallback
    # (or could return "*" if you want to allow all)
    return {"Access-Control-Allow-Origin": allowed_origins[0] if allowed_origins else "*"}


@functions_framework.http
def main(request: Request):
    """Main HTTP handler."""
    # Enable CORS
    if request.method == "OPTIONS":
        origin = request.headers.get("Origin", "")
        if CORS_ALLOWED_ORIGINS == "*":
            allow_origin = "*"
        else:
            allowed_origins = [o.strip() for o in CORS_ALLOWED_ORIGINS.split(",")]
            allow_origin = origin if origin in allowed_origins else (allowed_origins[0] if allowed_origins else "*")
        
        headers = {
            "Access-Control-Allow-Origin": allow_origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Max-Age": "3600",
        }
        return ("", 204, headers)
    
    headers = get_cors_headers(request)
    
    path = request.path
    
    # POST /investigate-skiptrace
    if request.method == "POST" and path == "/investigate-skiptrace":
        # Verify authentication
        user_id, auth_error = verify_firebase_token(request)
        if auth_error:
            return jsonify(auth_error), 401, headers
        
        try:
            data = request.get_json() or {}
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400, headers
        
        email = (data.get("email") or "").strip()
        full_name = (data.get("full_name") or "").strip()
        city = (data.get("city") or "").strip()
        drive_folder_id = (data.get("drive_folder_id") or "").strip()
        company_name = (data.get("company_name") or "").strip() or None
        
        # Validate
        errors = []
        
        # Optional: Validate drive_folder_id format if provided
        if drive_folder_id and (len(drive_folder_id) < 10 or len(drive_folder_id) > 100 or not drive_folder_id.replace('_', '').replace('-', '').isalnum()):
            errors.append({"field": "drive_folder_id", "message": "Invalid Drive folder ID format"})
        
        valid, msg = validate_email(email)
        if not valid:
            errors.append({"field": "email", "message": msg})
        
        valid, msg = validate_full_name(full_name)
        if not valid:
            errors.append({"field": "full_name", "message": msg})
        
        valid, msg = validate_city(city)
        if not valid:
            errors.append({"field": "city", "message": msg})
        
        if errors:
            return jsonify({"error": "validation_error", "details": errors}), 400, headers
        
        # Create job with user_id
        job_id = create_job(email, full_name, city, drive_folder_id, company_name, user_id=user_id)
        
        # Trigger skip trace workflow (async)
        try:
            trigger_workflow(job_id, email, full_name, city, company_name, workflow_name=SKIPTRACE_WORKFLOW_NAME)
        except Exception as e:
            # Update job as failed
            db.collection("jobs").document(job_id).update({
                "status": "failed",
                "error": f"Failed to start workflow: {str(e)}"
            })
            return jsonify({"error": f"Failed to start investigation: {str(e)}"}), 500, headers
        
        return jsonify({"job_id": job_id}), 202, headers
    
    # POST /investigate-origination
    if request.method == "POST" and path == "/investigate-origination":
        # Verify authentication
        user_id, auth_error = verify_firebase_token(request)
        if auth_error:
            return jsonify(auth_error), 401, headers
        
        try:
            data = request.get_json() or {}
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400, headers
        
        email = (data.get("email") or "").strip()
        full_name = (data.get("full_name") or "").strip()
        city = (data.get("city") or "").strip()
        drive_folder_id = (data.get("drive_folder_id") or "").strip()
        company_name = (data.get("company_name") or "").strip() or None
        
        # Validate
        errors = []
        
        # Optional: Validate drive_folder_id format if provided
        if drive_folder_id and (len(drive_folder_id) < 10 or len(drive_folder_id) > 100 or not drive_folder_id.replace('_', '').replace('-', '').isalnum()):
            errors.append({"field": "drive_folder_id", "message": "Invalid Drive folder ID format"})
        
        valid, msg = validate_email(email)
        if not valid:
            errors.append({"field": "email", "message": msg})
        
        valid, msg = validate_full_name(full_name)
        if not valid:
            errors.append({"field": "full_name", "message": msg})
        
        valid, msg = validate_city(city)
        if not valid:
            errors.append({"field": "city", "message": msg})
        
        if errors:
            return jsonify({"error": "validation_error", "details": errors}), 400, headers
        
        # Create job with user_id
        job_id = create_job(email, full_name, city, drive_folder_id, company_name, user_id=user_id)
        
        # Trigger origination workflow (async)
        try:
            trigger_workflow(job_id, email, full_name, city, company_name, workflow_name=ORIGINATION_WORKFLOW_NAME)
        except Exception as e:
            # Update job as failed
            db.collection("jobs").document(job_id).update({
                "status": "failed",
                "error": f"Failed to start workflow: {str(e)}"
            })
            return jsonify({"error": f"Failed to start investigation: {str(e)}"}), 500, headers
        
        return jsonify({"job_id": job_id}), 202, headers
    
    # GET /jobs/{job_id}
    if request.method == "GET" and path.startswith("/jobs/"):
        job_id = path.split("/jobs/")[-1].strip("/")
        
        if not job_id:
            return jsonify({"error": "Job ID required"}), 400, headers
        
        job = get_job(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404, headers
        
        # Backward compatibility: If job has user_id, require auth and verify ownership
        # If job has no user_id (old jobs from main frontend), allow without auth
        job_user_id = job.get("user_id")
        if job_user_id is not None:
            # Job has user_id - require authentication
            user_id, auth_error = verify_firebase_token(request)
            if auth_error:
                return jsonify(auth_error), 401, headers
            
            # CRITICAL: Verify ownership
            if job_user_id != user_id:
                return jsonify({"error": "Unauthorized"}), 403, headers
        
        return jsonify(format_job_response(job_id, job)), 200, headers
    
    # GET /get_markdown/{job_id}
    if request.method == "GET" and path.startswith("/get_markdown/"):
        job_id = path.split("/get_markdown/")[-1].strip("/")
        
        if not job_id:
            return jsonify({"error": "Job ID required"}), 400, headers
        
        try:
            job_doc = db.collection("jobs").document(job_id).get()
            if not job_doc.exists:
                return jsonify({"error": "Job not found"}), 404, headers
            
            job_data = job_doc.to_dict()
            
            # Backward compatibility: If job has user_id, require auth and verify ownership
            # If job has no user_id (old jobs from main frontend), allow without auth
            job_user_id = job_data.get("user_id")
            if job_user_id is not None:
                # Job has user_id - require authentication
                user_id, auth_error = verify_firebase_token(request)
                if auth_error:
                    return jsonify(auth_error), 401, headers
                
                # CRITICAL: Verify ownership
                if job_user_id != user_id:
                    return jsonify({"error": "Unauthorized"}), 403, headers
            
            markdown_reports = job_data.get("markdown_reports", {})
            
            if not markdown_reports:
                return jsonify({"error": "Markdown reports not available for this job"}), 404, headers
            
            # Return all available markdown reports (works for both skip trace and origination)
            # Skip trace has: identity, skiptrace
            # Origination has: summary, identity, corporate, litigation, regulator
            return jsonify(markdown_reports), 200, headers
        except Exception as e:
            return jsonify({"error": f"Failed to retrieve markdown: {str(e)}"}), 500, headers
    
    # POST /address-verification
    if request.method == "POST" and path == "/address-verification":
        # Verify authentication
        user_id, auth_error = verify_firebase_token(request)
        if auth_error:
            return jsonify(auth_error), 401, headers
        
        # Check that ADDRESS_VERIFICATION_URL is configured
        if not ADDRESS_VERIFICATION_URL:
            return jsonify({"error": "Address verification service not configured"}), 500, headers
        
        try:
            data = request.get_json() or {}
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400, headers
        
        # Accept either separate fields or combined address (for backward compatibility)
        street_address = (data.get("street_address") or "").strip()
        suite_unit = (data.get("suite_unit") or "").strip()
        city = (data.get("city") or "").strip()
        province = (data.get("province") or "").strip()
        postal_code = (data.get("postal_code") or "").strip()
        address = (data.get("address") or "").strip()
        business_name = (data.get("business_name") or "").strip()
        
        # Validate required fields
        if not address and not (street_address and city and province):
            return jsonify({"error": "address is required (or provide street_address, city, province)"}), 400, headers
        if not business_name:
            return jsonify({"error": "business_name is required"}), 400, headers
        
        # Build request payload - forward all fields to address_verification function
        payload = {"business_name": business_name}
        if street_address and city and province:
            # Use separate fields if provided
            payload["street_address"] = street_address
            if suite_unit:
                payload["suite_unit"] = suite_unit
            payload["city"] = city
            payload["province"] = province
            if postal_code:
                payload["postal_code"] = postal_code
        else:
            # Fallback to combined address
            payload["address"] = address
        
        def _call_address_verification():
            response = requests.post(
                ADDRESS_VERIFICATION_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=60  # Address verification may take longer due to LLM analysis
            )
            response.raise_for_status()
            return response
        
        try:
            response = retry_with_backoff(
                _call_address_verification,
                RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=10.0),
                operation_name="Address verification API call"
            )
            return jsonify(response.json()), response.status_code, headers
        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"Address verification failed: {str(e)}"}), 500, headers
    
    # POST /chat_handler
    if request.method == "POST" and path == "/chat_handler":
        # Verify authentication
        user_id, auth_error = verify_firebase_token(request)
        if auth_error:
            return jsonify(auth_error), 401, headers
        
        # Check that CHAT_HANDLER_URL is configured
        if not CHAT_HANDLER_URL:
            return jsonify({"error": "Chat handler service not configured"}), 500, headers
        
        try:
            data = request.get_json() or {}
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400, headers
        
        # If job_id is provided, verify ownership
        job_id = data.get("job_id")
        if job_id:
            job_doc = db.collection("jobs").document(job_id).get()
            if not job_doc.exists:
                return jsonify({"error": "Job not found"}), 404, headers
            
            job_data = job_doc.to_dict()
            if job_data.get("user_id") != user_id:
                return jsonify({"error": "Unauthorized"}), 403, headers
        
        def _call_chat_handler():
            response = requests.post(
                CHAT_HANDLER_URL,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=120  # Increased to handle longer conversations with more history
            )
            response.raise_for_status()
            return response
        
        try:
            response = retry_with_backoff(
                _call_chat_handler,
                RetryConfig(max_attempts=2, base_delay_seconds=1.0, max_delay_seconds=5.0),
                operation_name="Chat handler API call"
            )
            return jsonify(response.json()), response.status_code, headers
        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"Chat handler failed: {str(e)}"}), 500, headers
    
    # POST /chat_handler_origination
    if request.method == "POST" and path == "/chat_handler_origination":
        # Verify authentication
        user_id, auth_error = verify_firebase_token(request)
        if auth_error:
            return jsonify(auth_error), 401, headers
        
        # Check that CHAT_HANDLER_ORIGINATION_URL is configured
        if not CHAT_HANDLER_ORIGINATION_URL:
            return jsonify({"error": "Chat handler origination service not configured"}), 500, headers
        
        try:
            data = request.get_json() or {}
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400, headers
        
        # If job_id is provided, verify ownership
        job_id = data.get("job_id")
        if job_id:
            job_doc = db.collection("jobs").document(job_id).get()
            if not job_doc.exists:
                return jsonify({"error": "Job not found"}), 404, headers
            
            job_data = job_doc.to_dict()
            if job_data.get("user_id") != user_id:
                return jsonify({"error": "Unauthorized"}), 403, headers
        
        def _call_chat_handler_origination():
            response = requests.post(
                CHAT_HANDLER_ORIGINATION_URL,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=120  # Increased to handle longer conversations with more history
            )
            response.raise_for_status()
            return response
        
        try:
            response = retry_with_backoff(
                _call_chat_handler_origination,
                RetryConfig(max_attempts=2, base_delay_seconds=1.0, max_delay_seconds=5.0),
                operation_name="Chat handler origination API call"
            )
            return jsonify(response.json()), response.status_code, headers
        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"Chat handler origination failed: {str(e)}"}), 500, headers
    
    # Health check
    if request.method == "GET" and path in ("/health", "/"):
        return jsonify({
            "status": "healthy",
            "service": "api_gateway",
            "project": PROJECT_ID or "not_configured",
            "region": LOCATION
        }), 200, headers
    
    return jsonify({"error": "Not found"}), 404, headers
