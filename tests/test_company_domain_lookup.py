"""Tests for the company_domain_lookup Cloud Function (gcp/functions/company_domain_lookup/main.py).

Covers:
- vertex_ai_domain_resolution_grounded (LLM domain resolution with retry)
- main HTTP handler (CORS, validation, Firestore read/update, domain cleaning, error paths)
"""

import json
import sys
from unittest.mock import MagicMock, patch

import flask

# ---------------------------------------------------------------------------
# Mock heavy dependencies BEFORE loading the module
# ---------------------------------------------------------------------------
_mock_ff = MagicMock()
_mock_ff.http = lambda f: f
sys.modules.setdefault("functions_framework", _mock_ff)

# Mock Google Gen AI SDK
_mock_genai = MagicMock()
_mock_genai_types = MagicMock()

if "google" not in sys.modules:
    _mock_google = MagicMock()
    sys.modules["google"] = _mock_google
else:
    _mock_google = sys.modules["google"]
_mock_google.genai = _mock_genai

sys.modules["google.genai"] = _mock_genai
sys.modules["google.genai.types"] = _mock_genai_types

# Mock google.cloud.firestore (module-level: db = firestore.Client())
_mock_gc_firestore = MagicMock()
sys.modules.setdefault("google.cloud", MagicMock())
sys.modules.setdefault("google.cloud.firestore", _mock_gc_firestore)

# ---------------------------------------------------------------------------
# Load company_domain_lookup/main.py
# ---------------------------------------------------------------------------
from conftest import load_function_module

cdl_main = load_function_module("company_domain_lookup", "company_domain_lookup_main")

vertex_ai_domain_resolution_grounded = cdl_main.vertex_ai_domain_resolution_grounded
main_handler = cdl_main.main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_app = flask.Flask(__name__)


def _make_request(body=None, *, bad_json=False, method="POST"):
    req = MagicMock()
    req.method = method
    if bad_json:
        req.get_json.side_effect = Exception("bad JSON")
    else:
        req.get_json.return_value = body
    return req


def _mock_llm_response(data):
    """Return a mock GenAI response whose .text is JSON."""
    resp = MagicMock()
    resp.text = json.dumps(data)
    return resp


def _valid_domain_result():
    return {
        "domain": "acmecorp.com",
        "confidence": "high",
        "rationale": "Found official website in search results.",
    }


# ===========================================================================
# vertex_ai_domain_resolution_grounded
# ===========================================================================
class TestVertexAiDomainResolutionGrounded:
    """Tests for the LLM-based domain resolution."""

    def test_gcp_project_not_set_returns_error(self):
        with patch.object(cdl_main, "GCP_PROJECT", ""):
            result = vertex_ai_domain_resolution_grounded("Acme Corp")
        assert "error" in result

    def test_successful_resolution(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_llm_response(_valid_domain_result())

        with (
            patch.object(cdl_main, "GCP_PROJECT", "test-project"),
            patch.object(cdl_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_domain_resolution_grounded("Acme Corp")

        assert result["domain"] == "acmecorp.com"
        assert result["confidence"] == "high"

    def test_empty_response_retries_then_returns_error(self):
        mock_response = MagicMock()
        mock_response.text = ""

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(cdl_main, "GCP_PROJECT", "test-project"),
            patch.object(cdl_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_domain_resolution_grounded("Acme Corp")

        # After retries fail, returns {"error": ...}
        assert "error" in result

    def test_markdown_wrapped_json_stripped(self):
        inner = json.dumps(_valid_domain_result())
        mock_response = MagicMock()
        mock_response.text = f"```json\n{inner}\n```"

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(cdl_main, "GCP_PROJECT", "test-project"),
            patch.object(cdl_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_domain_resolution_grounded("Acme Corp")

        assert result["domain"] == "acmecorp.com"

    def test_missing_fields_get_defaults(self):
        mock_response = _mock_llm_response({"domain": "example.com"})

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(cdl_main, "GCP_PROJECT", "test-project"),
            patch.object(cdl_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_domain_resolution_grounded("Acme Corp")

        assert result["confidence"] == "low"  # default
        assert "missing" in result["rationale"].lower() or "completed" in result["rationale"].lower()

    def test_null_domain_normalized_to_empty_string(self):
        """LLM returning domain: null should be normalized to empty string."""
        mock_response = _mock_llm_response(
            {"domain": None, "confidence": "low", "rationale": "Could not determine domain."}
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(cdl_main, "GCP_PROJECT", "test-project"),
            patch.object(cdl_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_domain_resolution_grounded("Meorra Media")

        assert result["domain"] == ""
        assert result["confidence"] == "low"

    def test_invalid_confidence_corrected(self):
        data = _valid_domain_result()
        data["confidence"] = "very_high"
        mock_response = _mock_llm_response(data)

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(cdl_main, "GCP_PROJECT", "test-project"),
            patch.object(cdl_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_domain_resolution_grounded("Acme Corp")

        assert result["confidence"] == "medium"


# ===========================================================================
# main HTTP handler
# ===========================================================================
class TestMainHandler:
    """Tests for the HTTP entry point."""

    def test_cors_options(self):
        req = _make_request(method="OPTIONS")
        with _app.test_request_context():
            body, status, headers = main_handler(req)
        assert status == 204
        assert "Access-Control-Allow-Origin" in headers

    def test_non_post_method(self):
        req = _make_request(method="GET")
        with _app.test_request_context():
            resp, status, headers = main_handler(req)
        assert status == 405

    def test_missing_company_name(self):
        req = _make_request({"job_id": "abc123"})
        with _app.test_request_context():
            resp, status, headers = main_handler(req)
        assert status == 400

    def test_missing_job_id(self):
        req = _make_request({"company_name": "Acme Corp"})
        with _app.test_request_context():
            resp, status, headers = main_handler(req)
        assert status == 400

    def test_invalid_json(self):
        req = _make_request(bad_json=True)
        with _app.test_request_context():
            resp, status, headers = main_handler(req)
        assert status == 400

    def test_job_not_found(self):
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_job_ref = MagicMock()
        mock_job_ref.get.return_value = mock_doc

        mock_db = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_job_ref

        req = _make_request({"company_name": "Acme Corp", "job_id": "nonexistent"})

        with _app.test_request_context(), patch.object(cdl_main, "db", mock_db):
            resp, status, headers = main_handler(req)

        assert status == 404

    def test_successful_domain_lookup(self):
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_job_ref = MagicMock()
        mock_job_ref.get.return_value = mock_doc

        mock_db = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_job_ref

        llm_result = _valid_domain_result()

        req = _make_request({"company_name": "Acme Corp", "job_id": "job123"})

        with (
            _app.test_request_context(),
            patch.object(cdl_main, "db", mock_db),
            patch.object(cdl_main, "vertex_ai_domain_resolution_grounded", return_value=llm_result),
        ):
            resp, status, headers = main_handler(req)

        assert status == 200
        # Verify Firestore was updated
        mock_job_ref.update.assert_called_once()
        update_args = mock_job_ref.update.call_args[0][0]
        assert update_args["input.company_domain"] == "acmecorp.com"
        assert update_args["input.company_domain_confidence"] == "high"

    def test_llm_returns_error(self):
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_job_ref = MagicMock()
        mock_job_ref.get.return_value = mock_doc

        mock_db = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_job_ref

        req = _make_request({"company_name": "Acme Corp", "job_id": "job123"})

        with (
            _app.test_request_context(),
            patch.object(cdl_main, "db", mock_db),
            patch.object(
                cdl_main, "vertex_ai_domain_resolution_grounded", return_value={"error": "GCP_PROJECT not set"}
            ),
        ):
            resp, status, headers = main_handler(req)

        assert status == 200  # Returns 200 with error status

    def test_llm_returns_no_domain(self):
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_job_ref = MagicMock()
        mock_job_ref.get.return_value = mock_doc

        mock_db = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_job_ref

        llm_result = {"domain": "", "confidence": "low", "rationale": "Could not determine"}

        req = _make_request({"company_name": "Acme Corp", "job_id": "job123"})

        with (
            _app.test_request_context(),
            patch.object(cdl_main, "db", mock_db),
            patch.object(cdl_main, "vertex_ai_domain_resolution_grounded", return_value=llm_result),
        ):
            resp, status, headers = main_handler(req)

        assert status == 200

    def test_llm_returns_null_domain(self):
        """LLM returning domain: None should return no_domain, not crash."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_job_ref = MagicMock()
        mock_job_ref.get.return_value = mock_doc

        mock_db = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_job_ref

        llm_result = {"domain": None, "confidence": "low", "rationale": "Could not determine"}

        req = _make_request({"company_name": "Meorra Media", "job_id": "job123"})

        with (
            _app.test_request_context(),
            patch.object(cdl_main, "db", mock_db),
            patch.object(cdl_main, "vertex_ai_domain_resolution_grounded", return_value=llm_result),
        ):
            resp, status, headers = main_handler(req)

        assert status == 200
        data = resp.get_json()
        assert data["status"] == "no_domain"
        mock_job_ref.update.assert_not_called()

    def test_domain_cleaning(self):
        """Domain with protocol, www, and trailing slashes is cleaned."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_job_ref = MagicMock()
        mock_job_ref.get.return_value = mock_doc

        mock_db = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_job_ref

        llm_result = {
            "domain": "https://www.acmecorp.com/about",
            "confidence": "high",
            "rationale": "Found official website.",
        }

        req = _make_request({"company_name": "Acme Corp", "job_id": "job123"})

        with (
            _app.test_request_context(),
            patch.object(cdl_main, "db", mock_db),
            patch.object(cdl_main, "vertex_ai_domain_resolution_grounded", return_value=llm_result),
        ):
            resp, status, headers = main_handler(req)

        assert status == 200
        update_args = mock_job_ref.update.call_args[0][0]
        assert update_args["input.company_domain"] == "acmecorp.com"

    def test_unexpected_error_returns_200(self):
        """Unexpected errors return 200 (domain lookup is optional)."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_job_ref = MagicMock()
        mock_job_ref.get.return_value = mock_doc

        mock_db = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_job_ref

        req = _make_request({"company_name": "Acme Corp", "job_id": "job123"})

        with (
            _app.test_request_context(),
            patch.object(cdl_main, "db", mock_db),
            patch.object(cdl_main, "vertex_ai_domain_resolution_grounded", side_effect=RuntimeError("unexpected")),
        ):
            resp, status, headers = main_handler(req)

        assert status == 200  # Non-fatal: returns 200 so workflow continues
