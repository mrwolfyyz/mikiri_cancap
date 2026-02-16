"""Tests for the address_verification Cloud Function (gcp/functions/address_verification/main.py).

Covers:
- geocode_address (Nominatim geocoding with rate limiting)
- generate_street_view_url (pure URL builder)
- extract_grounding_metadata (mock response parsing)
- map_grounding_to_queries_payload (pure transform)
- _parse_and_validate_analysis (JSON parsing, defaults, enum validation)
- vertex_ai_analyze_address_grounded (mock Gemini call with retry)
- main HTTP handler (CORS, validation, parallel analysis + geocoding, error paths)
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
# Load address_verification/main.py
# ---------------------------------------------------------------------------
from conftest import load_function_module

av_main = load_function_module("address_verification", "address_verification_main")

geocode_address = av_main.geocode_address
generate_street_view_url = av_main.generate_street_view_url
extract_grounding_metadata = av_main.extract_grounding_metadata
map_grounding_to_queries_payload = av_main.map_grounding_to_queries_payload
_parse_and_validate_analysis = av_main._parse_and_validate_analysis
vertex_ai_analyze_address_grounded = av_main.vertex_ai_analyze_address_grounded
main_handler = av_main.main


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


def _mock_llm_analysis():
    """Return a valid analysis JSON dict."""
    return {
        "business_at_address": True,
        "is_virtual_workspace": False,
        "is_shipping_location": False,
        "is_residential": False,
        "is_suspicious": False,
        "fraud_risk_level": "low",
        "fraud_indicators": [],
        "confidence": "high",
        "reasoning": "Business confirmed at address.",
        "key_findings": ["Business exists"],
    }


# ===========================================================================
# generate_street_view_url
# ===========================================================================
class TestGenerateStreetViewUrl:
    """Tests for the Street View URL builder (pure function)."""

    def test_with_coordinates(self):
        url = generate_street_view_url("123 Main St", lat=43.65, lon=-79.38)
        assert "map_action=pano" in url
        assert "43.65" in url
        assert "-79.38" in url

    def test_without_coordinates(self):
        url = generate_street_view_url("123 Main St, Toronto, ON")
        assert "maps/search/" in url
        assert "123" in url

    def test_lat_none_lon_none(self):
        url = generate_street_view_url("456 Elm St")
        assert "maps/search/" in url

    def test_lat_zero_lon_zero(self):
        """Zero is valid, not None."""
        url = generate_street_view_url("somewhere", lat=0.0, lon=0.0)
        assert "map_action=pano" in url


# ===========================================================================
# extract_grounding_metadata
# ===========================================================================
class TestExtractGroundingMetadata:
    """Tests for grounding metadata extraction from mock response objects."""

    def test_empty_response(self):
        resp = MagicMock()
        resp.candidates = []
        result = extract_grounding_metadata(resp)
        assert result["grounding_sources"] == []
        assert result["search_queries"] == []

    def test_no_candidates_attribute(self):
        resp = MagicMock(spec=[])  # no attributes
        result = extract_grounding_metadata(resp)
        assert result["grounding_sources"] == []

    def test_with_grounding_data(self):
        chunk = MagicMock()
        chunk.web.uri = "https://example.com"
        chunk.web.title = "Example"

        grounding = MagicMock()
        grounding.web_search_queries = ["test query"]
        grounding.grounding_chunks = [chunk]
        grounding.search_entry_point.rendered_content = "<html>entry</html>"

        candidate = MagicMock()
        candidate.grounding_metadata = grounding

        resp = MagicMock()
        resp.candidates = [candidate]

        result = extract_grounding_metadata(resp)
        assert len(result["grounding_sources"]) == 1
        assert result["grounding_sources"][0]["url"] == "https://example.com"
        assert result["search_queries"] == ["test query"]
        assert "entry" in result["search_entry_point"]

    def test_exception_in_metadata_returns_empty(self):
        resp = MagicMock()
        resp.candidates = MagicMock(side_effect=RuntimeError("broken"))
        result = extract_grounding_metadata(resp)
        assert result["grounding_sources"] == []


# ===========================================================================
# map_grounding_to_queries_payload
# ===========================================================================
class TestMapGroundingToQueriesPayload:
    """Tests for the grounding metadata to queries_payload transform."""

    def test_empty_metadata(self):
        assert map_grounding_to_queries_payload({}) == []
        assert map_grounding_to_queries_payload(None) == []

    def test_no_sources(self):
        result = map_grounding_to_queries_payload({"grounding_sources": [], "search_queries": ["q1"]})
        assert result == []

    def test_with_sources(self):
        metadata = {
            "grounding_sources": [{"url": "https://example.com", "title": "Ex", "snippet": ""}],
            "search_queries": ["query1", "query2"],
        }
        result = map_grounding_to_queries_payload(metadata)
        assert len(result) == 1
        assert result[0]["id"] == "gemini_grounded_search"
        assert result[0]["type"] == "grounded"
        assert "query1, query2" in result[0]["query"]
        assert result[0]["hits"] == metadata["grounding_sources"]

    def test_no_search_queries_fallback(self):
        metadata = {
            "grounding_sources": [{"url": "https://example.com"}],
            "search_queries": [],
        }
        result = map_grounding_to_queries_payload(metadata)
        assert result[0]["query"] == "Gemini-determined queries"


# ===========================================================================
# _parse_and_validate_analysis
# ===========================================================================
class TestParseAndValidateAnalysis:
    """Tests for JSON parsing, markdown stripping, default filling, enum validation."""

    def test_valid_json(self):
        content = json.dumps(_mock_llm_analysis())
        resp = MagicMock()
        resp.candidates = []
        result = _parse_and_validate_analysis(content, resp)
        assert result["business_at_address"] is True
        assert result["fraud_risk_level"] == "low"

    def test_markdown_wrapped_json(self):
        inner = json.dumps(_mock_llm_analysis())
        content = f"```json\n{inner}\n```"
        resp = MagicMock()
        resp.candidates = []
        result = _parse_and_validate_analysis(content, resp)
        assert result["business_at_address"] is True

    def test_empty_content_raises(self):
        resp = MagicMock()
        resp.candidates = []
        try:
            _parse_and_validate_analysis("", resp)
            assert False, "Should have raised EmptyLLMResponseError"
        except Exception as e:
            assert "Empty" in str(e) or "empty" in str(e)

    def test_invalid_json_raises(self):
        resp = MagicMock()
        try:
            _parse_and_validate_analysis("not json at all", resp)
            assert False, "Should have raised"
        except Exception:
            pass

    def test_missing_fields_get_defaults(self):
        content = json.dumps({"reasoning": "test"})
        resp = MagicMock()
        resp.candidates = []
        result = _parse_and_validate_analysis(content, resp)
        assert result["business_at_address"] is False  # default
        assert result["is_suspicious"] is True  # default
        assert result["fraud_risk_level"] == "medium"  # default
        assert result["fraud_indicators"] == []
        assert result["key_findings"] == []

    def test_invalid_enum_corrected(self):
        data = _mock_llm_analysis()
        data["fraud_risk_level"] = "extreme"
        data["confidence"] = "very_high"
        content = json.dumps(data)
        resp = MagicMock()
        resp.candidates = []
        result = _parse_and_validate_analysis(content, resp)
        assert result["fraud_risk_level"] == "medium"
        assert result["confidence"] == "medium"

    def test_non_list_arrays_corrected(self):
        data = _mock_llm_analysis()
        data["fraud_indicators"] = "not a list"
        data["key_findings"] = 42
        content = json.dumps(data)
        resp = MagicMock()
        resp.candidates = []
        result = _parse_and_validate_analysis(content, resp)
        assert result["fraud_indicators"] == []
        assert result["key_findings"] == []

    def test_grounding_metadata_attached(self):
        content = json.dumps(_mock_llm_analysis())
        chunk = MagicMock()
        chunk.web.uri = "https://src.com"
        chunk.web.title = "Source"
        grounding = MagicMock()
        grounding.web_search_queries = ["q"]
        grounding.grounding_chunks = [chunk]
        grounding.search_entry_point.rendered_content = ""
        candidate = MagicMock()
        candidate.grounding_metadata = grounding
        resp = MagicMock()
        resp.candidates = [candidate]
        result = _parse_and_validate_analysis(content, resp)
        assert "_grounding_metadata" in result
        assert len(result["_grounding_metadata"]["grounding_sources"]) == 1


# ===========================================================================
# vertex_ai_analyze_address_grounded
# ===========================================================================
class TestVertexAiAnalyzeAddressGrounded:
    """Tests for the grounded LLM analysis call."""

    def test_gcp_project_not_set_raises(self):
        with patch.object(av_main, "GCP_PROJECT", ""):
            try:
                vertex_ai_analyze_address_grounded("123 Main St", "Acme Corp")
                assert False, "Should have raised ValueError"
            except ValueError as e:
                assert "GCP_PROJECT" in str(e)

    def test_successful_analysis(self):
        mock_response = MagicMock()
        mock_response.text = json.dumps(_mock_llm_analysis())
        mock_response.candidates = []

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(av_main, "GCP_PROJECT", "test-project"),
            patch.object(av_main, "_get_genai_client", return_value=mock_client),
            patch("retry_utils.time.sleep"),
        ):
            result = vertex_ai_analyze_address_grounded("123 Main St", "Acme Corp")

        assert result["business_at_address"] is True
        assert "_grounding_metadata" in result

    def test_empty_response_retries_then_raises(self):
        mock_response = MagicMock()
        mock_response.text = ""

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(av_main, "GCP_PROJECT", "test-project"),
            patch.object(av_main, "_get_genai_client", return_value=mock_client),
            patch("retry_utils.time.sleep"),
        ):
            try:
                vertex_ai_analyze_address_grounded("123 Main St", "Acme Corp")
                assert False, "Should have raised after retries"
            except Exception:
                pass


# ===========================================================================
# geocode_address
# ===========================================================================
class TestGeocodeAddress:
    """Tests for Nominatim geocoding."""

    def test_successful_geocode(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([{"lat": "43.65", "lon": "-79.38"}]).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with (
            patch.object(av_main, "_last_nominatim_call", 0.0),
            patch("address_verification_main.urlopen", return_value=mock_resp),
            patch("address_verification_main.time") as mock_time,
        ):
            mock_time.time.return_value = 100.0
            mock_time.sleep = MagicMock()
            lat, lon = geocode_address("123 Main St, Toronto")

        assert lat == 43.65
        assert lon == -79.38

    def test_no_results(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([]).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with (
            patch.object(av_main, "_last_nominatim_call", 0.0),
            patch("address_verification_main.urlopen", return_value=mock_resp),
            patch("address_verification_main.time") as mock_time,
        ):
            mock_time.time.return_value = 100.0
            mock_time.sleep = MagicMock()
            lat, lon = geocode_address("nonexistent address")

        assert lat is None
        assert lon is None

    def test_network_error_returns_none(self):
        """URLError on all attempts returns (None, None) after retries."""
        from urllib.error import URLError

        with (
            patch.object(av_main, "_last_nominatim_call", 0.0),
            patch("address_verification_main.urlopen", side_effect=URLError("timeout")),
            patch("address_verification_main.time") as mock_time,
            patch("retry_utils.time.sleep"),
        ):
            mock_time.time.return_value = 100.0
            mock_time.sleep = MagicMock()
            lat, lon = geocode_address("123 Main St")

        assert lat is None
        assert lon is None

    def test_http_error_returns_none(self):
        """HTTPError (e.g. 500) is caught and returns (None, None) after retries."""
        from urllib.error import HTTPError

        with (
            patch.object(av_main, "_last_nominatim_call", 0.0),
            patch(
                "address_verification_main.urlopen", side_effect=HTTPError("http://...", 500, "Server Error", {}, None)
            ),
            patch("address_verification_main.time") as mock_time,
            patch("retry_utils.time.sleep"),
        ):
            mock_time.time.return_value = 100.0
            mock_time.sleep = MagicMock()
            lat, lon = geocode_address("123 Main St")

        assert lat is None
        assert lon is None

    def test_transient_error_retried_then_succeeds(self):
        """URLError on first call is retried, second call succeeds."""
        from urllib.error import URLError

        mock_resp_ok = MagicMock()
        mock_resp_ok.read.return_value = json.dumps([{"lat": "43.65", "lon": "-79.38"}]).encode()
        mock_resp_ok.__enter__ = MagicMock(return_value=mock_resp_ok)
        mock_resp_ok.__exit__ = MagicMock(return_value=False)

        call_count = [0]

        def urlopen_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise URLError("Connection refused")
            return mock_resp_ok

        with (
            patch.object(av_main, "_last_nominatim_call", 0.0),
            patch("address_verification_main.urlopen", side_effect=urlopen_side_effect),
            patch("address_verification_main.time") as mock_time,
            patch("retry_utils.time.sleep"),
        ):
            mock_time.time.return_value = 100.0
            mock_time.sleep = MagicMock()
            lat, lon = geocode_address("123 Main St, Toronto")

        assert lat == 43.65
        assert lon == -79.38
        assert call_count[0] == 2

    def test_json_decode_error_returns_none(self):
        """Invalid JSON response from Nominatim returns (None, None)."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with (
            patch.object(av_main, "_last_nominatim_call", 0.0),
            patch("address_verification_main.urlopen", return_value=mock_resp),
            patch("address_verification_main.time") as mock_time,
        ):
            mock_time.time.return_value = 100.0
            mock_time.sleep = MagicMock()
            lat, lon = geocode_address("123 Main St")

        assert lat is None
        assert lon is None

    def test_key_error_missing_lat_returns_none(self):
        """Response missing 'lat' key returns (None, None)."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([{"other": "field"}]).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with (
            patch.object(av_main, "_last_nominatim_call", 0.0),
            patch("address_verification_main.urlopen", return_value=mock_resp),
            patch("address_verification_main.time") as mock_time,
        ):
            mock_time.time.return_value = 100.0
            mock_time.sleep = MagicMock()
            lat, lon = geocode_address("123 Main St")

        assert lat is None
        assert lon is None


# ===========================================================================
# extract_grounding_metadata - additional tests
# ===========================================================================
class TestExtractGroundingMetadataFallback:
    """Additional edge case tests for grounding metadata extraction."""

    def test_html_fallback_when_no_rendered_content(self):
        """Falls back to entry_point.html when rendered_content is absent."""
        grounding = MagicMock()
        grounding.web_search_queries = []
        grounding.grounding_chunks = []

        entry_point = MagicMock(spec=[])  # no rendered_content attr
        entry_point.html = "<div>test html</div>"
        grounding.search_entry_point = entry_point

        candidate = MagicMock()
        candidate.grounding_metadata = grounding

        resp = MagicMock()
        resp.candidates = [candidate]

        result = extract_grounding_metadata(resp)
        assert result["search_entry_point"] == "<div>test html</div>"


# ===========================================================================
# main HTTP handler
# ===========================================================================
class TestMainHandler:
    """Tests for the HTTP entry point."""

    def test_cors_options(self):
        req = _make_request(method="OPTIONS")
        body, status, headers = main_handler(req)
        assert status == 204
        assert "Access-Control-Allow-Origin" in headers

    def test_valid_request_separate_fields(self):
        body = {
            "street_address": "123 Main St",
            "suite_unit": "Suite 100",
            "city": "Toronto",
            "province": "ON",
            "postal_code": "M5H 2N2",
            "business_name": "Acme Corp",
        }
        mock_analysis = _mock_llm_analysis()
        mock_analysis["_grounding_metadata"] = {"grounding_sources": [], "search_queries": []}

        with (
            _app.test_request_context(),
            patch.object(av_main, "vertex_ai_analyze_address_grounded", return_value=mock_analysis),
            patch.object(av_main, "geocode_address", return_value=(43.65, -79.38)),
        ):
            resp, status, headers = main_handler(_make_request(body))

        assert status == 200

    def test_valid_request_combined_address(self):
        body = {
            "address": "123 Main St, Toronto, ON M5H 2N2",
            "business_name": "Acme Corp",
        }
        mock_analysis = _mock_llm_analysis()
        mock_analysis["_grounding_metadata"] = {"grounding_sources": [], "search_queries": []}

        with (
            _app.test_request_context(),
            patch.object(av_main, "vertex_ai_analyze_address_grounded", return_value=mock_analysis),
            patch.object(av_main, "geocode_address", return_value=(43.65, -79.38)),
        ):
            resp, status, headers = main_handler(_make_request(body))

        assert status == 200

    def test_missing_address(self):
        body = {"business_name": "Acme Corp"}
        with _app.test_request_context():
            resp, status, headers = main_handler(_make_request(body))
        assert status == 400

    def test_missing_business_name(self):
        body = {"address": "123 Main St, Toronto, ON"}
        with _app.test_request_context():
            resp, status, headers = main_handler(_make_request(body))
        assert status == 400

    def test_invalid_json(self):
        with _app.test_request_context():
            resp, status, headers = main_handler(_make_request(bad_json=True))
        # get_json raises -> req_data defaults to {}
        # Both address and business_name are empty -> 400
        assert status == 400

    def test_analysis_error_returns_500(self):
        body = {
            "address": "123 Main St, Toronto, ON",
            "business_name": "Acme Corp",
        }
        with (
            _app.test_request_context(),
            patch.object(av_main, "vertex_ai_analyze_address_grounded", side_effect=RuntimeError("LLM down")),
            patch.object(av_main, "geocode_address", return_value=(None, None)),
        ):
            resp, status, headers = main_handler(_make_request(body))

        assert status == 500

    def test_geocoding_fails_analysis_succeeds(self):
        """When geocoding returns (None, None) but LLM analysis succeeds, return 200."""
        body = {
            "address": "123 Main St, Toronto, ON",
            "business_name": "Acme Corp",
        }
        mock_analysis = _mock_llm_analysis()
        mock_analysis["_grounding_metadata"] = {"grounding_sources": [], "search_queries": []}

        with (
            _app.test_request_context(),
            patch.object(av_main, "vertex_ai_analyze_address_grounded", return_value=mock_analysis),
            patch.object(av_main, "geocode_address", return_value=(None, None)),
        ):
            resp, status, headers = main_handler(_make_request(body))

        assert status == 200
        data = json.loads(resp.get_data(as_text=True))
        assert data["geocoding"]["lat"] is None
        assert data["geocoding"]["lon"] is None
        assert data["analysis"]["business_at_address"] is True

    def test_analysis_error_with_geocoding_success(self):
        """When LLM analysis raises, 500 is returned even if geocoding succeeds."""
        body = {
            "address": "123 Main St, Toronto, ON",
            "business_name": "Acme Corp",
        }
        with (
            _app.test_request_context(),
            patch.object(av_main, "vertex_ai_analyze_address_grounded", side_effect=RuntimeError("LLM failed")),
            patch.object(av_main, "geocode_address", return_value=(43.65, -79.38)),
        ):
            resp, status, headers = main_handler(_make_request(body))

        assert status == 500
        data = json.loads(resp.get_data(as_text=True))
        assert "LLM failed" in data["error"]
