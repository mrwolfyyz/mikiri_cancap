"""Tests for the api_gateway Cloud Function (gcp/functions/api_gateway/main.py).

This is the most complex function — it's the front door to everything.
We mock Firestore, Firebase Auth, Workflows, and outbound HTTP calls,
then exercise every route and helper function.

Note: Validation helpers (validate_email, validate_full_name, etc.) are
already tested in test_api_gateway_validation.py.  These tests focus on
the routing, authentication, rate limiting, job lifecycle, and proxy logic.
"""

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import flask

# ---------------------------------------------------------------------------
# Mock heavy GCP dependencies BEFORE loading the module.
# ---------------------------------------------------------------------------
_mock_ff = MagicMock()
_mock_ff.http = lambda f: f
sys.modules.setdefault("functions_framework", _mock_ff)

# google.cloud.firestore
_mock_gc = MagicMock()
_mock_gc_firestore = MagicMock()
_mock_gc_firestore_v1 = MagicMock()
_mock_gc_firestore_v1_base_query = MagicMock()
sys.modules.setdefault("google", _mock_gc)
sys.modules.setdefault("google.cloud", _mock_gc)
sys.modules.setdefault("google.cloud.firestore", _mock_gc_firestore)
sys.modules.setdefault("google.cloud.firestore_v1", _mock_gc_firestore_v1)
sys.modules.setdefault("google.cloud.firestore_v1.base_query", _mock_gc_firestore_v1_base_query)

# google.cloud.workflows / executions
_mock_gc_workflows = MagicMock()
_mock_gc_workflows_v1 = MagicMock()
_mock_gc_workflows_exec = MagicMock()
sys.modules.setdefault("google.cloud.workflows", _mock_gc_workflows)
sys.modules.setdefault("google.cloud.workflows_v1", _mock_gc_workflows_v1)
sys.modules.setdefault("google.cloud.workflows.executions_v1", _mock_gc_workflows_exec)


# firebase_admin — auth exception classes must be real Exception subclasses
# so they can be used in except clauses.
class _InvalidIdTokenError(Exception):
    pass


class _ExpiredIdTokenError(Exception):
    pass


_mock_fb_admin = MagicMock()
_mock_fb_auth = MagicMock()
_mock_fb_auth.InvalidIdTokenError = _InvalidIdTokenError
_mock_fb_auth.ExpiredIdTokenError = _ExpiredIdTokenError
_mock_fb_app_check = MagicMock()
_mock_fb_admin.auth = _mock_fb_auth
_mock_fb_admin.app_check = _mock_fb_app_check
sys.modules.setdefault("firebase_admin", _mock_fb_admin)

# ---------------------------------------------------------------------------
# Set required environment variables before loading the module
# ---------------------------------------------------------------------------
os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("GCP_LOCATION", "northamerica-northeast1")
os.environ.setdefault("CHAT_HANDLER_URL", "https://chat.example.com")
os.environ.setdefault("CHAT_HANDLER_ORIGINATION_URL", "https://chat-orig.example.com")
os.environ.setdefault("ADDRESS_VERIFICATION_URL", "https://addr.example.com")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "*")
os.environ.setdefault("EXTENSION_PREFILL_SECRET", "test-extension-prefill-secret")
os.environ.setdefault("PREFILL_SESSION_TTL_MINUTES", "10")
os.environ.setdefault("REQUIRE_SSO", "false")
os.environ.setdefault("APP_CHECK_ENFORCED", "false")
os.environ.setdefault("ALLOWED_EMAIL_DOMAINS", "")

# ---------------------------------------------------------------------------
# Load api_gateway/main.py
# ---------------------------------------------------------------------------
from conftest import load_function_module

gw = load_function_module("api_gateway", "api_gateway_main")

main_handler = gw.main
verify_firebase_token = gw.verify_firebase_token
check_rate_limit = gw.check_rate_limit
create_job = gw.create_job
trigger_workflow = gw.trigger_workflow
format_job_response = gw.format_job_response
get_cors_headers = gw.get_cors_headers
verify_job_ownership = gw.verify_job_ownership
handle_investigation = gw.handle_investigation
proxy_chat_request = gw.proxy_chat_request
handle_extension_prefill_session = gw.handle_extension_prefill_session
handle_prefill_session_redeem = gw.handle_prefill_session_redeem
validate_prefill_payload = gw.validate_prefill_payload
_prefill_doc_expired = gw._prefill_doc_expired


# ---------------------------------------------------------------------------
# Flask test app (required for jsonify calls inside the gateway)
# ---------------------------------------------------------------------------
_app = flask.Flask(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_request(
    method="GET",
    path="/",
    body=None,
    headers=None,
    content_length=0,
    bad_json=False,
):
    """Build a mock Flask-like request."""
    req = MagicMock(spec=flask.Request)
    req.method = method
    req.path = path
    req.content_length = content_length
    req.headers = headers or {}
    if bad_json:
        req.get_json.side_effect = Exception("bad JSON")
    else:
        req.get_json.return_value = body
    return req


def _authed_request(user_id="user-123", **kwargs):
    """Build a request with a valid-looking Authorization header."""
    req = _make_request(**kwargs)
    req.headers = {**req.headers, "Authorization": f"Bearer valid-token-{user_id}"}
    return req


def _stub_auth(user_id="user-123"):
    """Patch verify_id_token to return a decoded token with the given uid."""
    return patch.object(gw.auth, "verify_id_token", return_value={"uid": user_id})


def _stub_rate_limit(allowed=True):
    return patch.object(gw, "check_rate_limit", return_value=allowed)


def _stub_get_job(job=None):
    return patch.object(gw, "get_job", return_value=job)


def _stub_create_job(job_id="abc123"):
    return patch.object(gw, "create_job", return_value=job_id)


def _stub_trigger_workflow():
    return patch.object(gw, "trigger_workflow", return_value="executions/abc")


def _parse_response(resp):
    """Extract (data_dict, status_code, headers) from a handler return value."""
    if isinstance(resp, tuple):
        body, status = resp[0], resp[1]
        hdrs = resp[2] if len(resp) > 2 else {}
    else:
        body, status, hdrs = resp, 200, {}

    if isinstance(body, flask.Response):
        data = json.loads(body.get_data(as_text=True))
    elif isinstance(body, str):
        data = json.loads(body) if body else {}
    elif isinstance(body, dict):
        data = body
    else:
        data = body
    return data, status, hdrs


# ===========================================================================
# verify_firebase_token
# ===========================================================================
class TestVerifyFirebaseToken:
    def test_valid_token(self):
        req = _make_request(headers={"Authorization": "Bearer good-token"})
        with patch.object(gw.auth, "verify_id_token", return_value={"uid": "u1"}):
            uid, err = verify_firebase_token(req)
        assert uid == "u1"
        assert err is None

    def test_missing_auth_header(self):
        req = _make_request(headers={})
        uid, err = verify_firebase_token(req)
        assert uid is None
        assert "Authentication required" in err["error"]

    def test_non_bearer_header(self):
        req = _make_request(headers={"Authorization": "Basic abc"})
        uid, err = verify_firebase_token(req)
        assert uid is None
        assert "Authentication required" in err["error"]

    def test_invalid_token(self):
        req = _make_request(headers={"Authorization": "Bearer bad"})
        with patch.object(
            gw.auth,
            "verify_id_token",
            side_effect=_InvalidIdTokenError("invalid"),
        ):
            uid, err = verify_firebase_token(req)
        assert uid is None
        assert "Authentication failed" in err["error"]

    def test_generic_exception(self):
        req = _make_request(headers={"Authorization": "Bearer bad"})
        with patch.object(gw.auth, "verify_id_token", side_effect=Exception("boom")):
            uid, err = verify_firebase_token(req)
        assert uid is None
        assert "Authentication failed" in err["error"]

    def test_sso_requires_google_provider(self):
        req = _make_request(headers={"Authorization": "Bearer good-token"})
        with (
            patch.object(gw, "REQUIRE_SSO", True),
            patch.object(gw, "ALLOWED_EMAIL_DOMAINS", {"cancap.ca"}),
            patch.object(
                gw.auth, "verify_id_token", return_value={"uid": "u1", "firebase": {"sign_in_provider": "anonymous"}}
            ),
        ):
            uid, err = verify_firebase_token(req)
        assert uid is None
        assert err["error"] == "SSO required"

    def test_sso_rejects_unverified_email(self):
        req = _make_request(headers={"Authorization": "Bearer good-token"})
        decoded = {
            "uid": "u1",
            "firebase": {"sign_in_provider": "google.com"},
            "email_verified": False,
            "email": "user@cancap.ca",
        }
        with (
            patch.object(gw, "REQUIRE_SSO", True),
            patch.object(gw, "ALLOWED_EMAIL_DOMAINS", {"cancap.ca"}),
            patch.object(gw.auth, "verify_id_token", return_value=decoded),
        ):
            uid, err = verify_firebase_token(req)
        assert uid is None
        assert err["error"] == "Email not verified"

    def test_sso_rejects_disallowed_domain(self):
        req = _make_request(headers={"Authorization": "Bearer good-token"})
        decoded = {
            "uid": "u1",
            "firebase": {"sign_in_provider": "google.com"},
            "email_verified": True,
            "email": "user@gmail.com",
        }
        with (
            patch.object(gw, "REQUIRE_SSO", True),
            patch.object(gw, "ALLOWED_EMAIL_DOMAINS", {"cancap.ca"}),
            patch.object(gw.auth, "verify_id_token", return_value=decoded),
        ):
            uid, err = verify_firebase_token(req)
        assert uid is None
        assert err["error"] == "Account not permitted"

    def test_sso_accepts_allowed_domain(self):
        req = _make_request(headers={"Authorization": "Bearer good-token"})
        decoded = {
            "uid": "u1",
            "firebase": {"sign_in_provider": "google.com"},
            "email_verified": True,
            "email": "user@cancap.ca",
        }
        with (
            patch.object(gw, "REQUIRE_SSO", True),
            patch.object(gw, "ALLOWED_EMAIL_DOMAINS", {"cancap.ca"}),
            patch.object(gw.auth, "verify_id_token", return_value=decoded),
        ):
            uid, err = verify_firebase_token(req)
        assert uid == "u1"
        assert err is None

    def test_app_check_missing_header_rejected(self):
        req = _make_request(headers={"Authorization": "Bearer good-token"})
        with (
            patch.object(gw, "APP_CHECK_ENFORCED", True),
            patch.object(gw.auth, "verify_id_token", return_value={"uid": "u1"}),
        ):
            uid, err = verify_firebase_token(req)
        assert uid is None
        assert err["error"] == "App Check token required"

    def test_app_check_invalid_token_rejected(self):
        req = _make_request(
            headers={"Authorization": "Bearer good-token", "X-Firebase-AppCheck": "bad-app-check-token"}
        )
        with (
            patch.object(gw, "APP_CHECK_ENFORCED", True),
            patch.object(gw.auth, "verify_id_token", return_value={"uid": "u1"}),
            patch.object(gw.app_check, "verify_token", side_effect=Exception("bad app check token")),
        ):
            uid, err = verify_firebase_token(req)
        assert uid is None
        assert err["error"] == "App Check failed"

    def test_app_check_valid_token_accepted(self):
        req = _make_request(
            headers={"Authorization": "Bearer good-token", "X-Firebase-AppCheck": "valid-app-check-token"}
        )
        with (
            patch.object(gw, "APP_CHECK_ENFORCED", True),
            patch.object(gw.auth, "verify_id_token", return_value={"uid": "u1"}),
            patch.object(gw.app_check, "verify_token", return_value={"sub": "app-id"}),
        ):
            uid, err = verify_firebase_token(req)
        assert uid == "u1"
        assert err is None


# ===========================================================================
# check_rate_limit
# ===========================================================================
class TestCheckRateLimit:
    def test_under_limit(self):
        mock_count = MagicMock()
        mock_count.value = 3
        gw.db.collection.return_value.where.return_value.where.return_value.count.return_value.get.return_value = [
            [mock_count]
        ]
        assert check_rate_limit("user-1") is True

    def test_at_limit(self):
        mock_count = MagicMock()
        mock_count.value = 5
        gw.db.collection.return_value.where.return_value.where.return_value.count.return_value.get.return_value = [
            [mock_count]
        ]
        assert check_rate_limit("user-1") is False

    def test_firestore_error_fails_closed(self):
        """If the rate limit query fails, deny the request (fail closed)."""
        gw.db.collection.return_value.where.side_effect = Exception("index missing")
        assert check_rate_limit("user-1") is False
        gw.db.collection.return_value.where.side_effect = None  # cleanup


# ===========================================================================
# format_job_response
# ===========================================================================
class TestFormatJobResponse:
    def test_pending_job(self):
        now = datetime.utcnow()
        job = {"status": "pending", "created_at": now}
        resp = format_job_response("j1", job)
        assert resp["job_id"] == "j1"
        assert resp["status"] == "pending"
        assert resp["created_at"].endswith("Z")

    def test_post_processing_status_has_message(self):
        now = datetime.utcnow()
        job = {"status": "post_processing", "created_at": now}
        resp = format_job_response("j1", job)
        assert "generating reports" in resp["message"].lower()

    def test_complete_job(self):
        created = datetime.utcnow() - timedelta(seconds=120)
        completed = datetime.utcnow()
        job = {
            "status": "complete",
            "created_at": created,
            "started_at": created + timedelta(seconds=5),
            "completed_at": completed,
            "input": {"email": "a@b.com"},
            "result_summary": {"overall_status": "clear"},
            "partial_failure": False,
            "report_urls": {"skiptrace": "https://..."},
        }
        resp = format_job_response("j1", job)
        assert resp["status"] == "complete"
        assert resp["completed_at"].endswith("Z")
        assert resp["elapsed_seconds"] == 120
        assert resp["input"]["email"] == "a@b.com"
        assert resp["result_summary"]["overall_status"] == "clear"
        assert resp["report_urls"]["skiptrace"] == "https://..."

    def test_complete_with_partial_failure(self):
        created = datetime.utcnow()
        job = {
            "status": "complete",
            "created_at": created,
            "completed_at": created + timedelta(seconds=60),
            "partial_failure": True,
            "errors": {"domain_enrichment": "timeout"},
            "report_urls": {},
        }
        resp = format_job_response("j1", job)
        assert resp["partial_failure"] is True
        assert resp["errors"]["domain_enrichment"] == "timeout"

    def test_failed_job(self):
        job = {"status": "failed", "created_at": datetime.utcnow(), "error": "workflow crashed"}
        resp = format_job_response("j1", job)
        assert resp["status"] == "failed"
        assert resp["error"] == "workflow crashed"

    def test_failed_job_default_error(self):
        job = {"status": "failed", "created_at": datetime.utcnow()}
        resp = format_job_response("j1", job)
        assert resp["error"] == "Unknown error"

    def test_created_at_none(self):
        """Job with created_at=None should not raise."""
        job = {"status": "pending", "created_at": None}
        resp = format_job_response("j1", job)
        assert resp["created_at"] is None

    def test_complete_without_completed_at(self):
        """Complete job with completed_at=None should not raise or include elapsed_seconds."""
        job = {
            "status": "complete",
            "created_at": datetime.utcnow(),
            "completed_at": None,
            "partial_failure": False,
            "report_urls": {"identity": "https://..."},
        }
        resp = format_job_response("j1", job)
        assert resp["status"] == "complete"
        assert "completed_at" not in resp
        assert "elapsed_seconds" not in resp

    def test_complete_empty_report_urls(self):
        """Complete job with empty report_urls dict should not include report_urls."""
        job = {
            "status": "complete",
            "created_at": datetime.utcnow(),
            "completed_at": datetime.utcnow(),
            "partial_failure": False,
            "report_urls": {},
        }
        resp = format_job_response("j1", job)
        assert "report_urls" not in resp


# ===========================================================================
# get_cors_headers
# ===========================================================================
class TestGetCorsHeaders:
    def test_wildcard(self):
        with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"):
            req = _make_request(headers={"Origin": "https://app.example.com"})
            headers = get_cors_headers(req)
        assert headers["Access-Control-Allow-Origin"] == "*"

    def test_matching_origin(self):
        with patch.object(gw, "CORS_ALLOWED_ORIGINS", "https://app.example.com,https://other.com"):
            req = _make_request(headers={"Origin": "https://app.example.com"})
            headers = get_cors_headers(req)
        assert headers["Access-Control-Allow-Origin"] == "https://app.example.com"

    def test_non_matching_origin_returns_no_cors_header(self):
        with patch.object(gw, "CORS_ALLOWED_ORIGINS", "https://app.example.com"):
            req = _make_request(headers={"Origin": "https://evil.com"})
            headers = get_cors_headers(req)
        assert headers == {}
        assert "Access-Control-Allow-Origin" not in headers

    def test_empty_allowlist_after_normalization(self):
        with patch.object(gw, "CORS_ALLOWED_ORIGINS", ", , "):
            req = _make_request(headers={"Origin": "https://app.example.com"})
            headers = get_cors_headers(req)
        assert headers == {}
        assert "*" not in str(headers.values())

    def test_allowlist_never_emits_wildcard_on_mismatch(self):
        with patch.object(gw, "CORS_ALLOWED_ORIGINS", "https://app.example.com,https://other.com"):
            req = _make_request(headers={"Origin": "https://attacker.example"})
            headers = get_cors_headers(req)
        assert "Access-Control-Allow-Origin" not in headers
        assert "*" not in headers.values()


# ===========================================================================
# verify_job_ownership
# ===========================================================================
class TestVerifyJobOwnership:
    def test_job_not_found(self):
        with _app.test_request_context():
            with _stub_get_job(None):
                _, _, err = verify_job_ownership(_make_request(), "j1", {})
            assert err is not None
            data, status, _ = _parse_response(err)
            assert status == 404

    def test_job_with_user_id_valid_auth(self):
        job = {"user_id": "u1", "status": "complete"}
        req = _make_request(headers={"Authorization": "Bearer tok"})
        with _app.test_request_context():
            with _stub_get_job(job), _stub_auth("u1"):
                got_job, got_uid, err = verify_job_ownership(req, "j1", {})
        assert err is None
        assert got_job == job
        assert got_uid == "u1"

    def test_job_with_user_id_wrong_user(self):
        job = {"user_id": "u1", "status": "complete"}
        req = _make_request(headers={"Authorization": "Bearer tok"})
        with _app.test_request_context():
            with _stub_get_job(job), _stub_auth("u-other"):
                _, _, err = verify_job_ownership(req, "j1", {})
            data, status, _ = _parse_response(err)
        assert status == 403

    def test_job_with_user_id_no_auth(self):
        job = {"user_id": "u1", "status": "complete"}
        req = _make_request(headers={})  # no auth header
        with _app.test_request_context():
            with _stub_get_job(job):
                _, _, err = verify_job_ownership(req, "j1", {})
            data, status, _ = _parse_response(err)
        assert status == 401

    def test_legacy_job_no_user_id_requires_auth(self):
        job = {"status": "complete"}  # no user_id key
        with _app.test_request_context():
            with _stub_get_job(job):
                _, _, err = verify_job_ownership(_make_request(), "j1", {})
        assert err is not None
        _, status, _ = _parse_response(err)
        assert status == 401

    def test_legacy_job_no_user_id_denied_when_authed(self):
        job = {"status": "complete"}  # no user_id key
        req = _make_request(headers={"Authorization": "Bearer tok"})
        with _app.test_request_context():
            with _stub_get_job(job), _stub_auth("u1"):
                _, _, err = verify_job_ownership(req, "j1", {})
        assert err is not None
        data, status, _ = _parse_response(err)
        assert status == 403
        assert "owner" in data["error"].lower() or "denied" in data["error"].lower()


# ===========================================================================
# handle_investigation
# ===========================================================================
class TestHandleInvestigation:
    def _valid_body(self):
        return {
            "email": "john@example.com",
            "full_name": "John Smith",
            "city": "Toronto",
            "province": "ON",
            "company_name": "Acme Inc",
        }

    def test_happy_path(self):
        req = _authed_request(
            method="POST",
            path="/investigate-skiptrace",
            body=self._valid_body(),
        )
        with _app.test_request_context():
            with _stub_auth(), _stub_rate_limit(), _stub_create_job("j1"), _stub_trigger_workflow():
                gw.db.collection.return_value.document.return_value.update = MagicMock()
                data, status, _ = _parse_response(handle_investigation(req, {}, "investigate-skiptrace"))
        assert status == 202
        assert data["job_id"] == "j1"

    def test_optional_company_name_omitted(self):
        body = {k: v for k, v in self._valid_body().items() if k != "company_name"}
        req = _authed_request(method="POST", path="/investigate-skiptrace", body=body)
        with _app.test_request_context():
            with _stub_auth(), _stub_rate_limit(), _stub_create_job("j1"), _stub_trigger_workflow() as mock_tw:
                gw.db.collection.return_value.document.return_value.update = MagicMock()
                data, status, _ = _parse_response(handle_investigation(req, {}, "investigate-skiptrace"))
        assert status == 202
        assert data["job_id"] == "j1"
        mock_tw.assert_called_once()
        assert mock_tw.call_args[0][5] == ""

    def test_company_name_too_long(self):
        body = self._valid_body()
        body["company_name"] = "x" * 201
        req = _authed_request(method="POST", path="/investigate-skiptrace", body=body)
        with _app.test_request_context():
            with _stub_auth(), _stub_rate_limit():
                data, status, _ = _parse_response(handle_investigation(req, {}, "investigate-skiptrace"))
        assert status == 400
        assert data["error"] == "validation_error"
        fields = {d["field"]: d["message"] for d in data["details"]}
        assert fields.get("company_name") == "Company name must be 200 characters or less"

    def test_auth_failure(self):
        req = _make_request(method="POST", headers={})
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_investigation(req, {}, "investigate-skiptrace"))
        assert status == 401

    def test_oversized_body(self):
        req = _authed_request(method="POST", content_length=60_000)
        with _app.test_request_context():
            with _stub_auth():
                data, status, _ = _parse_response(handle_investigation(req, {}, "x"))
        assert status == 413

    def test_rate_limited(self):
        req = _authed_request(method="POST", body=self._valid_body())
        with _app.test_request_context():
            with _stub_auth(), _stub_rate_limit(allowed=False):
                data, status, _ = _parse_response(handle_investigation(req, {}, "x"))
        assert status == 429

    def test_validation_errors(self):
        req = _authed_request(method="POST", body={"email": "bad"})
        with _app.test_request_context():
            with _stub_auth(), _stub_rate_limit():
                data, status, _ = _parse_response(handle_investigation(req, {}, "x"))
        assert status == 400
        assert data["error"] == "validation_error"
        fields = [d["field"] for d in data["details"]]
        assert "full_name" in fields
        assert "province" in fields

    def test_workflow_failure_marks_job_failed(self):
        req = _authed_request(method="POST", body=self._valid_body())
        with _app.test_request_context():
            with _stub_auth(), _stub_rate_limit(), _stub_create_job("j1"):
                with patch.object(gw, "trigger_workflow", side_effect=RuntimeError("boom")):
                    gw.db.collection.return_value.document.return_value.update = MagicMock()
                    data, status, _ = _parse_response(handle_investigation(req, {}, "x"))
        assert status == 500
        assert "boom" in data["error"]


# ===========================================================================
# proxy_chat_request
# ===========================================================================
class TestProxyChatRequest:
    def test_happy_path(self):
        req = _authed_request(method="POST", body={"message": "hello"})
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"reply": "hi"}
        mock_resp.status_code = 200

        with _app.test_request_context():
            with _stub_auth(), patch.object(gw, "retry_with_backoff", return_value=mock_resp):
                data, status, _ = _parse_response(proxy_chat_request(req, {}, "https://chat.example.com", "Chat"))
        assert status == 200
        assert data["reply"] == "hi"

    def test_auth_failure(self):
        req = _make_request(method="POST", headers={})
        with _app.test_request_context():
            data, status, _ = _parse_response(proxy_chat_request(req, {}, "https://chat.example.com", "Chat"))
        assert status == 401

    def test_oversized_body(self):
        req = _authed_request(method="POST", content_length=600_000)
        with _app.test_request_context():
            with _stub_auth():
                data, status, _ = _parse_response(proxy_chat_request(req, {}, "https://chat.example.com", "Chat"))
        assert status == 413

    def test_target_url_not_configured(self):
        req = _authed_request(method="POST", body={"message": "hi"})
        with _app.test_request_context():
            with _stub_auth():
                data, status, _ = _parse_response(proxy_chat_request(req, {}, "", "Chat"))
        assert status == 500
        assert "not configured" in data["error"]

    def test_job_ownership_check(self):
        """When job_id is in the body, ownership is verified."""
        req = _authed_request(method="POST", body={"job_id": "j1", "message": "hi"})
        with _app.test_request_context():
            with _stub_auth("u1"), _stub_get_job({"user_id": "u-other"}):
                data, status, _ = _parse_response(proxy_chat_request(req, {}, "https://chat.example.com", "Chat"))
        assert status == 403

    def test_job_not_found_for_ownership(self):
        req = _authed_request(method="POST", body={"job_id": "j1", "message": "hi"})
        with _app.test_request_context():
            with _stub_auth(), _stub_get_job(None):
                data, status, _ = _parse_response(proxy_chat_request(req, {}, "https://chat.example.com", "Chat"))
        assert status == 404

    def test_legacy_job_without_user_id_chat_denied(self):
        """Jobs without user_id cannot use chat proxy."""
        req = _authed_request(method="POST", body={"job_id": "legacy1", "message": "hi"})
        legacy_job = {"status": "complete"}  # no user_id key

        with _app.test_request_context():
            with _stub_auth("u1"), _stub_get_job(legacy_job):
                data, status, _ = _parse_response(proxy_chat_request(req, {}, "https://chat.example.com", "Chat"))
        assert status == 403
        assert "owner" in data["error"].lower() or "denied" in data["error"].lower()

    def test_proxy_posts_google_identity_token_to_chat_backend(self):
        """Downstream POST must include Authorization from _id_token_for_url (Cloud Run IAM)."""
        req = _authed_request(method="POST", body={"message": "hi"})
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"reply": "x"}
        mock_resp.status_code = 200
        post_kw: dict = {}

        def passthrough_retry(fn, *args, **kwargs):
            return fn()

        def capture_post(url, **kwargs):
            post_kw.update(kwargs)
            return mock_resp

        with _app.test_request_context():
            with (
                _stub_auth(),
                patch.object(gw, "_id_token_for_url", return_value="mock-google-id-token") as mock_id_tok,
                patch.object(gw, "retry_with_backoff", side_effect=passthrough_retry),
                patch.object(gw.requests, "post", side_effect=capture_post),
            ):
                data, status, _ = _parse_response(proxy_chat_request(req, {}, "https://chat.example.com", "Chat"))
        assert status == 200
        mock_id_tok.assert_called_once_with("https://chat.example.com")
        assert post_kw["headers"]["Authorization"] == "Bearer mock-google-id-token"

    def test_service_failure(self):
        import requests as req_lib

        req = _authed_request(method="POST", body={"message": "hi"})
        with _app.test_request_context():
            with (
                _stub_auth(),
                patch.object(
                    gw,
                    "retry_with_backoff",
                    side_effect=req_lib.exceptions.ConnectionError("down"),
                ),
            ):
                data, status, _ = _parse_response(proxy_chat_request(req, {}, "https://chat.example.com", "Chat"))
        assert status == 500
        assert "failed" in data["error"].lower()


# ===========================================================================
# Prefill session (extension + redeem)
# ===========================================================================
class TestPrefillSession:
    def test_validate_prefill_requires_one_field(self):
        payload, errors = validate_prefill_payload({})
        assert payload is None
        assert any(e.get("field") == "_all" for e in errors)

    def test_validate_prefill_email_only(self):
        payload, errors = validate_prefill_payload({"email": "john@example.com"})
        assert errors == []
        assert payload["email"] == "john@example.com"

    def test_handle_prefill_create_unauthorized(self):
        req = _make_request(
            method="POST",
            path="/extension/prefill-session",
            body={"email": "john@example.com"},
        )
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_extension_prefill_session(req, {}))
        assert status == 401

    def test_handle_prefill_create_success(self):
        req = _make_request(
            method="POST",
            path="/extension/prefill-session",
            body={"email": "john@example.com"},
        )
        req.headers = {"X-Extension-Prefill-Secret": "test-extension-prefill-secret"}
        mock_set = MagicMock()
        gw.db.collection.return_value.document.return_value.set = mock_set
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_extension_prefill_session(req, {}))
        assert status == 200
        assert "token" in data
        mock_set.assert_called_once()

    def test_handle_prefill_redeem_not_found(self):
        mock_snap = MagicMock()
        mock_snap.exists = False
        gw.db.collection.return_value.document.return_value.get.return_value = mock_snap

        req = _make_request(method="POST", path="/prefill-session/redeem", body={"token": "missing"})
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_prefill_session_redeem(req, {}))
        assert status == 404

    def test_handle_prefill_redeem_success(self):
        doc = {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "city": "Toronto",
            "company_name": "",
            "province": "",
            "expire_at": None,
        }
        mock_snap = MagicMock()
        mock_snap.exists = True
        mock_snap.to_dict.return_value = doc
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_snap
        mock_ref.delete = MagicMock()
        gw.db.collection.return_value.document.return_value = mock_ref

        req = _make_request(method="POST", path="/prefill-session/redeem", body={"token": "abc"})
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_prefill_session_redeem(req, {}))
        assert status == 200
        assert data["email"] == "jane@example.com"
        mock_ref.delete.assert_called_once()

    def test_prefill_doc_expired_none(self):
        assert _prefill_doc_expired(None) is False

    def test_prefill_doc_expired_datetime_past(self):
        past = datetime.now(UTC) - timedelta(hours=1)
        assert _prefill_doc_expired(past) is True

    def test_prefill_doc_expired_datetime_future(self):
        future = datetime.now(UTC) + timedelta(hours=1)
        assert _prefill_doc_expired(future) is False

    def test_validate_prefill_camel_case(self):
        payload, errors = validate_prefill_payload({"fullName": "Jo Smith", "companyName": "Acme"})
        assert errors == []
        assert payload["full_name"] == "Jo Smith"
        assert payload["company_name"] == "Acme"

    def test_validate_prefill_invalid_province(self):
        payload, errors = validate_prefill_payload({"email": "a@b.com", "province": "XX"})
        assert payload is None
        assert any(e["field"] == "province" for e in errors)

    def test_validate_prefill_company_too_long(self):
        payload, errors = validate_prefill_payload({"email": "a@b.com", "company_name": "x" * 201})
        assert payload is None
        assert any(e["field"] == "company_name" for e in errors)

    def test_validate_prefill_email_invalid(self):
        payload, errors = validate_prefill_payload({"email": "not-an-email"})
        assert payload is None
        assert any(e["field"] == "email" for e in errors)

    def test_validate_prefill_full_name_too_short(self):
        payload, errors = validate_prefill_payload({"full_name": "A"})
        assert payload is None
        assert any(e["field"] == "full_name" for e in errors)

    def test_validate_prefill_invalid_city(self):
        payload, errors = validate_prefill_payload({"email": "a@b.com", "city": "City123"})
        assert payload is None
        assert any(e["field"] == "city" for e in errors)

    def test_prefill_doc_expired_timestamp_raises(self):
        bad_ts = MagicMock()
        bad_ts.timestamp = MagicMock(side_effect=ValueError("bad ts"))
        assert _prefill_doc_expired(bad_ts) is True

    def test_handle_prefill_redeem_expired_doc_delete_raises(self):
        past = datetime.now(UTC) - timedelta(minutes=1)
        doc = {"email": "j@example.com", "expire_at": past}
        mock_snap = MagicMock()
        mock_snap.exists = True
        mock_snap.to_dict.return_value = doc
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_snap
        mock_ref.delete = MagicMock(side_effect=RuntimeError("delete failed"))
        gw.db.collection.return_value.document.return_value = mock_ref

        req = _make_request(method="POST", path="/prefill-session/redeem", body={"token": "abc"})
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_prefill_session_redeem(req, {}))
        assert status == 404
        mock_ref.delete.assert_called_once()

    def test_handle_prefill_create_oversized_body(self):
        req = _make_request(
            method="POST",
            path="/extension/prefill-session",
            content_length=20_000,
            body={"email": "a@b.com"},
        )
        req.headers = {"X-Extension-Prefill-Secret": "test-extension-prefill-secret"}
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_extension_prefill_session(req, {}))
        assert status == 413

    def test_handle_prefill_create_invalid_json(self):
        req = _make_request(
            method="POST",
            path="/extension/prefill-session",
            bad_json=True,
        )
        req.headers = {"X-Extension-Prefill-Secret": "test-extension-prefill-secret"}
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_extension_prefill_session(req, {}))
        assert status == 400

    def test_handle_prefill_create_firestore_failure(self):
        req = _make_request(
            method="POST",
            path="/extension/prefill-session",
            body={"email": "john@example.com"},
        )
        req.headers = {"X-Extension-Prefill-Secret": "test-extension-prefill-secret"}
        with _app.test_request_context():
            with patch.object(gw, "retry_with_backoff", side_effect=RuntimeError("fs")):
                data, status, _ = _parse_response(handle_extension_prefill_session(req, {}))
        assert status == 500

    def test_handle_prefill_redeem_oversized_body(self):
        req = _make_request(
            method="POST",
            path="/prefill-session/redeem",
            content_length=10_000,
            body={"token": "x"},
        )
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_prefill_session_redeem(req, {}))
        assert status == 413

    def test_handle_prefill_redeem_invalid_json(self):
        req = _make_request(method="POST", path="/prefill-session/redeem", bad_json=True)
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_prefill_session_redeem(req, {}))
        assert status == 400

    def test_handle_prefill_redeem_invalid_token_length(self):
        req = _make_request(
            method="POST",
            path="/prefill-session/redeem",
            body={"token": "x" * 600},
        )
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_prefill_session_redeem(req, {}))
        assert status == 400

    def test_handle_prefill_redeem_firestore_read_error(self):
        gw.db.collection.return_value.document.return_value.get.side_effect = RuntimeError("db")
        req = _make_request(method="POST", path="/prefill-session/redeem", body={"token": "tok"})
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_prefill_session_redeem(req, {}))
        assert status == 500
        gw.db.collection.return_value.document.return_value.get.side_effect = None

    def test_handle_prefill_redeem_expired_doc(self):
        past = datetime.now(UTC) - timedelta(minutes=1)
        doc = {
            "full_name": "X",
            "email": "j@example.com",
            "expire_at": past,
        }
        mock_snap = MagicMock()
        mock_snap.exists = True
        mock_snap.to_dict.return_value = doc
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_snap
        mock_ref.delete = MagicMock()
        gw.db.collection.return_value.document.return_value = mock_ref

        req = _make_request(method="POST", path="/prefill-session/redeem", body={"token": "abc"})
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_prefill_session_redeem(req, {}))
        assert status == 404
        mock_ref.delete.assert_called_once()

    def test_handle_prefill_redeem_delete_fails(self):
        doc = {
            "full_name": "Jane",
            "email": "j@example.com",
            "expire_at": None,
        }
        mock_snap = MagicMock()
        mock_snap.exists = True
        mock_snap.to_dict.return_value = doc
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_snap
        mock_ref.delete = MagicMock(side_effect=RuntimeError("delete failed"))
        gw.db.collection.return_value.document.return_value = mock_ref

        req = _make_request(method="POST", path="/prefill-session/redeem", body={"token": "abc"})
        with _app.test_request_context():
            data, status, _ = _parse_response(handle_prefill_session_redeem(req, {}))
        assert status == 500


# ===========================================================================
# Main routing
# ===========================================================================
class TestMainRouting:
    def test_options_cors_preflight(self):
        req = _make_request(method="OPTIONS", headers={"Origin": "https://app.com"})
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"):
                resp = main_handler(req)
        body, status, hdrs = resp
        assert status == 204
        assert "Access-Control-Allow-Methods" in hdrs

    def test_options_preflight_allowlist_matching_origin(self):
        origin = "https://app.example.com"
        req = _make_request(method="OPTIONS", headers={"Origin": origin})
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", f"{origin},https://other.com"):
                resp = main_handler(req)
        _, status, hdrs = resp
        assert status == 204
        assert hdrs.get("Access-Control-Allow-Origin") == origin
        assert "*" not in hdrs.get("Access-Control-Allow-Origin", "")

    def test_options_preflight_allowlist_non_matching_origin(self):
        req = _make_request(
            method="OPTIONS",
            headers={"Origin": "https://evil.com"},
        )
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "https://app.example.com"):
                resp = main_handler(req)
        _, status, hdrs = resp
        assert status == 204
        assert "Access-Control-Allow-Origin" not in hdrs

    def test_health_check(self):
        req = _make_request(method="GET", path="/health")
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 200
        assert data == {"status": "healthy", "service": "api_gateway"}

    def test_root_health_check(self):
        req = _make_request(method="GET", path="/")
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 200
        assert data == {"status": "healthy", "service": "api_gateway"}

    def test_unknown_path(self):
        req = _make_request(method="GET", path="/nonexistent")
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 404

    def test_post_extension_prefill_routes(self):
        req = _make_request(method="POST", path="/extension/prefill-session", body={"email": "a@b.com"})
        req.headers = {"X-Extension-Prefill-Secret": "test-extension-prefill-secret"}
        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                patch.object(gw, "handle_extension_prefill_session", return_value=("ok", 200, {})) as mock_h,
            ):
                main_handler(req)
        mock_h.assert_called_once()

    def test_post_prefill_redeem_routes(self):
        req = _make_request(method="POST", path="/prefill-session/redeem", body={"token": "x"})
        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                patch.object(gw, "handle_prefill_session_redeem", return_value=("ok", 200, {})) as mock_h,
            ):
                main_handler(req)
        mock_h.assert_called_once()

    def test_post_investigate_skiptrace_routes_correctly(self):
        req = _authed_request(method="POST", path="/investigate-skiptrace")
        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                patch.object(gw, "handle_investigation", return_value=("ok", 202, {})) as mock_hi,
            ):
                main_handler(req)
        mock_hi.assert_called_once()
        # Second arg is headers dict, third is workflow name
        assert mock_hi.call_args[0][2] == gw.SKIPTRACE_WORKFLOW_NAME

    def test_post_investigate_origination_routes_correctly(self):
        req = _authed_request(method="POST", path="/investigate-origination")
        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                patch.object(gw, "handle_investigation", return_value=("ok", 202, {})) as mock_hi,
            ):
                main_handler(req)
        mock_hi.assert_called_once()
        assert mock_hi.call_args[0][2] == gw.ORIGINATION_WORKFLOW_NAME

    def test_get_jobs_returns_job(self):
        now = datetime.utcnow()
        job = {"status": "pending", "created_at": now}
        req = _make_request(method="GET", path="/jobs/j1")
        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                patch.object(gw, "verify_job_ownership", return_value=(job, None, None)),
            ):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 200
        assert data["job_id"] == "j1"
        assert data["status"] == "pending"

    def test_get_jobs_empty_id(self):
        req = _make_request(method="GET", path="/jobs/")
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 400

    def test_get_markdown_returns_reports(self):
        job = {"markdown_reports": {"identity": "# Identity\nJohn Smith"}}
        req = _make_request(method="GET", path="/get_markdown/j1")
        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                patch.object(gw, "verify_job_ownership", return_value=(job, None, None)),
            ):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 200
        assert data["identity"] == "# Identity\nJohn Smith"

    def test_get_markdown_no_reports(self):
        job = {"markdown_reports": {}}
        req = _make_request(method="GET", path="/get_markdown/j1")
        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                patch.object(gw, "verify_job_ownership", return_value=(job, None, None)),
            ):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 404

    def test_post_chat_handler_routes_to_proxy(self):
        req = _authed_request(method="POST", path="/chat_handler", body={"message": "hi"})
        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                patch.object(gw, "proxy_chat_request", return_value=("ok", 200, {})) as mock_proxy,
            ):
                main_handler(req)
        mock_proxy.assert_called_once()
        assert mock_proxy.call_args[0][2] == gw.CHAT_HANDLER_URL

    def test_post_chat_handler_origination_routes_to_proxy(self):
        req = _authed_request(method="POST", path="/chat_handler_origination", body={"message": "hi"})
        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                patch.object(gw, "proxy_chat_request", return_value=("ok", 200, {})) as mock_proxy,
            ):
                main_handler(req)
        mock_proxy.assert_called_once()
        assert mock_proxy.call_args[0][2] == gw.CHAT_HANDLER_ORIGINATION_URL

    def test_post_feedback_happy_path(self):
        job = {"user_id": "u1", "status": "complete"}
        req = _authed_request(
            method="POST",
            path="/jobs/j1/feedback",
            body={"rating": "positive", "comment": "Great results"},
        )
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"), _stub_auth("u1"), _stub_get_job(job):
                gw.db.collection.return_value.document.return_value.update = MagicMock()
                data, status, _ = _parse_response(main_handler(req))
        assert status == 200
        assert data["status"] == "ok"

    def test_post_feedback_invalid_rating(self):
        job = {"user_id": "u1", "status": "complete"}
        req = _authed_request(
            method="POST",
            path="/jobs/j1/feedback",
            body={"rating": "neutral", "comment": "ok"},
        )
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"), _stub_auth("u1"), _stub_get_job(job):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 400
        assert "positive" in data["error"] or "negative" in data["error"]

    def test_post_feedback_comment_too_long(self):
        job = {"user_id": "u1", "status": "complete"}
        req = _authed_request(
            method="POST",
            path="/jobs/j1/feedback",
            body={"rating": "positive", "comment": "x" * 1001},
        )
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"), _stub_auth("u1"), _stub_get_job(job):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 400

    def test_post_feedback_wrong_user(self):
        job = {"user_id": "u-owner", "status": "complete"}
        req = _authed_request(
            method="POST",
            path="/jobs/j1/feedback",
            body={"rating": "positive", "comment": ""},
        )
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"), _stub_auth("u-other"), _stub_get_job(job):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 403

    def test_post_address_verification_happy_path(self):
        req = _authed_request(
            method="POST",
            path="/address-verification",
            body={
                "business_name": "Acme Inc",
                "street_address": "123 Main St",
                "city": "Toronto",
                "province": "ON",
            },
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"verified": True}
        mock_resp.status_code = 200

        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                _stub_auth(),
                patch.object(gw, "retry_with_backoff", return_value=mock_resp),
            ):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 200
        assert data["verified"] is True

    def test_post_address_verification_missing_fields(self):
        req = _authed_request(
            method="POST",
            path="/address-verification",
            body={"business_name": "Acme Inc"},  # no address fields
        )
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"), _stub_auth():
                data, status, _ = _parse_response(main_handler(req))
        assert status == 400

    def test_post_address_verification_missing_business_name(self):
        req = _authed_request(
            method="POST",
            path="/address-verification",
            body={"address": "123 Main St, Toronto, ON"},
        )
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"), _stub_auth():
                data, status, _ = _parse_response(main_handler(req))
        assert status == 400
        assert "business_name" in data["error"]

    # --- Address verification proxy: additional tests ---

    def test_address_verification_not_configured(self):
        """Returns 500 when ADDRESS_VERIFICATION_URL is empty."""
        req = _authed_request(
            method="POST",
            path="/address-verification",
            body={"address": "123 Main St, Toronto, ON", "business_name": "Acme"},
        )
        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                _stub_auth(),
                patch.object(gw, "ADDRESS_VERIFICATION_URL", ""),
            ):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 500
        assert "not configured" in data["error"]

    def test_address_verification_request_exception(self):
        """Returns 500 when the downstream service call fails."""
        import requests as req_lib

        req = _authed_request(
            method="POST",
            path="/address-verification",
            body={"address": "123 Main St, Toronto, ON", "business_name": "Acme"},
        )
        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                _stub_auth(),
                patch.object(gw, "retry_with_backoff", side_effect=req_lib.exceptions.ConnectionError("down")),
            ):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 500
        assert "failed" in data["error"].lower()

    def test_address_verification_invalid_json(self):
        """Returns 400 when request body is not valid JSON."""
        req = _authed_request(
            method="POST",
            path="/address-verification",
            bad_json=True,
        )
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"), _stub_auth():
                data, status, _ = _parse_response(main_handler(req))
        assert status == 400

    def test_address_verification_combined_address_field(self):
        """Accepts the legacy combined address field format."""
        req = _authed_request(
            method="POST",
            path="/address-verification",
            body={"address": "123 Main St, Toronto, ON M5H 2N2", "business_name": "Acme"},
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"verified": True}
        mock_resp.status_code = 200

        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                _stub_auth(),
                patch.object(gw, "retry_with_backoff", return_value=mock_resp),
            ):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 200
        assert data["verified"] is True

    # --- Feedback route: additional tests ---

    def test_feedback_invalid_path_format(self):
        """Malformed feedback path returns 400."""
        req = _authed_request(
            method="POST",
            path="/jobs/j1/feedback/extra",
            body={"rating": "positive", "comment": ""},
        )
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"), _stub_auth():
                data, status, _ = _parse_response(main_handler(req))
        # Either 400 (invalid feedback path) or 404 (doesn't match any route)
        assert status in (400, 404)

    def test_feedback_invalid_json(self):
        """Returns 400 when feedback request body is not valid JSON."""
        job = {"user_id": "user-123", "status": "complete"}
        req = _authed_request(
            method="POST",
            path="/jobs/j1/feedback",
            bad_json=True,
        )
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"), _stub_auth(), _stub_get_job(job):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 400

    def test_feedback_firestore_exception(self):
        """Returns 500 when Firestore update fails."""
        job = {"user_id": "user-123", "status": "complete"}
        req = _authed_request(
            method="POST",
            path="/jobs/j1/feedback",
            body={"rating": "positive", "comment": "great"},
        )
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"), _stub_auth(), _stub_get_job(job):
                gw.db.collection.return_value.document.return_value.update.side_effect = RuntimeError(
                    "Firestore write failed"
                )
                data, status, _ = _parse_response(main_handler(req))
                gw.db.collection.return_value.document.return_value.update.side_effect = None
        assert status == 500
        assert "Failed to save feedback" in data["error"]

    # --- GET /get_markdown: additional tests ---

    def test_get_markdown_empty_job_id(self):
        """Returns 400 when job_id is empty."""
        req = _make_request(method="GET", path="/get_markdown/")
        with _app.test_request_context():
            with patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 400

    def test_get_markdown_exception(self):
        """Returns 500 when an exception occurs during markdown retrieval."""
        req = _make_request(method="GET", path="/get_markdown/j1")
        with _app.test_request_context():
            with (
                patch.object(gw, "CORS_ALLOWED_ORIGINS", "*"),
                patch.object(gw, "verify_job_ownership", side_effect=RuntimeError("db error")),
            ):
                data, status, _ = _parse_response(main_handler(req))
        assert status == 500
        assert "db error" in data["error"]


# ===========================================================================
# Retry coverage tests
# ===========================================================================
class TestTriggerWorkflowRetry:
    """Tests for retry logic in trigger_workflow."""

    def test_transient_failure_retried(self):
        """Workflow trigger retries on transient 503."""
        mock_client_instance = MagicMock()
        mock_execution = MagicMock()
        mock_execution.name = "projects/p/locations/l/workflows/w/executions/abc"

        mock_client_instance.create_execution.side_effect = [
            Exception("503 Service Unavailable"),
            mock_execution,
        ]

        with (
            patch.object(gw, "PROJECT_ID", "test-project"),
            patch.object(gw, "LOCATION", "us-central1"),
            patch.object(gw.executions_v1, "ExecutionsClient", return_value=mock_client_instance),
            patch.object(gw.executions_v1, "Execution", return_value=MagicMock()),
            patch("retry_utils.time.sleep"),
        ):
            result = trigger_workflow("j1", "a@b.com", "John Smith", "Toronto", "ON")

        assert result == mock_execution.name
        assert mock_client_instance.create_execution.call_count == 2


class TestCreateJobRetry:
    """Tests for retry logic in create_job."""

    def test_firestore_transient_failure_retried(self):
        """create_job retries Firestore set on transient failure."""
        call_count = [0]

        def set_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("503 Service Unavailable")

        gw.db.collection.return_value.document.return_value.set.side_effect = set_side_effect

        with patch("retry_utils.time.sleep"):
            job_id = create_job("a@b.com", "John Smith", "Toronto", "ON")

        assert job_id is not None
        assert call_count[0] == 2

        # Restore
        gw.db.collection.return_value.document.return_value.set.side_effect = None
