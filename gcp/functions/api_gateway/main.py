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
- POST /extension/prefill-session - Chrome extension: create one-time prefill (shared secret)
- POST /prefill-session/redeem - Browser: redeem prefill token (no PII in URL)
- GET /health - Health check

Note: This version has hardcoded values removed for deployment flexibility.
Configuration is loaded from environment variables set by Terraform.
"""

import base64
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import functions_framework
import requests
from firebase_admin import app_check, auth, initialize_app
from flask import Request, Response, jsonify
from google.cloud import firestore
from google.cloud.workflows import executions_v1

# Import retry utilities (local copy for deployment)
from llm_input_validators import (
    MAX_CITY_LEN,
    MAX_FULL_NAME_LEN,
    PROVINCE_NAMES,
    normalize_and_validate_allowlist_text,
    normalize_province_for_query,
)
from retry_utils import RetryConfig, retry_with_backoff

# Canadian province/territory codes (same keys as PROVINCE_NAMES); kept for tests and callers.
VALID_PROVINCES = list(PROVINCE_NAMES.keys())

# Configure logging
logger = logging.getLogger(__name__)

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

# CORS configuration (must be explicitly configured per environment)
CORS_ALLOWED_ORIGINS = (os.environ.get("CORS_ALLOWED_ORIGINS") or "").strip()
if not CORS_ALLOWED_ORIGINS:
    raise ValueError(
        "CORS_ALLOWED_ORIGINS must be explicitly configured. Use '*' only for deliberate development usage."
    )

# Job document retention (aligned with report generators and frontend chat TTL)
JOB_RETENTION_DAYS = 7

# Chrome extension → web app prefill (Firestore collection prefill_sessions)
EXTENSION_PREFILL_SECRET = (os.environ.get("EXTENSION_PREFILL_SECRET") or "").strip()
HISTORY_TOKEN_SECRET = (os.environ.get("HISTORY_TOKEN_SECRET") or "").strip()
if not HISTORY_TOKEN_SECRET:
    print("[api_gateway] WARNING: HISTORY_TOKEN_SECRET not set — page tokens are unsigned")
PREFILL_SESSION_TTL_MINUTES = int(os.environ.get("PREFILL_SESSION_TTL_MINUTES") or "10")
PREFILL_SESSION_COLLECTION = "prefill_sessions"

REQUIRE_SSO = os.environ.get("REQUIRE_SSO") == "true"
APP_CHECK_ENFORCED = os.environ.get("APP_CHECK_ENFORCED") == "true"
ALLOWED_EMAIL_DOMAINS = {d.strip().lower() for d in os.environ.get("ALLOWED_EMAIL_DOMAINS", "").split(",") if d.strip()}

# Frontend base URL for absolute links in CSV exports (empty = relative path fallback)
FRONTEND_RESULTS_BASE_URL = os.environ.get("FRONTEND_RESULTS_BASE_URL", "").strip().rstrip("/")


# =============================================================================
# Authentication & Validation Helpers
# =============================================================================


def verify_firebase_token_with_claims(request: Request) -> tuple[str | None, dict[str, Any] | None, dict | None]:
    """
    Verify Firebase ID token from Authorization header.
    When REQUIRE_SSO is true, additionally enforce Google sign-in provider and domain allowlist.
    When APP_CHECK_ENFORCED is true, additionally verify X-Firebase-AppCheck header.
    Returns: (user_id, decoded_claims, None) on success or (None, None, error_dict) on failure
    """
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        return None, None, {"error": "Authentication required"}

    token = auth_header.split("Bearer ")[1]

    try:
        decoded = auth.verify_id_token(token)
    except (auth.InvalidIdTokenError, auth.ExpiredIdTokenError):
        logger.warning("Firebase token verification failed")
        return None, None, {"error": "Authentication failed. Please refresh the page."}
    except Exception as e:
        logger.error("Token verification error: %s", e)
        return None, None, {"error": "Authentication failed. Please refresh the page."}

    user_id = decoded.get("uid")

    if REQUIRE_SSO:
        provider = decoded.get("firebase", {}).get("sign_in_provider")
        if provider != "google.com":
            logger.warning("Non-Google sign-in provider rejected: %s", provider)
            return None, None, {"error": "SSO required"}

        if not decoded.get("email_verified"):
            logger.warning("Unverified email rejected for uid=%s", user_id)
            return None, None, {"error": "Email not verified"}

        email = (decoded.get("email") or "").lower()
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if not ALLOWED_EMAIL_DOMAINS:
            logger.error("SSO enabled without ALLOWED_EMAIL_DOMAINS configured")
            return None, None, {"error": "Authentication configuration error"}
        if domain not in ALLOWED_EMAIL_DOMAINS:
            logger.warning("Disallowed email domain rejected: %s", domain)
            return None, None, {"error": "Account not permitted"}

    if APP_CHECK_ENFORCED:
        ac_token = request.headers.get("X-Firebase-AppCheck", "")
        if not ac_token:
            logger.warning("Missing App Check token on authenticated request")
            return None, None, {"error": "App Check token required"}
        try:
            app_check.verify_token(ac_token)
        except Exception as e:
            logger.warning("App Check verification failed: %s", e)
            return None, None, {"error": "App Check failed"}

    return user_id, decoded, None


def verify_firebase_token(request: Request) -> tuple[str | None, dict | None]:
    """
    Verify Firebase ID token from Authorization header.
    Returns: (user_id, None) on success or (None, error_dict) on failure
    """
    user_id, _decoded, error = verify_firebase_token_with_claims(request)
    return user_id, error


def _user_profile_from_claims(decoded: dict[str, Any] | None) -> dict[str, str | None]:
    """Extract displayable user fields from verified Firebase token claims."""
    if not decoded:
        return {"user_email": None, "user_name": None}

    email = (decoded.get("email") or "").strip().lower() or None
    name = (decoded.get("name") or decoded.get("display_name") or "").strip() or None
    return {"user_email": email, "user_name": name}


def _request_args(request: Request) -> dict[str, str]:
    """Return query parameters as a plain dict for Flask requests and tests."""
    args = getattr(request, "args", None)
    if not args:
        return {}
    if hasattr(args, "to_dict"):
        return args.to_dict(flat=True)
    return dict(args)


def _isoformat(value: Any) -> str | None:
    """Format Firestore datetime-like values for JSON responses."""
    if not value:
        return None
    if hasattr(value, "isoformat"):
        suffix = "Z" if getattr(value, "tzinfo", None) is None else ""
        return value.isoformat() + suffix
    return str(value)


def _json_safe(value: Any) -> Any:
    """Convert Firestore values to JSON-safe values."""
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return _isoformat(value)
    return value


def _parse_date_param(value: str | None, end_of_day: bool = False) -> datetime | None:
    """Parse YYYY-MM-DD or ISO-ish dates from query params."""
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        parsed = datetime.fromisoformat(normalized)
        if end_of_day:
            return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        return parsed
    return datetime.fromisoformat(normalized.replace("Z", "+00:00")).replace(tzinfo=None)


def _history_filter_signature(params: dict[str, str], limit: int) -> str:
    """Stable signature used to keep page tokens bound to one filtered result set."""
    user_filter = (params.get("user_id") or "").strip()
    user_field = "user_email" if "@" in user_filter else "user_id"
    cars_ref = (params.get("cars_reference_number") or "").strip().upper() or None
    normalized = {
        "start_date": _isoformat(_parse_date_param(params.get("start_date"))),
        "end_date": _isoformat(_parse_date_param(params.get("end_date"), end_of_day=True)),
        "user_field": user_field if user_filter else None,
        "user_filter": user_filter.lower() if user_field == "user_email" else user_filter,
        "cars_reference_number": cars_ref,
        "cars_mode": "prefix" if cars_ref else None,
        "limit": limit,
    }
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _encode_history_page_token(
    job_id: str,
    filter_signature: str,
    *,
    created_at: Any = None,
    cars_ref: str | None = None,
) -> str:
    iat = int(datetime.now(UTC).timestamp())
    inner: dict[str, Any] = {
        "filter_signature": filter_signature,
        "iat": iat,
        "job_id": job_id,
    }
    if cars_ref is not None:
        inner["cars_ref"] = cars_ref
    else:
        inner["created_at"] = _isoformat(created_at)
    inner_raw = json.dumps(inner, sort_keys=True, separators=(",", ":")).encode("utf-8")
    mac = hmac.new(HISTORY_TOKEN_SECRET.encode("utf-8"), inner_raw, hashlib.sha256).hexdigest()
    payload = {**inner, "mac": mac}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_history_page_token(token: str, expected_filter_signature: str) -> dict[str, Any]:
    try:
        padded = token + ("=" * (-len(token) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid history page token") from exc

    if not isinstance(payload, dict):
        raise ValueError("Invalid history page token")

    mac_from_token = payload.pop("mac", None)
    if not isinstance(mac_from_token, str):
        raise ValueError("Invalid history page token")

    inner_raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected_mac = hmac.new(HISTORY_TOKEN_SECRET.encode("utf-8"), inner_raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac_from_token, expected_mac):
        raise ValueError("Invalid history page token")

    iat = payload.get("iat")
    if not isinstance(iat, (int, float)) or (datetime.now(UTC).timestamp() - iat) > 3600:
        raise ValueError("Invalid history page token")

    if payload.get("filter_signature") != expected_filter_signature:
        raise ValueError("Invalid history page token")

    job_id = (payload.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("Invalid history page token")

    cars_ref = payload.get("cars_ref")
    if cars_ref is not None:
        if not isinstance(cars_ref, str) or not cars_ref:
            raise ValueError("Invalid history page token")
        return {"cars_ref": cars_ref, "job_id": job_id}

    created_at = _parse_date_param(payload.get("created_at"))
    if not created_at:
        raise ValueError("Invalid history page token")
    return {"created_at": created_at, "job_id": job_id}


def _in_date_range(ts: Any, start: Any, end: Any) -> bool:
    """Return True when ts falls within [start, end]; None bounds are ignored."""
    if ts is None:
        return start is None and end is None
    if not isinstance(ts, datetime):
        try:
            ts = _parse_date_param(ts)
        except Exception:
            return True
    if start and ts < start:
        return False
    if end and ts > end:
        return False
    return True


def _feedback_entries(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Return feedback history, falling back to the legacy single feedback field."""
    entries = job.get("feedback_entries") or []
    if entries:
        return entries
    legacy = job.get("feedback")
    return [legacy] if legacy else []


def _check_feedback_rate_limit(entries: list[dict[str, Any]], user_id: str) -> bool:
    """Return True if user is within feedback rate limit, False if exceeded. No extra Firestore read.

    Uses POSIX timestamps for comparison so both timezone-aware Firestore datetimes and
    timezone-naive datetimes (tests) are handled without TypeError.
    """
    window_start_ts = (datetime.now(UTC) - timedelta(hours=FEEDBACK_RATE_LIMIT_WINDOW_HOURS)).timestamp()
    recent = [
        e
        for e in entries
        if e.get("user_id") == user_id
        and hasattr(e.get("submitted_at"), "timestamp")
        and e["submitted_at"].timestamp() >= window_start_ts
    ]
    return len(recent) < FEEDBACK_RATE_LIMIT_MAX


def _feedback_summary(job: dict[str, Any]) -> dict[str, Any] | None:
    entries = _feedback_entries(job)
    if not entries:
        return None
    latest = entries[-1]
    comment = (latest.get("comment") or "").strip()
    return {
        "rating": latest.get("rating"),
        "comment_summary": comment[:140] + ("..." if len(comment) > 140 else ""),
        "submitted_at": _isoformat(latest.get("submitted_at")),
        "user_id": latest.get("user_id"),
        "count": len(entries),
    }


_FORMULA_CHARS = frozenset("=+-@\t\r")


def _csv_safe(value: str | None) -> str:
    s = value or ""
    return f"'{s}" if s and s[0] in _FORMULA_CHARS else s


def _is_skiptrace_job(job: dict[str, Any]) -> bool:
    """Identify skiptrace jobs, including older docs created before workflow_type was stored."""
    if job.get("workflow_type") == "skiptrace":
        return True
    input_data = job.get("input") or {}
    return bool(input_data.get("cars_reference_number"))


def _format_history_row(job_id: str, job: dict[str, Any]) -> dict[str, Any]:
    input_data = job.get("input") or {}
    feedback = _feedback_summary(job)
    user_email = job.get("user_email")
    user_name = job.get("user_name")
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "created_at": _isoformat(job.get("created_at")),
        "completed_at": _isoformat(job.get("completed_at")),
        "user_id": job.get("user_id"),
        "user_email": user_email,
        "user_name": user_name,
        "user_display": user_email or user_name or job.get("user_id"),
        "full_name": input_data.get("full_name"),
        "cars_reference_number": input_data.get("cars_reference_number"),
        "feedback": feedback,
        "feedback_count": feedback["count"] if feedback else 0,
        "results_url": f"{FRONTEND_RESULTS_BASE_URL + '/' if FRONTEND_RESULTS_BASE_URL else ''}results.html?job_id={job_id}&workflow=skiptrace",
    }


def _id_token_for_url(target_url: str) -> str:
    """Google identity token for invoking a Gen2 function / Cloud Run URL (service-to-service)."""
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    audience = target_url.rstrip("/")
    auth_req = Request()
    return id_token.fetch_id_token(auth_req, audience)


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
    """
    Validate full name for LLM-bound fields.
    Order: shared NFKC + whitespace collapse + allow-list + length cap, then api_gateway-only
    two-word rule. On success returns (True, normalized_name).
    """
    if not name or not str(name).strip():
        return False, "Full name is required"
    normalized = normalize_and_validate_allowlist_text(str(name).strip(), MAX_FULL_NAME_LEN)
    if normalized is None:
        return False, "Full name must contain only letters, spaces, and limited punctuation"
    if len(normalized) < 2:
        return False, "Full name must be 2-200 characters"
    if len(normalized.split()) < 2:
        return False, "Must contain first and last name"
    return True, normalized


def validate_city(city: str) -> tuple[bool, str]:
    """
    Validate city (optional for prefill). On success: (True, normalized_city), or (True, '') if empty.
    """
    if not city or not str(city).strip():
        return True, ""
    normalized = normalize_and_validate_allowlist_text(str(city).strip(), MAX_CITY_LEN)
    if normalized is None:
        return False, "City contains invalid characters"
    if len(normalized) < 2:
        return False, "City must be 2-120 characters"
    return True, normalized


def _province_validation_message(err: str | None) -> str:
    if err == "Invalid province code":
        return "Invalid province. Must be a valid Canadian province code"
    if err == "Invalid province":
        return "Invalid province"
    return "Invalid province"


def validate_province(province: str) -> tuple[bool, str]:
    """Validate province (required): two-letter code or allow-listed free text. Returns (True, normalized)."""
    if not province or not str(province).strip():
        return False, "Province is required"
    norm, err = normalize_province_for_query(str(province).strip())
    if err:
        return False, _province_validation_message(err)
    if norm is None:
        return False, "Invalid province"
    return True, norm


def validate_cars_reference_number(cars_reference_number: str) -> tuple[bool, str]:
    """Validate and normalize CARS reference number. On success returns uppercase value."""
    normalized = (cars_reference_number or "").strip().upper()
    if not normalized:
        return False, "CARS Reference Number is required"
    if not re.fullmatch(r"[A-Z]{5}\d+", normalized):
        return False, "CARS Reference Number must start with 5 letters followed by numbers"
    return True, normalized


def validate_prefill_province_optional(province: str) -> tuple[bool, str]:
    """Province optional; if set, must be a valid code or allow-listed free text."""
    if not province or not str(province).strip():
        return True, ""
    norm, err = normalize_province_for_query(str(province).strip())
    if err:
        return False, _province_validation_message(err)
    if norm is None:
        return False, "Invalid province"
    return True, norm


def validate_prefill_payload(data: dict[str, Any]) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
    """
    Validate prefill payload from Chrome extension (looser than investigation).

    Requires at least one of: full_name, email, city, company_name (mirror extension hasData).
    """
    full_name = (data.get("full_name") or "").strip()
    email = (data.get("email") or "").strip()
    city = (data.get("city") or "").strip()
    company_name = (data.get("company_name") or "").strip()
    province = (data.get("province") or "").strip()

    # Accept legacy camelCase from some clients
    if not full_name and data.get("fullName"):
        full_name = str(data.get("fullName") or "").strip()
    if not company_name and data.get("companyName"):
        company_name = str(data.get("companyName") or "").strip()

    errors: list[dict[str, str]] = []

    if not any([full_name, email, city, company_name]):
        errors.append(
            {"field": "_all", "message": "At least one of full_name, email, city, or company_name is required"}
        )
        return None, errors

    if email:
        valid, msg = validate_email(email)
        if not valid:
            errors.append({"field": "email", "message": msg})

    if full_name:
        if len(full_name) < 2 or len(full_name) > 100:
            errors.append({"field": "full_name", "message": "Full name must be 2-100 characters"})

    if city:
        valid, msg = validate_city(city)
        if not valid:
            errors.append({"field": "city", "message": msg})
        else:
            city = msg

    if company_name and len(company_name) > 200:
        errors.append({"field": "company_name", "message": "Company name must be 200 characters or less"})

    valid, msg = validate_prefill_province_optional(province)
    if not valid:
        errors.append({"field": "province", "message": msg})
    else:
        province = msg

    if errors:
        return None, errors

    return {
        "full_name": full_name,
        "email": email,
        "city": city,
        "company_name": company_name,
        "province": province,
    }, []


def _verify_extension_prefill_secret(request: Request) -> bool:
    """Constant-time compare of X-Extension-Prefill-Secret to EXTENSION_PREFILL_SECRET."""
    if not EXTENSION_PREFILL_SECRET:
        return False
    got = (request.headers.get("X-Extension-Prefill-Secret") or "").strip()
    try:
        return hmac.compare_digest(got.encode("utf-8"), EXTENSION_PREFILL_SECRET.encode("utf-8"))
    except Exception:
        return False


def _prefill_doc_expired(expire_at: Any) -> bool:
    """True if expire_at is in the past (Firestore Timestamp or datetime)."""
    if expire_at is None:
        return False
    try:
        if hasattr(expire_at, "timestamp"):
            return datetime.now(UTC).timestamp() > expire_at.timestamp()
        if isinstance(expire_at, datetime):
            exp = expire_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
            return datetime.now(UTC) > exp
    except Exception:
        return True
    return False


def handle_extension_prefill_session(request: Request, headers: dict):
    """POST /extension/prefill-session — authenticated by X-Extension-Prefill-Secret."""
    if not _verify_extension_prefill_secret(request):
        return jsonify({"error": "Unauthorized"}), 401, headers

    content_length = request.content_length or 0
    if content_length > 10_000:
        return jsonify({"error": "Request too large"}), 413, headers

    try:
        data = request.get_json() or {}
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400, headers

    payload, errors = validate_prefill_payload(data)
    if errors or payload is None:
        return jsonify({"error": "validation_error", "details": errors}), 400, headers

    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    expire_at = now + timedelta(minutes=PREFILL_SESSION_TTL_MINUTES)

    doc = {
        "full_name": payload["full_name"] or None,
        "email": payload["email"] or None,
        "city": payload["city"] or None,
        "company_name": payload["company_name"] or None,
        "province": payload["province"] or None,
        "created_at": now,
        "expire_at": expire_at,
    }

    try:
        retry_with_backoff(
            lambda: db.collection(PREFILL_SESSION_COLLECTION).document(token).set(doc),
            RetryConfig(max_attempts=3, base_delay_seconds=0.5, max_delay_seconds=5.0),
            operation_name="Firestore create prefill session",
        )
    except Exception as e:
        logger.error("Prefill session create failed: %s", e)
        return jsonify({"error": "Failed to create session"}), 500, headers

    return jsonify({"token": token}), 200, headers


def handle_prefill_session_redeem(request: Request, headers: dict):
    """
    POST /prefill-session/redeem — one-time token exchange (token in JSON body).

    Unauthenticated: token is high-entropy, single-use, short TTL.
    """
    content_length = request.content_length or 0
    if content_length > 5_000:
        return jsonify({"error": "Request too large"}), 413, headers

    try:
        data = request.get_json() or {}
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400, headers

    token = (data.get("token") or "").strip()
    if not token or len(token) > 512:
        return jsonify({"error": "Invalid token"}), 400, headers

    doc_ref = db.collection(PREFILL_SESSION_COLLECTION).document(token)

    try:
        snap = doc_ref.get()
    except Exception as e:
        logger.error("Prefill redeem read failed: %s", e)
        return jsonify({"error": "Failed to redeem"}), 500, headers

    if not snap.exists:
        return jsonify({"error": "Invalid or expired token"}), 404, headers

    doc = snap.to_dict() or {}
    if _prefill_doc_expired(doc.get("expire_at")):
        try:
            doc_ref.delete()
        except Exception as e:
            logger.warning("Prefill expired doc cleanup delete failed: %s", e)
        return jsonify({"error": "Invalid or expired token"}), 404, headers

    try:
        doc_ref.delete()
    except Exception as e:
        logger.error("Prefill redeem delete failed: %s", e)
        return jsonify({"error": "Failed to redeem"}), 500, headers

    return (
        jsonify(
            {
                "full_name": (doc.get("full_name") or "") or "",
                "email": (doc.get("email") or "") or "",
                "city": (doc.get("city") or "") or "",
                "company_name": (doc.get("company_name") or "") or "",
                "province": (doc.get("province") or "") or "",
            }
        ),
        200,
        headers,
    )


# =============================================================================
# Rate Limiting
# =============================================================================

# Maximum investigation requests per user within the rate limit window
RATE_LIMIT_MAX_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 300  # 5 minutes
FEEDBACK_RATE_LIMIT_MAX = 5
FEEDBACK_RATE_LIMIT_WINDOW_HOURS = 1
HISTORY_LIST_RATE_LIMIT_MAX = 120
HISTORY_LIST_RATE_LIMIT_WINDOW_SECONDS = 3600
CSV_EXPORT_RATE_LIMIT_MAX = 10
CSV_EXPORT_RATE_LIMIT_WINDOW_SECONDS = 3600
FEEDBACK_GET_RATE_LIMIT_MAX = 120
FEEDBACK_GET_RATE_LIMIT_WINDOW_SECONDS = 3600


def check_rate_limit(user_id: str) -> bool:
    """Check if user has exceeded the investigation rate limit.

    Returns True if the request is allowed, False if rate limited.
    If the Firestore count query cannot be completed (after retries for transient
    errors), returns False (fail closed) so the caller returns 429.
    """
    from google.cloud.firestore_v1.base_query import FieldFilter

    def _count_under_limit() -> bool:
        window_start = datetime.utcnow() - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
        recent_jobs = (
            db.collection("jobs")
            .where(filter=FieldFilter("user_id", "==", user_id))
            .where(filter=FieldFilter("created_at", ">=", window_start))
            .count()
            .get()
        )
        count = recent_jobs[0][0].value
        return count < RATE_LIMIT_MAX_REQUESTS

    try:
        return retry_with_backoff(
            _count_under_limit,
            RetryConfig(max_attempts=3, base_delay_seconds=0.5, max_delay_seconds=5.0),
            operation_name="Firestore rate limit count",
        )
    except Exception as e:
        logger.error("Rate limit check failed; rejecting request: %s", e)
        return False


def check_and_record_endpoint_rate_limit(user_id: str, endpoint: str, max_requests: int, window_seconds: int) -> bool:
    """Rate-limit read endpoints via a per-user per-window Firestore counter.

    Uses a document keyed by (user_id, endpoint, time_bucket) so no composite
    index is required. Fails open on Firestore error — blocking all users due to
    an infrastructure hiccup is worse than briefly skipping rate enforcement.
    """
    import math

    bucket = int(math.floor(datetime.utcnow().timestamp() / window_seconds))
    doc_id = f"{user_id}_{endpoint}_{bucket}"

    try:
        doc_ref = db.collection("endpoint_rate_limit_counters").document(doc_id)
        doc = doc_ref.get()
        count = (doc.get("count") or 0) if doc.exists else 0
        if count >= max_requests:
            return False
        doc_ref.set(
            {
                "user_id": user_id,
                "endpoint": endpoint,
                "bucket": bucket,
                "count": firestore.Increment(1),
                "expires_at": datetime.utcnow() + timedelta(seconds=window_seconds * 2),
            },
            merge=True,
        )
        return True
    except Exception as e:
        print(f"[ApiGateway] WARNING: endpoint rate limit check failed for {endpoint}, failing open: {e}")
        return True


# =============================================================================
# Data Access Helpers
# =============================================================================


def create_job(
    email: str,
    full_name: str,
    city: str,
    province: str = None,
    drive_folder_id: str = None,
    company_name: str = None,
    cars_reference_number: str = None,
    user_id: str = None,
    workflow_type: str = None,
    user_email: str = None,
    user_name: str = None,
) -> str:
    """Create a new job in Firestore."""
    job_id = uuid.uuid4().hex[:12]
    now = datetime.utcnow()
    expire_at = now + timedelta(days=JOB_RETENTION_DAYS)

    job_data = {
        "status": "triggering",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "expire_at": expire_at,
        "user_id": user_id,  # Store user_id for ownership verification
        "user_email": user_email,
        "user_name": user_name,
        "workflow_type": workflow_type,
        "input": {
            "email": email,
            "full_name": full_name,
            "city": city or None,
            "province": province or None,
            "drive_folder_id": drive_folder_id,
            "company_name": company_name or None,
            "cars_reference_number": cars_reference_number or None,
        },
        "result": None,
        "result_summary": None,
        "partial_failure": False,
        "errors": {},
        "report_urls": {},
        "reports_generated": False,
        "error": None,
    }

    retry_with_backoff(
        lambda: db.collection("jobs").document(job_id).set(job_data),
        RetryConfig(max_attempts=3, base_delay_seconds=0.5, max_delay_seconds=5.0),
        operation_name="Firestore create job",
    )
    return job_id


def trigger_workflow(
    job_id: str,
    email: str,
    full_name: str,
    city: str,
    province: str,
    company_name: str = None,
    workflow_name: str = None,
) -> str:
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
        "province": province,
    }

    # Add company_name if provided
    if company_name:
        workflow_input["company_name"] = company_name

    execution = executions_v1.Execution(argument=json.dumps(workflow_input))

    response = retry_with_backoff(
        lambda: client.create_execution(parent=parent, execution=execution),
        RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=10.0),
        operation_name="Workflow trigger",
    )
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


# =============================================================================
# CORS & Ownership Helpers
# =============================================================================


def get_cors_headers(request: Request):
    """Get CORS headers based on configuration and request origin."""
    origin = request.headers.get("Origin", "")

    # If CORS_ALLOWED_ORIGINS is "*", allow all origins
    if CORS_ALLOWED_ORIGINS == "*":
        return {"Access-Control-Allow-Origin": "*"}

    # Split comma-separated origins; omit empty tokens (commas-only / whitespace env values)
    allowed_origins = [o.strip() for o in CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
    if not allowed_origins:
        return {}

    # Exact origin match only (browser requires exact match)
    if origin in allowed_origins:
        return {"Access-Control-Allow-Origin": origin}

    return {}


def verify_job_ownership(request: Request, job_id: str, headers: dict) -> tuple[dict | None, str | None, tuple | None]:
    """
    Verify job exists and caller owns it.

    Requires a valid Firebase ID token. Job document must include user_id matching the token.

    Returns: (job, user_id, error_response)
        - On success: (job_dict, user_id, None)
        - On error: (None, None, (jsonify_response, status_code, headers))
    """
    job = get_job(job_id)
    if not job:
        return None, None, (jsonify({"error": "Job not found"}), 404, headers)

    user_id, auth_error = verify_firebase_token(request)
    if auth_error:
        return None, None, (jsonify(auth_error), 401, headers)

    job_user_id = job.get("user_id")
    if job_user_id is None:
        return (
            None,
            None,
            (
                jsonify({"error": "Job has no owner; access denied"}),
                403,
                headers,
            ),
        )
    if job_user_id != user_id:
        return None, None, (jsonify({"error": "Unauthorized"}), 403, headers)
    return job, user_id, None


def _history_query_from_request(request: Request) -> tuple[Any, dict[str, str], str]:
    """Build the Firestore query for history. Returns (query, params, mode).

    mode is "cars" when CARS prefix filtering is active (ordering by cars_reference_number)
    or "date" otherwise (ordering by created_at). When mode is "cars", date range filters are
    omitted from the Firestore query — callers must apply them client-side because Firestore
    forbids range filters on two different fields.
    """
    params = _request_args(request)
    query = db.collection("jobs").where("workflow_type", "==", "skiptrace")

    start_date = _parse_date_param(params.get("start_date"))
    end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
    user_filter = (params.get("user_id") or "").strip()
    cars_reference_number = (params.get("cars_reference_number") or "").strip().upper()

    if user_filter:
        if "@" in user_filter:
            query = query.where("user_email", "==", user_filter.lower())
        else:
            query = query.where("user_id", "==", user_filter)

    if cars_reference_number:
        cars_prefix_end = cars_reference_number + ""
        query = (
            query.where("input.cars_reference_number", ">=", cars_reference_number)
            .where("input.cars_reference_number", "<=", cars_prefix_end)
            .order_by("input.cars_reference_number", direction=firestore.Query.ASCENDING)
            .order_by("__name__", direction=firestore.Query.DESCENDING)
        )
        return query, params, "cars"

    if start_date:
        query = query.where("created_at", ">=", start_date)
    if end_date:
        query = query.where("created_at", "<=", end_date)

    return (
        query.order_by("created_at", direction=firestore.Query.DESCENDING).order_by(
            "__name__", direction=firestore.Query.DESCENDING
        ),
        params,
        "date",
    )


def handle_history_users(request: Request, headers: dict):
    """Return distinct skiptrace users for populating the history user filter."""
    uid, auth_error = verify_firebase_token(request)
    if auth_error:
        return jsonify(auth_error), 401, headers
    try:
        doc = db.collection("meta").document("skiptrace_users").get()
        users_map = (doc.to_dict() or {}).get("users", {}) if doc.exists else {}
        users = sorted(users_map.values(), key=lambda u: (u.get("user_email") or "").lower())
    except Exception as e:
        logger.error("Failed to load skiptrace users: %s", e)
        users = []
    print(f"[ApiGateway] history_users user={uid} count={len(users)}")
    return jsonify({"users": users}), 200, headers


def handle_history_list(request: Request, headers: dict):
    """Return paginated skiptrace history rows for any authenticated SSO user."""
    uid, auth_error = verify_firebase_token(request)
    if auth_error:
        return jsonify(auth_error), 401, headers

    if not check_and_record_endpoint_rate_limit(
        uid, "history_list", HISTORY_LIST_RATE_LIMIT_MAX, HISTORY_LIST_RATE_LIMIT_WINDOW_SECONDS
    ):
        return jsonify({"error": "Too many requests. Please wait before loading more history."}), 429, headers

    try:
        query, params, mode = _history_query_from_request(request)
        raw_limit = int(params.get("limit") or "50")
        limit = max(1, min(raw_limit, 200))
        filter_signature = _history_filter_signature(params, limit)
        page_token = (params.get("page_token") or "").strip()
        if page_token:
            cursor = _decode_history_page_token(page_token, filter_signature)
            if mode == "cars":
                query = query.start_after(
                    {"input.cars_reference_number": cursor["cars_ref"], "__name__": cursor["job_id"]}
                )
            else:
                query = query.start_after({"created_at": cursor["created_at"], "__name__": cursor["job_id"]})

        total_count = None
        try:
            count_result = query.limit(10001).count(alias="total").get()
            total_count = count_result[0][0].value
        except Exception:  # nosec B110 — total_count is non-critical; null is a safe fallback
            pass

        if mode == "cars":
            # Fetch extra docs so date-range filtering client-side still fills the page.
            # Page size may be < limit when both CARS prefix and date range are active.
            start_date = _parse_date_param(params.get("start_date"))
            end_date = _parse_date_param(params.get("end_date"), end_of_day=True)
            raw_docs = list(query.limit(limit * 4).stream())
            filtered = [
                d for d in raw_docs if _in_date_range((d.to_dict() or {}).get("created_at"), start_date, end_date)
            ]
            page_docs = filtered[:limit]
            has_more = len(filtered) > limit or len(raw_docs) == limit * 4
            rows = [_format_history_row(doc.id, doc.to_dict()) for doc in page_docs]
            next_page_token = None
            if has_more and page_docs:
                last_doc = page_docs[-1]
                next_page_token = _encode_history_page_token(
                    last_doc.id,
                    filter_signature,
                    cars_ref=(last_doc.to_dict() or {}).get("input", {}).get("cars_reference_number"),
                )
        else:
            docs = list(query.limit(limit + 1).stream())
            page_docs = docs[:limit]
            rows = [_format_history_row(doc.id, doc.to_dict()) for doc in page_docs]
            has_more = len(docs) > limit
            next_page_token = None
            if has_more and page_docs:
                last_doc = page_docs[-1]
                next_page_token = _encode_history_page_token(
                    last_doc.id,
                    filter_signature,
                    created_at=(last_doc.to_dict() or {}).get("created_at"),
                )

        print(
            f"[ApiGateway] history_list user={uid} mode={mode} filter_sig={filter_signature} row_count={len(rows)} has_more={has_more}"
        )
        return (
            jsonify(
                {
                    "rows": rows,
                    "limit": limit,
                    "page_size": limit,
                    "has_more": has_more,
                    "next_page_token": next_page_token,
                    "total_count": total_count,
                }
            ),
            200,
            headers,
        )
    except ValueError as e:
        if "history page token" in str(e):
            return jsonify({"error": "Invalid history page token"}), 400, headers
        return jsonify({"error": "Invalid history filter"}), 400, headers
    except Exception as e:
        logger.error("Search history list failed: %s", e)
        return jsonify({"error": f"Failed to load search history: {str(e)}"}), 500, headers


def handle_history_csv_export(request: Request, headers: dict):
    """Return CSV for all skiptrace history rows matching filters."""
    uid, auth_error = verify_firebase_token(request)
    if auth_error:
        return jsonify(auth_error), 401, headers

    if not check_and_record_endpoint_rate_limit(
        uid, "history_csv_export", CSV_EXPORT_RATE_LIMIT_MAX, CSV_EXPORT_RATE_LIMIT_WINDOW_SECONDS
    ):
        return jsonify({"error": "CSV export rate limit exceeded. Maximum 10 exports per hour."}), 429, headers

    try:
        query, csv_params, csv_mode = _history_query_from_request(request)
        count_result = query.count().get()
        total = count_result[0][0].value
        if total > 5000:
            return jsonify({"error": "Result set too large; narrow your filters."}), 413, headers
        rows_raw = [_format_history_row(doc.id, doc.to_dict()) for doc in query.limit(5000).stream()]
        if csv_mode == "cars":
            csv_start = _parse_date_param(csv_params.get("start_date"))
            csv_end = _parse_date_param(csv_params.get("end_date"), end_of_day=True)
            rows = [r for r in rows_raw if _in_date_range(r.get("created_at"), csv_start, csv_end)]
        else:
            rows = rows_raw
    except ValueError:
        return jsonify({"error": "Invalid history filter"}), 400, headers
    except Exception as e:
        logger.error("Search history CSV export failed: %s", e)
        return jsonify({"error": f"Failed to export search history: {str(e)}"}), 500, headers

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "CARS Reference Number",
            "Date/Time of Search",
            "Full Name",
            "User",
            "Job ID",
            "Status",
            "Feedback Summary",
            "Feedback Count",
            "Results URL",
        ],
    )
    writer.writeheader()
    for row in rows:
        feedback = row.get("feedback") or {}
        writer.writerow(
            {
                "CARS Reference Number": _csv_safe(row.get("cars_reference_number")),
                "Date/Time of Search": row.get("created_at") or "",
                "Full Name": _csv_safe(row.get("full_name")),
                "User": _csv_safe(row.get("user_display")),
                "Job ID": row.get("job_id") or "",
                "Status": row.get("status") or "",
                "Feedback Summary": _csv_safe(feedback.get("comment_summary")),
                "Feedback Count": row.get("feedback_count") or 0,
                "Results URL": row.get("results_url") or "",
            }
        )

    csv_bytes = len(output.getvalue().encode())
    print(f"[ApiGateway] history_csv_export user={uid} row_count={len(rows)} bytes={csv_bytes}")
    filename = f"skiptrace-search-history-{datetime.utcnow().strftime('%Y-%m-%d')}.csv"
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response, 200, headers


def handle_history_feedback_get(request: Request, job_id: str, headers: dict):
    """Return full feedback history for a skiptrace job."""
    uid, auth_error = verify_firebase_token(request)
    if auth_error:
        return jsonify(auth_error), 401, headers

    if not check_and_record_endpoint_rate_limit(
        uid, "feedback_get", FEEDBACK_GET_RATE_LIMIT_MAX, FEEDBACK_GET_RATE_LIMIT_WINDOW_SECONDS
    ):
        return jsonify({"error": "Too many requests."}), 429, headers

    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404, headers
    if not _is_skiptrace_job(job):
        return jsonify({"error": "Unauthorized"}), 403, headers

    entries = []
    for entry in _feedback_entries(job):
        entries.append(
            {
                "rating": entry.get("rating"),
                "comment": entry.get("comment") or "",
                "submitted_at": _isoformat(entry.get("submitted_at")),
                "user_id": entry.get("user_id"),
                "user_email": entry.get("user_email"),
            }
        )
    print(f"[ApiGateway] feedback_get user={uid} job_id={job_id} entry_count={len(entries)}")
    return jsonify({"entries": entries}), 200, headers


def handle_skiptrace_result_data(request: Request, job_id: str, headers: dict):
    """Return data needed to render a skiptrace result for any authenticated SSO user."""
    _user_id, auth_error = verify_firebase_token(request)
    if auth_error:
        return jsonify(auth_error), 401, headers

    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404, headers
    if not _is_skiptrace_job(job):
        return jsonify({"error": "Unauthorized"}), 403, headers

    markdown_reports = job.get("markdown_reports", {})
    if not markdown_reports:
        return jsonify({"error": "Markdown reports not available for this job"}), 404, headers

    return jsonify({"job_data": _json_safe(job), "markdown_reports": markdown_reports}), 200, headers


# =============================================================================
# Route Handlers
# =============================================================================


def handle_investigation(request: Request, headers: dict, workflow_name: str):
    """Handle POST /investigate-skiptrace and /investigate-origination."""
    # Verify authentication
    user_id, decoded_claims, auth_error = verify_firebase_token_with_claims(request)
    if auth_error:
        return jsonify(auth_error), 401, headers
    user_profile = _user_profile_from_claims(decoded_claims)

    # H3: Reject oversized request bodies early
    content_length = request.content_length or 0
    if content_length > 50_000:  # 50KB - investigation payloads are small
        return jsonify({"error": "Request too large"}), 413, headers

    # H4: Server-side rate limiting
    if not check_rate_limit(user_id):
        return jsonify({"error": "Too many requests. Please wait a few minutes."}), 429, headers

    try:
        data = request.get_json() or {}
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400, headers

    email = (data.get("email") or "").strip()
    full_name = (data.get("full_name") or "").strip()
    city = (data.get("city") or "").strip()
    province = (data.get("province") or "").strip()
    drive_folder_id = (data.get("drive_folder_id") or "").strip()
    company_name = (data.get("company_name") or "").strip()
    cars_reference_number = (data.get("cars_reference_number") or "").strip()
    is_skiptrace = workflow_name == SKIPTRACE_WORKFLOW_NAME

    # Validate
    errors = []

    # Optional: Validate drive_folder_id format if provided
    if drive_folder_id and (
        len(drive_folder_id) < 10
        or len(drive_folder_id) > 100
        or not drive_folder_id.replace("_", "").replace("-", "").isalnum()
    ):
        errors.append({"field": "drive_folder_id", "message": "Invalid Drive folder ID format"})

    valid, msg = validate_email(email)
    if not valid:
        errors.append({"field": "email", "message": msg})

    valid, msg = validate_full_name(full_name)
    if not valid:
        errors.append({"field": "full_name", "message": msg})
    else:
        full_name = msg

    valid, msg = validate_city(city)
    if not valid:
        errors.append({"field": "city", "message": msg})
    else:
        city = msg

    valid, msg = validate_province(province)
    if not valid:
        errors.append({"field": "province", "message": msg})
    else:
        province = msg

    if company_name and len(company_name) > 200:
        errors.append({"field": "company_name", "message": "Company name must be 200 characters or less"})

    if is_skiptrace:
        valid, msg = validate_cars_reference_number(cars_reference_number)
        if not valid:
            errors.append({"field": "cars_reference_number", "message": msg})
        else:
            cars_reference_number = msg
    elif cars_reference_number:
        valid, msg = validate_cars_reference_number(cars_reference_number)
        if not valid:
            errors.append({"field": "cars_reference_number", "message": msg})
        else:
            cars_reference_number = msg

    if errors:
        return jsonify({"error": "validation_error", "details": errors}), 400, headers

    # Create job with user_id (initial status "triggering" until workflow starts)
    job_id = create_job(
        email,
        full_name,
        city,
        province,
        drive_folder_id,
        company_name,
        cars_reference_number,
        user_id=user_id,
        workflow_type="skiptrace" if is_skiptrace else "origination",
        user_email=user_profile["user_email"],
        user_name=user_profile["user_name"],
    )

    # Track skiptrace users for history filter autocomplete
    if is_skiptrace:
        try:
            db.collection("meta").document("skiptrace_users").set(
                {
                    "users": {
                        user_id: {
                            "user_id": user_id,
                            "user_email": user_profile["user_email"],
                            "user_name": user_profile["user_name"],
                        }
                    }
                },
                merge=True,
            )
        except Exception as e:
            print(f"[ApiGateway] WARNING: failed to update skiptrace_users: {e}")

    # Trigger workflow (async), then mark job as pending
    try:
        trigger_workflow(job_id, email, full_name, city, province, company_name, workflow_name=workflow_name)
        retry_with_backoff(
            lambda: db.collection("jobs").document(job_id).update({"status": "pending"}),
            RetryConfig(max_attempts=3, base_delay_seconds=0.5, max_delay_seconds=5.0),
            operation_name="Firestore update job pending",
        )
    except Exception as e:
        # Update job as failed — capture error string eagerly for lambda closure
        error_msg = f"Failed to start workflow: {str(e)}"
        retry_with_backoff(
            lambda: db.collection("jobs").document(job_id).update({"status": "failed", "error": error_msg}),
            RetryConfig(max_attempts=3, base_delay_seconds=0.5, max_delay_seconds=5.0),
            operation_name="Firestore update job failed",
        )
        return jsonify({"error": f"Failed to start investigation: {str(e)}"}), 500, headers

    return jsonify({"job_id": job_id}), 202, headers


def proxy_chat_request(request: Request, headers: dict, target_url: str, service_name: str):
    """Handle POST /chat_handler and /chat_handler_origination."""
    # Verify authentication
    user_id, auth_error = verify_firebase_token(request)
    if auth_error:
        return jsonify(auth_error), 401, headers

    # H3: Reject oversized request bodies early
    content_length = request.content_length or 0
    if content_length > 500_000:  # 500KB limit for chat (includes markdown context)
        return jsonify({"error": "Request too large"}), 413, headers

    # Check that target URL is configured
    if not target_url:
        return jsonify({"error": f"{service_name} service not configured"}), 500, headers

    try:
        data = request.get_json() or {}
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400, headers

    # If job_id is provided, verify ownership
    job_id = data.get("job_id")
    if job_id:
        job = get_job(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404, headers
        job_user_id = job.get("user_id")
        if job_user_id is None:
            return jsonify({"error": "Job has no owner; access denied"}), 403, headers
        if job_user_id != user_id:
            return jsonify({"error": "Unauthorized"}), 403, headers

    def _call_service():
        id_token = _id_token_for_url(target_url)
        response = requests.post(
            target_url,
            json=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {id_token}",
            },
            timeout=120,  # Handle longer conversations with more history
        )
        response.raise_for_status()
        return response

    try:
        response = retry_with_backoff(
            _call_service,
            RetryConfig(max_attempts=2, base_delay_seconds=1.0, max_delay_seconds=5.0),
            operation_name=f"{service_name} API call",
        )
        return jsonify(response.json()), response.status_code, headers
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"{service_name} failed: {str(e)}"}), 500, headers


# =============================================================================
# Main Entry Point
# =============================================================================


@functions_framework.http
def main(request: Request):
    """Main HTTP handler."""
    # Handle CORS preflight
    if request.method == "OPTIONS":
        headers = get_cors_headers(request)
        headers.update(
            {
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Firebase-AppCheck, X-Extension-Prefill-Secret",
                "Access-Control-Max-Age": "3600",
            }
        )
        return ("", 204, headers)

    headers = get_cors_headers(request)

    path = request.path

    # POST /extension/prefill-session (Chrome extension — shared secret)
    if request.method == "POST" and path == "/extension/prefill-session":
        return handle_extension_prefill_session(request, headers)

    # POST /prefill-session/redeem (browser — opaque token in JSON body)
    if request.method == "POST" and path == "/prefill-session/redeem":
        return handle_prefill_session_redeem(request, headers)

    # POST /investigate-skiptrace
    if request.method == "POST" and path == "/investigate-skiptrace":
        return handle_investigation(request, headers, SKIPTRACE_WORKFLOW_NAME)

    # POST /investigate-origination
    if request.method == "POST" and path == "/investigate-origination":
        return handle_investigation(request, headers, ORIGINATION_WORKFLOW_NAME)

    # GET /jobs/history/export.csv
    if request.method == "GET" and path == "/jobs/history/export.csv":
        return handle_history_csv_export(request, headers)

    # GET /jobs/history/users
    if request.method == "GET" and path == "/jobs/history/users":
        return handle_history_users(request, headers)

    # GET /jobs/history
    if request.method == "GET" and path == "/jobs/history":
        return handle_history_list(request, headers)

    # GET /jobs/{job_id}/feedback
    if request.method == "GET" and "/jobs/" in path and path.endswith("/feedback"):
        path_parts = path.strip("/").split("/")
        if len(path_parts) != 3 or path_parts[0] != "jobs" or path_parts[2] != "feedback":
            return jsonify({"error": "Invalid feedback path"}), 400, headers
        return handle_history_feedback_get(request, path_parts[1], headers)

    # GET /jobs/{job_id}/result-data
    if request.method == "GET" and "/jobs/" in path and path.endswith("/result-data"):
        path_parts = path.strip("/").split("/")
        if len(path_parts) != 3 or path_parts[0] != "jobs" or path_parts[2] != "result-data":
            return jsonify({"error": "Invalid result path"}), 400, headers
        return handle_skiptrace_result_data(request, path_parts[1], headers)

    # GET /jobs/{job_id}
    if request.method == "GET" and path.startswith("/jobs/"):
        job_id = path.split("/jobs/")[-1].strip("/")

        if not job_id:
            return jsonify({"error": "Job ID required"}), 400, headers

        job, _, error_response = verify_job_ownership(request, job_id, headers)
        if error_response:
            return error_response

        return jsonify(format_job_response(job_id, job)), 200, headers

    # GET /get_markdown/{job_id}
    if request.method == "GET" and path.startswith("/get_markdown/"):
        job_id = path.split("/get_markdown/")[-1].strip("/")

        if not job_id:
            return jsonify({"error": "Job ID required"}), 400, headers

        try:
            job, _, error_response = verify_job_ownership(request, job_id, headers)
            if error_response:
                return error_response

            markdown_reports = job.get("markdown_reports", {})

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
        _user_id, auth_error = verify_firebase_token(request)
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
            id_token = _id_token_for_url(ADDRESS_VERIFICATION_URL)
            response = requests.post(
                ADDRESS_VERIFICATION_URL,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {id_token}",
                },
                timeout=60,  # Address verification may take longer due to LLM analysis
            )
            response.raise_for_status()
            return response

        try:
            response = retry_with_backoff(
                _call_address_verification,
                RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=10.0),
                operation_name="Address verification API call",
            )
            return jsonify(response.json()), response.status_code, headers
        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"Address verification failed: {str(e)}"}), 500, headers

    # POST /chat_handler
    if request.method == "POST" and path == "/chat_handler":
        return proxy_chat_request(request, headers, CHAT_HANDLER_URL, "Chat handler")

    # POST /chat_handler_origination
    if request.method == "POST" and path == "/chat_handler_origination":
        return proxy_chat_request(request, headers, CHAT_HANDLER_ORIGINATION_URL, "Chat handler origination")

    # POST /jobs/{job_id}/feedback
    if request.method == "POST" and "/jobs/" in path and path.endswith("/feedback"):
        # Extract job_id from path: /jobs/{job_id}/feedback
        path_parts = path.strip("/").split("/")
        if len(path_parts) != 3 or path_parts[0] != "jobs" or path_parts[2] != "feedback":
            return jsonify({"error": "Invalid feedback path"}), 400, headers

        job_id = path_parts[1]
        if not job_id:
            return jsonify({"error": "Job ID required"}), 400, headers

        # Verify authentication (always required for feedback - need user_id)
        user_id, decoded_claims, auth_error = verify_firebase_token_with_claims(request)
        if auth_error:
            return jsonify(auth_error), 401, headers
        user_profile = _user_profile_from_claims(decoded_claims)

        # Get job. Skiptrace feedback can be appended by any authenticated SSO
        # user via Search History; other workflow feedback remains owner-only.
        job = get_job(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404, headers

        job_user_id = job.get("user_id")
        if job_user_id is None:
            return jsonify({"error": "Job has no owner; access denied"}), 403, headers
        if job_user_id != user_id and not _is_skiptrace_job(job):
            return jsonify({"error": "Unauthorized"}), 403, headers

        # Parse and validate request body
        try:
            data = request.get_json() or {}
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400, headers

        rating = (data.get("rating") or "").strip()
        comment = (data.get("comment") or "").strip()

        if rating not in ("positive", "negative"):
            return jsonify({"error": "rating must be 'positive' or 'negative'"}), 400, headers

        if len(comment) > 1000:
            return jsonify({"error": "comment must be 1000 characters or fewer"}), 400, headers

        existing_entries = _feedback_entries(job)

        if len(existing_entries) >= 500:
            return jsonify({"error": "Feedback limit reached for this job."}), 409, headers

        if not _check_feedback_rate_limit(existing_entries, user_id):
            return jsonify({"error": "Feedback rate limit exceeded. Try again later."}), 429, headers

        feedback_data = {
            "rating": rating,
            "comment": comment,
            "submitted_at": datetime.utcnow(),
            "user_id": user_id,
            "user_email": user_profile["user_email"],
        }

        try:
            db.collection("jobs").document(job_id).update({"feedback_entries": firestore.ArrayUnion([feedback_data])})
        except Exception as e:
            return jsonify({"error": f"Failed to save feedback: {str(e)}"}), 500, headers

        updated_job = dict(job)
        updated_job["feedback_entries"] = [*existing_entries, feedback_data]
        row = _format_history_row(job_id, updated_job)
        print(f"[ApiGateway] feedback_post user={user_id} job_id={job_id} rating={rating} comment_len={len(comment)}")
        return jsonify({"status": "ok", "row": row}), 200, headers

    # Health check
    if request.method == "GET" and path in ("/health", "/"):
        return (
            jsonify(
                {
                    "status": "healthy",
                    "service": "api_gateway",
                }
            ),
            200,
            headers,
        )

    return jsonify({"error": "Not found"}), 404, headers
