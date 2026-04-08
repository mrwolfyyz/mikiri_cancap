"""Tests for the query_constructor Cloud Function (gcp/functions/query_constructor/main.py).

The module has module-level Vertex AI initialization. We mock vertexai and
GenerativeModel in sys.modules before loading, then patch _MODEL per test.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from retry_utils import EmptyLLMResponseError

# ---------------------------------------------------------------------------
# Mock heavy dependencies BEFORE loading the module
# ---------------------------------------------------------------------------
_mock_ff = MagicMock()
_mock_ff.http = lambda f: f
sys.modules.setdefault("functions_framework", _mock_ff)

_mock_vertexai = MagicMock()
_mock_gen_models = MagicMock()
sys.modules.setdefault("vertexai", _mock_vertexai)
sys.modules.setdefault("vertexai.generative_models", _mock_gen_models)

# Provide GenerativeModel and GenerationConfig as MagicMock classes
_mock_gen_models.GenerativeModel = MagicMock()
_mock_gen_models.GenerationConfig = MagicMock()

# ---------------------------------------------------------------------------
# Load query_constructor/main.py via conftest helper.
# Set GCP_PROJECT so the module-level init path is exercised.
# ---------------------------------------------------------------------------
from llm_input_validators import (
    MAX_FULL_NAME_LEN,
    is_allowed_llm_input_char,
    normalize_and_validate_allowlist_text,
    normalize_province_for_query,
)

from conftest import load_function_module

_orig_gcp_project = os.environ.get("GCP_PROJECT")
os.environ["GCP_PROJECT"] = "test-project"

qc_main = load_function_module("query_constructor", "query_constructor_main")

# Restore env var
if _orig_gcp_project is None:
    os.environ.pop("GCP_PROJECT", None)
else:
    os.environ["GCP_PROJECT"] = _orig_gcp_project

generate_precision_query = qc_main.generate_precision_query
PROVINCE_NAMES = qc_main.PROVINCE_NAMES
main_handler = qc_main.main


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


def _mock_llm_response(data):
    """Return a mock Vertex AI response whose .text is JSON."""
    resp = MagicMock()
    resp.text = json.dumps(data)
    return resp


# ===========================================================================
# PROVINCE_NAMES
# ===========================================================================
class TestProvinceNames:
    """Tests for the province code -> full name mapping."""

    def test_all_13_entries(self):
        assert len(PROVINCE_NAMES) == 13

    def test_key_mappings(self):
        assert PROVINCE_NAMES["ON"] == "Ontario"
        assert PROVINCE_NAMES["BC"] == "British Columbia"
        assert PROVINCE_NAMES["QC"] == "Quebec"
        assert PROVINCE_NAMES["AB"] == "Alberta"
        assert PROVINCE_NAMES["NL"] == "Newfoundland and Labrador"

    def test_territories_present(self):
        assert "NT" in PROVINCE_NAMES
        assert "YT" in PROVINCE_NAMES
        assert "NU" in PROVINCE_NAMES


# ===========================================================================
# generate_precision_query
# ===========================================================================
class TestGeneratePrecisionQuery:
    """Tests for the core LLM-powered query generation."""

    def test_successful_generation(self):
        llm_data = {
            "original_name": "Timothy Lewis",
            "generated_names": ["Tim Lewis"],
            "vertex_query": '"Timothy Lewis" OR "Tim Lewis" "Cobourg, Ontario"',
        }
        mock_model = MagicMock()
        mock_model.generate_content.return_value = _mock_llm_response(llm_data)

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            result = generate_precision_query("Timothy Lewis", "Cobourg", "ON")

        assert result["original_name"] == "Timothy Lewis"
        assert result["generated_names"] == ["Tim Lewis"]
        assert "Timothy Lewis" in result["vertex_query"]

    def test_province_code_converted_to_full_name(self):
        """Province code (e.g. 'ON') should be expanded in the prompt."""
        llm_data = {
            "original_name": "John Doe",
            "generated_names": [],
            "vertex_query": '"John Doe" "Toronto, Ontario"',
        }
        mock_model = MagicMock()
        mock_model.generate_content.return_value = _mock_llm_response(llm_data)

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            _result = generate_precision_query("John Doe", "Toronto", "ON")

        # The actual model call should have received "Ontario" not "ON"
        call_args = mock_model.generate_content.call_args
        prompt_text = call_args[0][0]
        assert "Ontario" in prompt_text

    def test_empty_province(self):
        llm_data = {
            "original_name": "Jane Smith",
            "generated_names": [],
            "vertex_query": '"Jane Smith" "Vancouver"',
        }
        mock_model = MagicMock()
        mock_model.generate_content.return_value = _mock_llm_response(llm_data)

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            result = generate_precision_query("Jane Smith", "Vancouver", "")

        assert result["original_name"] == "Jane Smith"

    def test_missing_original_name_defaults(self):
        """If LLM omits original_name, it defaults to the input full_name."""
        llm_data = {
            "generated_names": ["Tim"],
            "vertex_query": '"Timothy Lewis" OR "Tim" "Cobourg"',
        }
        mock_model = MagicMock()
        mock_model.generate_content.return_value = _mock_llm_response(llm_data)

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            result = generate_precision_query("Timothy Lewis", "Cobourg")

        assert result["original_name"] == "Timothy Lewis"

    def test_missing_generated_names_defaults_to_empty_list(self):
        llm_data = {
            "original_name": "John Doe",
            "vertex_query": '"John Doe" "Toronto"',
        }
        mock_model = MagicMock()
        mock_model.generate_content.return_value = _mock_llm_response(llm_data)

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            result = generate_precision_query("John Doe", "Toronto")

        assert result["generated_names"] == []

    def test_missing_vertex_query_fallback(self):
        """If LLM omits vertex_query, a basic fallback is constructed."""
        llm_data = {
            "original_name": "John Doe",
            "generated_names": [],
        }
        mock_model = MagicMock()
        mock_model.generate_content.return_value = _mock_llm_response(llm_data)

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            result = generate_precision_query("John Doe", "Toronto", "ON")

        assert '"John Doe"' in result["vertex_query"]
        assert '"Toronto, Ontario"' in result["vertex_query"]

    def test_non_list_generated_names_coerced(self):
        llm_data = {
            "original_name": "John Doe",
            "generated_names": "not a list",
            "vertex_query": '"John Doe" "Toronto"',
        }
        mock_model = MagicMock()
        mock_model.generate_content.return_value = _mock_llm_response(llm_data)

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            result = generate_precision_query("John Doe", "Toronto")

        assert result["generated_names"] == []

    def test_gcp_project_not_set_raises(self):
        with patch.object(qc_main, "GCP_PROJECT", ""), patch.object(qc_main, "_MODEL", None):
            try:
                generate_precision_query("John Doe", "Toronto")
                assert False, "Should have raised RuntimeError"
            except RuntimeError as e:
                assert "GCP_PROJECT" in str(e)

    def test_markdown_wrapped_json_stripped(self):
        """JSON wrapped in markdown code blocks is handled."""
        inner_data = {
            "original_name": "John Doe",
            "generated_names": [],
            "vertex_query": '"John Doe" "Toronto"',
        }
        mock_response = MagicMock()
        mock_response.text = f"```json\n{json.dumps(inner_data)}\n```"

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            result = generate_precision_query("John Doe", "Toronto")

        assert result["original_name"] == "John Doe"

    def test_empty_response_raises_empty_llm_error(self):
        """Empty LLM response triggers EmptyLLMResponseError (retried)."""
        mock_response = MagicMock()
        mock_response.text = ""

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            with pytest.raises(EmptyLLMResponseError):
                generate_precision_query("John Doe", "Toronto")

    def test_none_response_object_raises_empty_llm(self):
        mock_model = MagicMock()
        mock_model.generate_content.return_value = None

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            with pytest.raises(EmptyLLMResponseError):
                generate_precision_query("John Doe", "Toronto")

    def test_response_missing_text_attribute_raises(self):
        mock_model = MagicMock()
        mock_model.generate_content.return_value = object()

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            with pytest.raises(EmptyLLMResponseError):
                generate_precision_query("John Doe", "Toronto")

    def test_markdown_stripped_to_empty_raises(self):
        mock_response = MagicMock()
        mock_response.text = "```json\n```"

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            with pytest.raises(EmptyLLMResponseError):
                generate_precision_query("John Doe", "Toronto")

    def test_json_decode_extra_data_raises_empty_llm(self):
        """Non-'expecting value' JSON errors still map to EmptyLLMResponseError (retry path)."""
        mock_response = MagicMock()
        mock_response.text = '{"a":1}x'

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            with pytest.raises(EmptyLLMResponseError):
                generate_precision_query("John Doe", "Toronto")


# ===========================================================================
# main HTTP handler
# ===========================================================================
class TestMainHandler:
    """Tests for the HTTP entry point."""

    def test_valid_request(self):
        llm_data = {
            "original_name": "John Doe",
            "generated_names": ["Johnny Doe"],
            "vertex_query": '"John Doe" OR "Johnny Doe" "Toronto, Ontario"',
        }
        mock_model = MagicMock()
        mock_model.generate_content.return_value = _mock_llm_response(llm_data)

        req = _make_request({"full_name": "John Doe", "city": "Toronto", "province": "ON"})

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            result, status = main_handler(req)

        assert status == 200
        assert result["original_name"] == "John Doe"

    def test_missing_full_name(self):
        req = _make_request({"city": "Toronto"})
        result, status = main_handler(req)
        assert status == 400
        assert "required" in result["error"].lower()

    def test_missing_city(self):
        req = _make_request({"full_name": "John Doe"})
        result, status = main_handler(req)
        assert status == 400
        assert "required" in result["error"].lower()

    def test_invalid_json(self):
        req = _make_request(bad_json=True)
        result, status = main_handler(req)
        assert status == 400

    def test_llm_error_returns_500(self):
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = RuntimeError("Vertex AI down")

        req = _make_request({"full_name": "John Doe", "city": "Toronto", "province": "ON"})

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            result, status = main_handler(req)

        assert status == 500

    def test_whitespace_stripped_from_inputs(self):
        llm_data = {
            "original_name": "John Doe",
            "generated_names": [],
            "vertex_query": '"John Doe" "Toronto"',
        }
        mock_model = MagicMock()
        mock_model.generate_content.return_value = _mock_llm_response(llm_data)

        req = _make_request({"full_name": "  John Doe  ", "city": "  Toronto  ", "province": "  ON  "})

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            result, status = main_handler(req)

        assert status == 200

    def test_prompt_injection_characters_in_full_name_rejected(self):
        req = _make_request({"full_name": 'John"; ignore previous', "city": "Toronto"})
        result, status = main_handler(req)
        assert status == 400
        assert "letters" in result["error"].lower() or "punctuation" in result["error"].lower()

    def test_invalid_province_returns_400(self):
        req = _make_request({"full_name": "John Doe", "city": "Toronto", "province": "Bad@Province"})
        result, status = main_handler(req)
        assert status == 400
        assert "province" in result["error"].lower() or "invalid" in result["error"].lower()

    def test_unicode_name_and_city_accepted(self):
        llm_data = {
            "original_name": "François Tremblay",
            "generated_names": [],
            "vertex_query": '"François Tremblay" "Montréal, Quebec"',
        }
        mock_model = MagicMock()
        mock_model.generate_content.return_value = _mock_llm_response(llm_data)

        req = _make_request({"full_name": "François Tremblay", "city": "Montréal", "province": "QC"})

        with (
            patch.object(qc_main, "_MODEL", mock_model),
            patch.object(qc_main, "GCP_PROJECT", "test-project"),
            patch("retry_utils.time.sleep"),
        ):
            result, status = main_handler(req)

        assert status == 200
        assert result["original_name"] == "François Tremblay"


# ===========================================================================
# Normalization helpers (coverage for allow-list and province parsing)
# ===========================================================================
class TestNormalizationHelpers:
    def test_allowlist_empty_returns_none(self):
        assert normalize_and_validate_allowlist_text("", MAX_FULL_NAME_LEN) is None

    def test_allowlist_whitespace_only_returns_none(self):
        assert normalize_and_validate_allowlist_text("   \n\t  ", MAX_FULL_NAME_LEN) is None

    def test_allowlist_too_long_returns_none(self):
        long_name = "A" * (MAX_FULL_NAME_LEN + 1)
        assert normalize_and_validate_allowlist_text(long_name, MAX_FULL_NAME_LEN) is None

    def test_allowlist_invalid_char_returns_none(self):
        assert normalize_and_validate_allowlist_text("John@Doe", MAX_FULL_NAME_LEN) is None

    def test_is_allowed_digit_rejected(self):
        assert is_allowed_llm_input_char("0") is False

    def test_is_allowed_tab_uses_isspace_branch(self):
        assert is_allowed_llm_input_char("\t") is True

    def test_normalize_province_invalid_full_name_allowlist(self):
        code, err = normalize_province_for_query("Ontario@")
        assert code is None
        assert err == "Invalid province"

    def test_normalize_province_invalid_two_letter_code(self):
        code, err = normalize_province_for_query("ZZ")
        assert code is None
        assert err == "Invalid province code"

    def test_normalize_province_whitespace_only_returns_empty(self):
        code, err = normalize_province_for_query("   ")
        assert code == ""
        assert err is None

    def test_normalize_province_full_name_via_allowlist(self):
        code, err = normalize_province_for_query("Ontario")
        assert err is None
        assert code == "Ontario"
