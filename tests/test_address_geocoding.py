"""Tests for the address_geocoding Cloud Function (gcp/functions/address_geocoding/main.py).

Covers:
- _nominatim_request (Nominatim geocoding API call)
- extract_addresses_from_queries_llm (LLM address extraction with retry)
- main HTTP handler (validation, identity + corporate data, geocoding loop)
"""

import json
import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Mock heavy dependencies BEFORE loading the module
# ---------------------------------------------------------------------------
_mock_ff = MagicMock()
_mock_ff.http = lambda f: f
sys.modules.setdefault("functions_framework", _mock_ff)

# Mock Google Gen AI SDK (google.genai, google.genai.types)
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

# ---------------------------------------------------------------------------
# Load address_geocoding/main.py
# ---------------------------------------------------------------------------
from conftest import load_function_module

ag_main = load_function_module("address_geocoding", "address_geocoding_main")

_nominatim_request = ag_main._nominatim_request
extract_addresses_from_queries_llm = ag_main.extract_addresses_from_queries_llm
main_handler = ag_main.main


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
# _nominatim_request
# ===========================================================================
class TestNominatimRequest:
    """Tests for the low-level Nominatim geocoding call."""

    def test_successful_geocode(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([{"lat": "43.6532", "lon": "-79.3832"}]).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("address_geocoding_main.urlopen", return_value=mock_resp):
            lat, lon = _nominatim_request("123 Main St, Toronto")

        assert lat == 43.6532
        assert lon == -79.3832

    def test_no_results(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([]).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("address_geocoding_main.urlopen", return_value=mock_resp):
            lat, lon = _nominatim_request("nonexistent address")

        assert lat is None
        assert lon is None

    def test_network_error_raises(self):
        """Network errors should propagate for retry_with_backoff to handle."""
        from urllib.error import URLError

        with patch("address_geocoding_main.urlopen", side_effect=URLError("timeout")):
            try:
                _nominatim_request("123 Main St")
                assert False, "Should have raised URLError"
            except URLError:
                pass


# ===========================================================================
# extract_addresses_from_queries_llm
# ===========================================================================
class TestExtractAddressesFromQueriesLlm:
    """Tests for LLM-based address extraction."""

    def test_no_gcp_project(self):
        with patch.object(ag_main, "GCP_PROJECT", ""):
            result = extract_addresses_from_queries_llm([{"hits": [{"title": "test"}]}])
        assert result == []

    def test_no_hits(self):
        result = extract_addresses_from_queries_llm([{"hits": []}])
        assert result == []

    def test_empty_queries(self):
        result = extract_addresses_from_queries_llm([])
        assert result == []

    def test_successful_extraction(self):
        llm_response = {
            "addresses": [
                {
                    "address_raw": "123 Main St, Toronto, ON M5H 2N2",
                    "confidence": "high",
                    "source_url": "https://example.com",
                    "snippet": "John lives at 123 Main St",
                },
                {
                    "address_raw": "456 Elm St, Ottawa, ON K1A 0B1",
                    "confidence": "medium",
                    "source_url": "https://other.com",
                    "snippet": "Office at 456 Elm",
                },
            ]
        }

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps(llm_response)
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(ag_main, "GCP_PROJECT", "test-project"),
            patch.object(ag_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = extract_addresses_from_queries_llm(
                [{"hits": [{"title": "test", "snippet": "123 Main St", "url": "https://example.com"}]}]
            )

        assert len(result) == 2
        assert "123 Main St" in result[0]["address_raw"]

    def test_deduplication(self):
        """Duplicate addresses should be removed."""
        llm_response = {
            "addresses": [
                {"address_raw": "123 Main St, Toronto, ON", "source_url": "https://a.com", "snippet": ""},
                {"address_raw": "123 Main St, Toronto, ON", "source_url": "https://b.com", "snippet": ""},
            ]
        }

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps(llm_response)
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(ag_main, "GCP_PROJECT", "test-project"),
            patch.object(ag_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = extract_addresses_from_queries_llm(
                [{"hits": [{"title": "t", "snippet": "s", "url": "https://a.com"}]}]
            )

        assert len(result) == 1

    def test_markdown_wrapped_response(self):
        inner = json.dumps(
            {"addresses": [{"address_raw": "789 Oak Ave, Vancouver, BC", "source_url": "https://c.com", "snippet": ""}]}
        )
        response_text = f"```json\n{inner}\n```"

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = response_text
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(ag_main, "GCP_PROJECT", "test-project"),
            patch.object(ag_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = extract_addresses_from_queries_llm(
                [{"hits": [{"title": "t", "snippet": "s", "url": "https://c.com"}]}]
            )

        assert len(result) == 1

    def test_empty_llm_response_returns_empty(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = ""
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(ag_main, "GCP_PROJECT", "test-project"),
            patch.object(ag_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = extract_addresses_from_queries_llm(
                [{"hits": [{"title": "t", "snippet": "s", "url": "https://a.com"}]}]
            )

        assert result == []

    def test_malformed_json_response_returns_empty(self):
        """Non-JSON LLM response returns empty list instead of crashing."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "this is not valid json at all"
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(ag_main, "GCP_PROJECT", "test-project"),
            patch.object(ag_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = extract_addresses_from_queries_llm(
                [{"hits": [{"title": "t", "snippet": "s", "url": "https://a.com"}]}]
            )

        assert result == []

    def test_address_with_accented_characters(self):
        """Addresses with accented characters are preserved correctly."""
        llm_response = {
            "addresses": [
                {
                    "address_raw": "123 Rue Sainte-Hélène, Montréal, QC",
                    "confidence": "high",
                    "source_url": "https://example.com",
                    "snippet": "Located at 123 Rue Sainte-Hélène",
                },
            ]
        }

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps(llm_response, ensure_ascii=False)
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(ag_main, "GCP_PROJECT", "test-project"),
            patch.object(ag_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = extract_addresses_from_queries_llm(
                [{"hits": [{"title": "t", "snippet": "Montréal", "url": "https://example.com"}]}]
            )

        assert len(result) == 1
        assert "Montréal" in result[0]["address_raw"]

    def test_genai_client_error_returns_empty(self):
        """If genai.Client() raises, the function returns empty list."""
        with (
            patch.object(ag_main, "GCP_PROJECT", "test-project"),
            patch.object(ag_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.side_effect = RuntimeError("auth error")
            result = extract_addresses_from_queries_llm(
                [{"hits": [{"title": "t", "snippet": "s", "url": "https://a.com"}]}]
            )

        assert result == []


# ===========================================================================
# main HTTP handler
# ===========================================================================
class TestMainHandler:
    """Tests for the HTTP entry point."""

    def test_invalid_json(self):
        result, status = main_handler(_make_request(bad_json=True))
        assert status == 400

    def test_no_queries_no_corporate(self):
        """No data to process → empty result."""
        result, status = main_handler(_make_request({}))
        assert status == 200
        assert result["addresses"] == {}

    def test_identity_data_only(self):
        body = {
            "identity": {
                "seed": {"full_name": "John Doe", "email": "john@example.com"},
                "queries": [{"hits": [{"title": "test", "snippet": "123 Main St", "url": "https://a.com"}]}],
            }
        }

        extracted = [{"address_raw": "123 Main St, Toronto, ON", "source_url": "https://a.com", "snippet": ""}]

        with (
            patch.object(ag_main, "extract_addresses_from_queries_llm", return_value=extracted),
            patch.object(ag_main, "retry_with_backoff", return_value=(43.65, -79.38)),
            patch("address_geocoding_main.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            result, status = main_handler(_make_request(body))

        assert status == 200
        assert "123 Main St, Toronto, ON" in result["addresses"]

    def test_corporate_data_included(self):
        body = {
            "identity": {"queries": []},
            "corporate": {
                "debug": {
                    "full_hits_raw": [{"title": "corp hit", "snippet": "456 Elm St", "url": "https://b.com"}],
                    "last_hits_raw": [],
                }
            },
        }

        extracted = [{"address_raw": "456 Elm St, Ottawa, ON", "source_url": "https://b.com", "snippet": ""}]

        with (
            patch.object(ag_main, "extract_addresses_from_queries_llm", return_value=extracted),
            patch.object(ag_main, "retry_with_backoff", return_value=(45.42, -75.69)),
            patch("address_geocoding_main.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            result, status = main_handler(_make_request(body))

        assert status == 200
        assert "456 Elm St, Ottawa, ON" in result["addresses"]

    def test_no_addresses_found(self):
        body = {
            "identity": {
                "queries": [{"hits": [{"title": "test"}]}],
            }
        }

        with patch.object(ag_main, "extract_addresses_from_queries_llm", return_value=[]):
            result, status = main_handler(_make_request(body))

        assert status == 200
        assert result["addresses"] == {}

    def test_geocoding_failure_captured(self):
        body = {
            "identity": {
                "queries": [{"hits": [{"title": "test"}]}],
            }
        }

        extracted = [{"address_raw": "999 Unknown Rd", "source_url": "https://a.com", "snippet": ""}]

        with (
            patch.object(ag_main, "extract_addresses_from_queries_llm", return_value=extracted),
            patch.object(ag_main, "retry_with_backoff", side_effect=RuntimeError("geocoding failed")),
            patch("address_geocoding_main.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            result, status = main_handler(_make_request(body))

        assert status == 200
        addr_result = result["addresses"]["999 Unknown Rd"]
        assert addr_result["lat"] is None
        assert "geocoding failed" in addr_result["error"].lower()

    def test_geocoding_timeout_captured(self):
        """URLError during geocoding is captured per-address, not a function crash."""
        body = {
            "identity": {
                "queries": [{"hits": [{"title": "test"}]}],
            }
        }

        extracted = [{"address_raw": "123 Slow Rd, Toronto, ON", "source_url": "https://a.com", "snippet": ""}]
        from urllib.error import URLError

        with (
            patch.object(ag_main, "extract_addresses_from_queries_llm", return_value=extracted),
            patch.object(ag_main, "retry_with_backoff", side_effect=URLError("timed out")),
            patch("address_geocoding_main.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            result, status = main_handler(_make_request(body))

        assert status == 200
        addr_result = result["addresses"]["123 Slow Rd, Toronto, ON"]
        assert addr_result["lat"] is None
        assert addr_result["error"] is not None

    def test_multiple_addresses_rate_limited(self):
        """Sleep is called between geocoding requests for rate limiting."""
        body = {
            "identity": {
                "queries": [{"hits": [{"title": "test"}]}],
            }
        }

        extracted = [
            {"address_raw": "123 First St", "source_url": "https://a.com", "snippet": ""},
            {"address_raw": "456 Second Ave", "source_url": "https://b.com", "snippet": ""},
        ]

        with (
            patch.object(ag_main, "extract_addresses_from_queries_llm", return_value=extracted),
            patch.object(ag_main, "retry_with_backoff", return_value=(43.0, -79.0)),
            patch("address_geocoding_main.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            result, status = main_handler(_make_request(body))

        assert status == 200
        assert len(result["addresses"]) == 2
        # time.sleep should be called at least once for rate limiting between requests
        assert mock_time.sleep.call_count >= 1
