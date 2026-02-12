"""Tests for the phase1_identity Cloud Function (gcp/functions/phase1_identity/main.py).

Covers:
- Pure helpers: email_prefix, extract_domain, generate_name_variations, is_business_email
- transform_pse_query_to_natural_language (regex query transform)
- vertex_ai_search_precision/recall/linkedin (mock search client)
- extract_grounding_metadata (mock response parsing)
- vertex_ai_score (mock Gemini LLM call)
- hibp_breaches (mock HTTP requests)
- classify_contactability (deterministic matrix)
- main HTTP handler (validation, orchestration, rate limit → 429)
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

# Mock Vertex AI Search (discoveryengine)
_mock_discoveryengine = MagicMock()
sys.modules.setdefault("google.cloud", MagicMock())
sys.modules.setdefault("google.cloud.discoveryengine_v1", _mock_discoveryengine)

# ---------------------------------------------------------------------------
# Load phase1_identity/main.py
# ---------------------------------------------------------------------------
from conftest import load_function_module

p1_main = load_function_module("phase1_identity", "phase1_identity_main")

email_prefix = p1_main.email_prefix
extract_domain = p1_main.extract_domain
generate_name_variations = p1_main.generate_name_variations
is_business_email = p1_main.is_business_email
transform_pse_query_to_natural_language = p1_main.transform_pse_query_to_natural_language
vertex_ai_search_precision = p1_main.vertex_ai_search_precision
vertex_ai_search_recall = p1_main.vertex_ai_search_recall
vertex_ai_search_linkedin = p1_main.vertex_ai_search_linkedin
extract_grounding_metadata = p1_main.extract_grounding_metadata
vertex_ai_score = p1_main.vertex_ai_score
hibp_breaches = p1_main.hibp_breaches
classify_contactability = p1_main.classify_contactability
main_handler = p1_main.main
SearchHit = p1_main.SearchHit
PROVINCE_NAMES = p1_main.PROVINCE_NAMES


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


def _mock_scored_result():
    """Return a valid LLM identity scoring result."""
    return {
        "top_handles": [
            {"platform": "linkedin", "handle": "jdoe", "url": "https://linkedin.com/in/jdoe", "confidence": "high"},
        ],
        "identity_clues": [],
        "location": {"city": "Toronto", "confidence": "high"},
        "rationale": "Matched via LinkedIn profile.",
        "_grounding_metadata": {"grounding_sources": [], "search_queries": []},
    }


# ===========================================================================
# Pure helpers
# ===========================================================================
class TestEmailPrefix:
    def test_basic(self):
        assert email_prefix("john@example.com") == "john"

    def test_no_at_sign(self):
        assert email_prefix("noemail") == "noemail"

    def test_empty(self):
        assert email_prefix("") == ""


class TestExtractDomain:
    def test_basic_url(self):
        assert extract_domain("https://www.example.com/page") == "example.com"

    def test_no_www(self):
        assert extract_domain("https://example.com") == "example.com"

    def test_empty(self):
        assert extract_domain("") == ""

    def test_malformed(self):
        result = extract_domain("not-a-url")
        assert isinstance(result, str)


class TestGenerateNameVariations:
    def test_two_part_name(self):
        full, variation = generate_name_variations("John Smith")
        assert full == "John Smith"
        assert variation is None

    def test_three_part_name(self):
        full, variation = generate_name_variations("John Michael Smith")
        assert full == "John Michael Smith"
        assert variation == "Michael Smith"

    def test_four_part_name(self):
        full, variation = generate_name_variations("John Michael David Smith")
        assert variation == "David Smith"

    def test_single_name(self):
        full, variation = generate_name_variations("Madonna")
        assert full == "Madonna"
        assert variation is None

    def test_empty_name(self):
        full, variation = generate_name_variations("")
        assert full == ""
        assert variation is None


class TestIsBusinessEmail:
    def test_business_email(self):
        assert is_business_email("john@acmecorp.com") is True

    def test_personal_email(self):
        assert is_business_email("john@gmail.com") is False

    def test_empty(self):
        assert is_business_email("") is False


# ===========================================================================
# transform_pse_query_to_natural_language
# ===========================================================================
class TestTransformPseQueryToNaturalLanguage:
    def test_intitle_quoted(self):
        result = transform_pse_query_to_natural_language('intitle:"John Smith" Toronto')
        assert result == "John Smith Toronto"

    def test_intitle_unquoted(self):
        result = transform_pse_query_to_natural_language("intitle:John Toronto")
        assert result == "John Toronto"

    def test_intext_quoted(self):
        result = transform_pse_query_to_natural_language('intext:"John Smith"')
        assert result == "John Smith"

    def test_mixed_operators(self):
        result = transform_pse_query_to_natural_language('intitle:"John Smith" OR intitle:"Smith" Toronto')
        assert "John Smith" in result
        assert "Smith" in result
        assert "Toronto" in result
        assert "intitle:" not in result

    def test_standalone_quotes_removed(self):
        result = transform_pse_query_to_natural_language('"John Smith" Toronto')
        assert result == "John Smith Toronto"

    def test_no_operators_passthrough(self):
        result = transform_pse_query_to_natural_language("John Smith Toronto")
        assert result == "John Smith Toronto"

    def test_extra_whitespace_cleaned(self):
        result = transform_pse_query_to_natural_language('intitle:"A"   intitle:"B"')
        assert "  " not in result


# ===========================================================================
# extract_grounding_metadata
# ===========================================================================
class TestExtractGroundingMetadata:
    def test_empty_candidates(self):
        resp = MagicMock()
        resp.candidates = []
        result = extract_grounding_metadata(resp)
        assert result["grounding_sources"] == []

    def test_with_grounding_data(self):
        chunk = MagicMock()
        chunk.web.uri = "https://example.com"
        chunk.web.title = "Example"

        grounding = MagicMock()
        grounding.web_search_queries = ["query1"]
        grounding.grounding_chunks = [chunk]
        grounding.search_entry_point.rendered_content = "<html></html>"

        candidate = MagicMock()
        candidate.grounding_metadata = grounding

        resp = MagicMock()
        resp.candidates = [candidate]

        result = extract_grounding_metadata(resp)
        assert len(result["grounding_sources"]) == 1
        assert result["search_queries"] == ["query1"]

    def test_none_search_queries_handled(self):
        """grounding.web_search_queries can be None for some responses."""
        grounding = MagicMock()
        grounding.web_search_queries = None
        grounding.grounding_chunks = None

        candidate = MagicMock()
        candidate.grounding_metadata = grounding

        resp = MagicMock()
        resp.candidates = [candidate]

        result = extract_grounding_metadata(resp)
        assert result["search_queries"] == []
        assert result["grounding_sources"] == []

    def test_html_fallback_when_rendered_content_missing(self):
        """Falls back to entry_point.html when rendered_content is absent."""
        grounding = MagicMock()
        grounding.web_search_queries = []
        grounding.grounding_chunks = []

        entry_point = MagicMock(spec=[])  # no rendered_content attr
        entry_point.html = "<div>fallback</div>"
        grounding.search_entry_point = entry_point

        candidate = MagicMock()
        candidate.grounding_metadata = grounding

        resp = MagicMock()
        resp.candidates = [candidate]

        result = extract_grounding_metadata(resp)
        assert result["search_entry_point"] == "<div>fallback</div>"

    def test_exception_returns_empty_metadata(self):
        """An exception during metadata extraction returns the default empty dict."""
        resp = MagicMock()
        # Make candidates[0] raise when accessing grounding_metadata
        bad_candidate = MagicMock()
        type(bad_candidate).grounding_metadata = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        resp.candidates = [bad_candidate]

        result = extract_grounding_metadata(resp)
        assert result["grounding_sources"] == []
        assert result["search_queries"] == []
        assert result["search_entry_point"] == ""


# ===========================================================================
# vertex_ai_score
# ===========================================================================
class TestVertexAiScore:
    def test_gcp_project_not_set_returns_error(self):
        with patch.object(p1_main, "GCP_PROJECT", ""):
            result = vertex_ai_score({}, [])
        assert "error" in result

    def test_successful_scoring(self):
        scored = _mock_scored_result()
        mock_response = MagicMock()
        mock_response.text = json.dumps(scored)
        mock_response.candidates = [MagicMock()]  # Non-empty to pass empty-check

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
            patch.object(p1_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_score({"full_name": "John Doe"}, [])

        assert "top_handles" in result
        assert result["top_handles"][0]["platform"] == "linkedin"

    def test_empty_response_retries(self):
        mock_response = MagicMock()
        mock_response.text = ""

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
            patch.object(p1_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_score({"full_name": "John Doe"}, [])

        assert "error" in result

    def test_structurally_empty_json_retries(self):
        """Empty JSON {} with no handles/rationale/clues/location is retried."""
        mock_response = MagicMock()
        mock_response.text = "{}"

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
            patch.object(p1_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_score({"full_name": "John Doe"}, [])

        assert "error" in result

    def test_invalid_confidence_corrected(self):
        scored = _mock_scored_result()
        scored["top_handles"][0]["confidence"] = "extreme"
        mock_response = MagicMock()
        mock_response.text = json.dumps(scored)
        mock_response.candidates = [MagicMock()]  # Non-empty to pass empty-check

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
            patch.object(p1_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_score({"full_name": "John Doe"}, [])

        assert result["top_handles"][0]["confidence"] == "medium"

    def test_no_candidates_block_reason(self):
        """Empty candidates list with block_reason triggers EmptyLLMResponseError and retries."""
        mock_response = MagicMock()
        mock_response.candidates = []
        mock_response.prompt_feedback.block_reason = "SAFETY"
        # response is truthy but has no candidates

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
            patch.object(p1_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_score({"full_name": "John Doe"}, [])

        assert "error" in result

    def test_response_text_raises_value_error(self):
        """ValueError from response.text (safety filter) is treated as empty response."""
        mock_response = MagicMock()
        mock_response.candidates = [MagicMock()]  # Non-empty
        type(mock_response).text = property(lambda self: (_ for _ in ()).throw(ValueError("blocked by safety filters")))

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
            patch.object(p1_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_score({"full_name": "John Doe"}, [])

        assert "error" in result

    def test_markdown_code_block_stripped(self):
        """Markdown-wrapped JSON is properly unwrapped."""
        scored = _mock_scored_result()
        wrapped = "```json\n" + json.dumps(scored) + "\n```"
        mock_response = MagicMock()
        mock_response.text = wrapped
        mock_response.candidates = [MagicMock()]

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
            patch.object(p1_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_score({"full_name": "John Doe"}, [])

        assert "top_handles" in result
        assert result["top_handles"][0]["platform"] == "linkedin"

    def test_json_decode_error_retries_then_errors(self):
        """Malformed JSON triggers EmptyLLMResponseError and retries exhaust."""
        mock_response = MagicMock()
        mock_response.text = "this is not valid json at all"
        mock_response.candidates = [MagicMock()]

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with (
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
            patch.object(p1_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result = vertex_ai_score({"full_name": "John Doe"}, [])

        assert "error" in result

    def test_rate_limit_error_propagates(self):
        """resource_exhausted in outer exception handler raises RateLimitExhaustedError."""
        import pytest
        from retry_utils import RateLimitExhaustedError

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("429 resource_exhausted: too many requests")

        with (
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
            patch.object(p1_main, "genai") as mock_genai_mod,
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            with pytest.raises(RateLimitExhaustedError):
                vertex_ai_score({"full_name": "John Doe"}, [])


# ===========================================================================
# hibp_breaches
# ===========================================================================
class TestHibpBreaches:
    def test_no_api_key(self):
        with patch.object(p1_main, "HIBP_API_KEY", ""):
            result = hibp_breaches("john@example.com")
        assert result == []

    def test_empty_email(self):
        with patch.object(p1_main, "HIBP_API_KEY", "test-key"):
            result = hibp_breaches("")
        assert result == []

    def test_successful_lookup(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"Title": "Adobe", "Name": "Adobe", "BreachDate": "2013-10-04"},
            {"Title": "LinkedIn", "Name": "LinkedIn", "BreachDate": "2012-05-05"},
        ]

        with (
            patch.object(p1_main, "HIBP_API_KEY", "test-key"),
            patch("phase1_identity_main.requests.get", return_value=mock_response),
            patch("retry_utils.time.sleep"),
        ):
            result = hibp_breaches("john@example.com")

        assert len(result) == 2
        assert result[0]["name"] == "Adobe"
        assert result[1]["name"] == "LinkedIn"

    def test_404_no_breaches(self):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status = MagicMock()

        with (
            patch.object(p1_main, "HIBP_API_KEY", "test-key"),
            patch("phase1_identity_main.requests.get", return_value=mock_response),
            patch("retry_utils.time.sleep"),
        ):
            result = hibp_breaches("john@example.com")

        assert result == []

    def test_api_error_returns_empty(self):
        with (
            patch.object(p1_main, "HIBP_API_KEY", "test-key"),
            patch("phase1_identity_main.requests.get", side_effect=RuntimeError("connection error")),
            patch("retry_utils.time.sleep"),
        ):
            result = hibp_breaches("john@example.com")

        assert result == []


# ===========================================================================
# classify_contactability
# ===========================================================================
class TestClassifyContactability:
    """Tests for the deterministic contactability matrix."""

    def test_low_footprint_no_breaches(self):
        result = classify_contactability(
            [{"confidence": "high"}],  # 1 handle
            [],
        )
        assert result["contactability"] == "Low"
        assert result["footprint_bucket"] == "LOW"
        assert result["breach_bucket"] == "NO"

    def test_medium_footprint_few_breaches(self):
        handles = [{"confidence": "high"}, {"confidence": "medium"}, {"confidence": "low"}]
        breaches = [{"name": "A"}, {"name": "B"}]
        result = classify_contactability(handles, breaches)
        assert result["contactability"] == "Good"
        assert result["footprint_bucket"] == "MED"
        assert result["breach_bucket"] == "FEW"

    def test_high_footprint_many_breaches(self):
        handles = [{"confidence": "high"}] * 5
        breaches = [{"name": str(i)} for i in range(5)]
        result = classify_contactability(handles, breaches)
        assert result["contactability"] == "Extremely high"
        assert result["footprint_bucket"] == "HIGH"
        assert result["breach_bucket"] == "MANY"

    def test_no_handles_no_breaches(self):
        result = classify_contactability([], [])
        assert result["contactability"] == "Low"
        assert result["num_social"] == 0
        assert result["num_breaches"] == 0

    def test_low_confidence_handles_not_counted(self):
        handles = [{"confidence": "low"}, {"confidence": "low"}]
        result = classify_contactability(handles, [])
        assert result["num_social"] == 0
        assert result["footprint_bucket"] == "LOW"

    def test_none_inputs(self):
        result = classify_contactability(None, None)
        assert result["num_social"] == 0
        assert result["num_breaches"] == 0


# ===========================================================================
# main HTTP handler
# ===========================================================================
class TestMainHandler:
    """Tests for the HTTP entry point."""

    def test_missing_email(self):
        req = _make_request({"full_name": "John Doe"})
        result, status = main_handler(req)
        assert status == 400
        assert "email" in result["error"].lower()

    def test_missing_full_name(self):
        req = _make_request({"email": "john@example.com"})
        result, status = main_handler(req)
        assert status == 400
        assert "full_name" in result["error"].lower()

    def test_invalid_json(self):
        req = _make_request(bad_json=True)
        result, status = main_handler(req)
        assert status == 400

    def test_valid_request(self):
        """Full orchestration with all external calls mocked."""
        body = {
            "job_id": "job123",
            "email": "john@acmecorp.com",
            "full_name": "John Doe",
            "city": "Toronto",
            "province": "ON",
        }

        scored = _mock_scored_result()
        mock_response = MagicMock()
        mock_response.text = json.dumps(scored)
        mock_response.candidates = []

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        mock_search_results = [
            {"url": "https://linkedin.com/in/jdoe", "title": "John Doe", "snippet": "Toronto", "relevance_score": 0.0}
        ]

        hibp_data = [{"Title": "Adobe", "Name": "Adobe", "BreachDate": "2013-10-04"}]
        mock_hibp_resp = MagicMock()
        mock_hibp_resp.status_code = 200
        mock_hibp_resp.json.return_value = hibp_data

        with (
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
            patch.object(p1_main, "HIBP_API_KEY", "test-key"),
            patch.object(p1_main, "_get_search_client", return_value=MagicMock()),
            patch.object(p1_main, "vertex_ai_search_precision", return_value=mock_search_results),
            patch.object(p1_main, "vertex_ai_search_recall", return_value=[]),
            patch.object(p1_main, "vertex_ai_search_linkedin", return_value=[]),
            patch.object(p1_main, "genai") as mock_genai_mod,
            patch("phase1_identity_main.requests.get", return_value=mock_hibp_resp),
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result, status = main_handler(_make_request(body))

        assert status == 200
        assert "seed" in result
        assert "top_handles" in result
        assert "breaches" in result
        assert "contactability" in result

    def test_rate_limit_returns_429(self):
        """RateLimitExhaustedError in LLM scoring bubbles up as 429."""
        from retry_utils import RateLimitExhaustedError

        body = {
            "job_id": "job123",
            "email": "john@acmecorp.com",
            "full_name": "John Doe",
            "city": "Toronto",
            "province": "ON",
        }

        with (
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
            patch.object(p1_main, "HIBP_API_KEY", ""),
            patch.object(p1_main, "_get_search_client", return_value=MagicMock()),
            patch.object(p1_main, "vertex_ai_search_precision", return_value=[]),
            patch.object(p1_main, "vertex_ai_search_recall", return_value=[]),
            patch.object(p1_main, "vertex_ai_search_linkedin", return_value=[]),
            patch.object(p1_main, "vertex_ai_score", side_effect=RateLimitExhaustedError("rate limited")),
            patch("retry_utils.time.sleep"),
        ):
            result, status = main_handler(_make_request(body))

        assert status == 429
        assert "retryable" in result


# ===========================================================================
# Vertex AI Search (core search function)
# ===========================================================================
class TestVertexAiSearch:
    """Tests for the _vertex_ai_search wrapper and search functions."""

    def test_search_exception_returns_empty_list(self):
        """Exceptions in Vertex AI Search are caught and return empty list."""
        mock_client = MagicMock()
        mock_client.search.side_effect = RuntimeError("gRPC error")

        with (
            patch.object(p1_main, "_search_client", mock_client),
            patch.object(p1_main, "_get_search_client", return_value=mock_client),
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
        ):
            result = vertex_ai_search_precision("John Doe Toronto", 5)

        assert result == []

    def test_search_returns_parsed_results(self):
        """Successful search returns list of dicts with url, title, snippet."""
        # Build mock search response
        mock_doc1 = MagicMock()
        mock_doc1.document.derived_struct_data = {
            "link": "https://linkedin.com/in/jdoe",
            "title": "John Doe",
            "snippets": [{"snippet": "Software Engineer in Toronto"}],
        }
        mock_doc2 = MagicMock()
        mock_doc2.document.derived_struct_data = {
            "link": "https://twitter.com/jdoe",
            "title": "jdoe",
            "snippets": [],
        }

        mock_response = MagicMock()
        mock_response.results = [mock_doc1, mock_doc2]

        mock_client = MagicMock()
        mock_client.search.return_value = mock_response

        with (
            patch.object(p1_main, "_search_client", mock_client),
            patch.object(p1_main, "_get_search_client", return_value=mock_client),
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
        ):
            result = vertex_ai_search_precision("John Doe", 5)

        assert len(result) == 2
        assert result[0]["url"] == "https://linkedin.com/in/jdoe"
        assert result[0]["snippet"] == "Software Engineer in Toronto"
        assert result[1]["snippet"] == ""  # no snippets → empty string


# ===========================================================================
# Identity resolution orchestration (_run_identity_resolution via main)
# ===========================================================================
class TestRunIdentityResolution:
    """Tests for complex orchestration logic inside _run_identity_resolution."""

    def _base_body(self, **overrides):
        body = {
            "job_id": "job-orch",
            "email": "john@acmecorp.com",
            "full_name": "John Doe",
            "city": "Toronto",
            "province": "ON",
        }
        body.update(overrides)
        return body

    def _make_scored(self, city="Toronto", confidence="high", **overrides):
        scored = _mock_scored_result()
        scored["location"] = {"city": city, "confidence": confidence}
        scored.update(overrides)
        return scored

    def _run_main(self, body, scored, precision_results=None, linkedin_results=None):
        """Run main_handler with standard mocks, returning (result, status)."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(scored)
        mock_response.candidates = [MagicMock()]

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        mock_hibp_resp = MagicMock()
        mock_hibp_resp.status_code = 404
        mock_hibp_resp.raise_for_status = MagicMock()

        with (
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
            patch.object(p1_main, "HIBP_API_KEY", "test-key"),
            patch.object(p1_main, "_get_search_client", return_value=MagicMock()),
            patch.object(p1_main, "vertex_ai_search_precision", return_value=precision_results or []) as mock_precision,
            patch.object(p1_main, "vertex_ai_search_recall", return_value=[]),
            patch.object(p1_main, "vertex_ai_search_linkedin", return_value=linkedin_results or []) as mock_linkedin,
            patch.object(p1_main, "genai") as mock_genai_mod,
            patch("phase1_identity_main.requests.get", return_value=mock_hibp_resp),
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result, status = main_handler(_make_request(body))

        return result, status, mock_precision, mock_linkedin

    def test_location_rerun_triggered_when_city_differs(self):
        """When LLM resolves a different city with high confidence, a rerun precision search fires."""
        scored = self._make_scored(city="Vancouver", confidence="high")
        result, status, mock_precision, _ = self._run_main(self._base_body(), scored)

        assert status == 200
        # Precision should be called at least twice: initial + rerun
        assert mock_precision.call_count >= 2
        # Second call should include "Vancouver" in the query
        rerun_call_args = mock_precision.call_args_list[-1]
        assert "Vancouver" in rerun_call_args[0][0]
        # queries payload should have precision_rerun entry
        assert any(q["id"] == "precision_rerun" for q in result["queries"])

    def test_location_rerun_not_triggered_same_city(self):
        """When LLM resolves the same city, no rerun search happens."""
        scored = self._make_scored(city="Toronto", confidence="high")
        result, status, mock_precision, _ = self._run_main(self._base_body(), scored)

        assert status == 200
        # No precision_rerun entry in queries (rerun did not fire)
        assert not any(q["id"] == "precision_rerun" for q in result["queries"])
        # No call should contain "precision_rerun"-style city-differing query
        for call_args in mock_precision.call_args_list:
            # None of the calls should be the rerun (which would have a different city)
            assert "Vancouver" not in call_args[0][0]

    def test_city_linkedin_fallback(self):
        """When company LinkedIn search returns 0 results and city is provided,
        a city-based LinkedIn search fires as fallback."""
        body = self._base_body(company_name="Acme Corp")
        scored = self._make_scored()
        result, status, _, mock_linkedin = self._run_main(body, scored, linkedin_results=[])

        assert status == 200
        # LinkedIn called for company query (returns []), then city fallback
        assert mock_linkedin.call_count >= 2

    def test_identity_clues_normalized_from_combined_hits(self):
        """Identity clues get snippet/source_query_id enriched from combined_hits."""
        precision_results = [
            {
                "url": "https://news.com/article",
                "title": "John Doe in News",
                "snippet": "John Doe was featured...",
                "relevance_score": 0.0,
            }
        ]
        scored = self._make_scored()
        scored["identity_clues"] = [{"url": "https://news.com/article", "title": ""}]
        scored["top_handles"] = []  # no handles to filter out

        result, status, _, _ = self._run_main(self._base_body(), scored, precision_results=precision_results)

        assert status == 200
        clues = result.get("identity_clues", [])
        assert len(clues) >= 1
        # Clue should have snippet enriched from the precision hit
        matched = [c for c in clues if c.get("url") == "https://news.com/article"]
        assert len(matched) == 1
        assert matched[0]["snippet"] == "John Doe was featured..."

    def test_parallel_search_timeout_graceful(self):
        """A timeout in one search future doesn't crash the whole function."""
        scored = self._make_scored()
        mock_response = MagicMock()
        mock_response.text = json.dumps(scored)
        mock_response.candidates = [MagicMock()]

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        mock_hibp_resp = MagicMock()
        mock_hibp_resp.status_code = 404
        mock_hibp_resp.raise_for_status = MagicMock()

        call_count = {"precision": 0}

        def precision_side_effect(query, num=5):
            call_count["precision"] += 1
            if call_count["precision"] == 1:
                raise TimeoutError("search timed out")
            return []

        with (
            patch.object(p1_main, "GCP_PROJECT", "test-project"),
            patch.object(p1_main, "HIBP_API_KEY", "test-key"),
            patch.object(p1_main, "_get_search_client", return_value=MagicMock()),
            patch.object(p1_main, "vertex_ai_search_precision", side_effect=precision_side_effect),
            patch.object(p1_main, "vertex_ai_search_recall", return_value=[]),
            patch.object(p1_main, "vertex_ai_search_linkedin", return_value=[]),
            patch.object(p1_main, "genai") as mock_genai_mod,
            patch("phase1_identity_main.requests.get", return_value=mock_hibp_resp),
            patch("retry_utils.time.sleep"),
        ):
            mock_genai_mod.Client.return_value = mock_client
            result, status = main_handler(_make_request(self._base_body()))

        # Should still return 200 with partial results
        assert status == 200
        assert "seed" in result
