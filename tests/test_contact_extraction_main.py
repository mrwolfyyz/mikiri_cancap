"""Tests for the contact_extraction Cloud Function HTTP handler.

The heavy lifting (LLM call, normalization, dedup) is tested in
test_contact_extraction_utils.py.  These tests cover the thin HTTP
wrapper in gcp/functions/contact_extraction/main.py: JSON parsing,
input validation, delegation to extract_contact_info_llm, and error
responses.
"""

import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Mock heavy dependencies before loading the module
# ---------------------------------------------------------------------------
_mock_ff = MagicMock()
_mock_ff.http = lambda f: f
sys.modules.setdefault("functions_framework", _mock_ff)

_mock_vertexai = MagicMock()
_mock_gen_models = MagicMock()
sys.modules.setdefault("vertexai", _mock_vertexai)
sys.modules.setdefault("vertexai.generative_models", _mock_gen_models)

# ---------------------------------------------------------------------------
# Load contact_extraction/main.py
# ---------------------------------------------------------------------------
from conftest import load_function_module

ce_main = load_function_module("contact_extraction", "contact_extraction_main")
main_handler = ce_main.main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_request(body=None, *, bad_json=False):
    req = MagicMock()
    if bad_json:
        req.get_json.side_effect = Exception("bad JSON")
    else:
        req.get_json.return_value = body
    return req


# ===========================================================================
# Tests
# ===========================================================================
class TestMainHandler:
    """Tests for the HTTP entry point."""

    def test_valid_request(self):
        body = {
            "job_id": "abc123",
            "identity": {
                "seed": {"full_name": "John Smith", "email": "john@example.com"},
                "queries": [{"hits": [{"title": "test", "snippet": "info", "url": "https://example.com"}]}],
            },
        }
        fake_contacts = {"phones": [{"number_digits": "4165551234"}], "emails": [], "addresses": []}

        with patch.object(ce_main, "extract_contact_info_llm", return_value=fake_contacts) as mock_extract:
            result, status, _ = main_handler(_make_request(body))

        assert status == 200
        assert result["contacts"] == fake_contacts
        # Verify correct args passed to the extraction function
        mock_extract.assert_called_once()
        call_args = mock_extract.call_args
        assert call_args[0][0] == body["identity"]["queries"]  # queries
        assert call_args[0][1] == body["identity"]["seed"]     # seed
        assert call_args[0][2] == "john@example.com"           # exclude_email

    def test_exclude_email_from_seed(self):
        """exclude_email is extracted from seed.email."""
        body = {
            "job_id": "x",
            "identity": {
                "seed": {"full_name": "Jane", "email": "jane@test.com"},
                "queries": [{"hits": [{"title": "t"}]}],
            },
        }
        with patch.object(ce_main, "extract_contact_info_llm", return_value={"phones": [], "emails": [], "addresses": []}) as mock_extract:
            main_handler(_make_request(body))

        assert mock_extract.call_args[0][2] == "jane@test.com"

    def test_no_seed_email_passes_none(self):
        """If seed has no email, exclude_email should be None."""
        body = {
            "job_id": "x",
            "identity": {
                "seed": {"full_name": "Jane"},
                "queries": [{"hits": [{"title": "t"}]}],
            },
        }
        with patch.object(ce_main, "extract_contact_info_llm", return_value={"phones": [], "emails": [], "addresses": []}) as mock_extract:
            main_handler(_make_request(body))

        assert mock_extract.call_args[0][2] is None

    def test_missing_identity(self):
        result, status = main_handler(_make_request({"job_id": "x"}))
        assert status == 400
        assert "identity" in result["error"].lower()

    def test_empty_identity(self):
        result, status = main_handler(_make_request({"identity": {}}))
        assert status == 400

    def test_invalid_json(self):
        result, status = main_handler(_make_request(bad_json=True))
        assert status == 400

    def test_extraction_raises_returns_500(self):
        body = {
            "job_id": "x",
            "identity": {
                "seed": {"full_name": "John"},
                "queries": [{"hits": [{"title": "t"}]}],
            },
        }
        with patch.object(ce_main, "extract_contact_info_llm", side_effect=RuntimeError("LLM error")):
            result, status = main_handler(_make_request(body))

        assert status == 500
        assert "LLM error" in result["error"]
        assert result["error_type"] == "RuntimeError"

    def test_missing_queries_and_seed_default_to_empty(self):
        """If identity has no queries/seed keys, they default to empty."""
        body = {"identity": {"some_key": "value"}}
        with patch.object(ce_main, "extract_contact_info_llm", return_value={"phones": [], "emails": [], "addresses": []}) as mock_extract:
            result, status, _ = main_handler(_make_request(body))

        assert status == 200
        assert mock_extract.call_args[0][0] == []   # queries
        assert mock_extract.call_args[0][1] == {}   # seed
