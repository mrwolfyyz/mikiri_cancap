"""Tests for contact_extraction_utils.py.

The module has heavy Vertex AI dependencies at the module level.
We mock these in sys.modules before import so the module loads in
the test environment without GCP credentials.

The most valuable thing to test here is the post-processing logic:
phone normalization/dedup, email filtering/dedup, and address
validation/dedup. We mock the LLM response and verify the output.
"""

import sys
import json
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Mock Vertex AI modules BEFORE importing contact_extraction_utils.
# The module does:
#   import vertexai
#   from vertexai.generative_models import GenerativeModel, GenerationConfig
# ---------------------------------------------------------------------------
_mock_vertexai = MagicMock()
_mock_gen_models = MagicMock()
sys.modules.setdefault("vertexai", _mock_vertexai)
sys.modules.setdefault("vertexai.generative_models", _mock_gen_models)

# Now safe to import
import contact_extraction_utils
from contact_extraction_utils import extract_contact_info_llm, EXTRACTION_SCHEMA


# ---------------------------------------------------------------------------
# Helper to set up a mocked LLM response for extract_contact_info_llm.
# Returns (MockGenerativeModel, mock_model_instance) so tests can inspect
# calls and configure the response.
# ---------------------------------------------------------------------------
def _run_extraction(llm_response_data, seed=None, exclude_email=None, queries=None):
    """Run extract_contact_info_llm with a mocked LLM response."""
    seed = seed or {"full_name": "John Smith", "email": "john@example.com"}
    queries = queries or [{"hits": [{"title": "test", "snippet": "info", "url": "https://example.com"}]}]

    mock_response = MagicMock()
    mock_response.text = json.dumps(llm_response_data)

    with (
        patch.object(contact_extraction_utils, "GCP_PROJECT", "test-project"),
        patch.object(contact_extraction_utils, "vertexai"),
        patch.object(contact_extraction_utils, "GenerativeModel") as MockGenModel,
    ):
        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response
        MockGenModel.return_value = mock_model

        return extract_contact_info_llm(queries, seed, exclude_email)


# ===========================================================================
# Schema tests
# ===========================================================================
class TestExtractionSchema:
    """Tests for the EXTRACTION_SCHEMA constant."""

    def test_schema_has_required_top_level_keys(self):
        assert set(EXTRACTION_SCHEMA["required"]) == {"phones", "emails", "addresses"}

    def test_phone_schema_requires_fields(self):
        phone_required = EXTRACTION_SCHEMA["properties"]["phones"]["items"]["required"]
        assert "number_raw" in phone_required
        assert "number_digits" in phone_required
        assert "confidence" in phone_required
        assert "source_url" in phone_required

    def test_email_schema_requires_fields(self):
        email_required = EXTRACTION_SCHEMA["properties"]["emails"]["items"]["required"]
        assert "email" in email_required
        assert "confidence" in email_required
        assert "source_url" in email_required

    def test_address_schema_requires_fields(self):
        addr_required = EXTRACTION_SCHEMA["properties"]["addresses"]["items"]["required"]
        assert "address_raw" in addr_required
        assert "confidence" in addr_required
        assert "source_url" in addr_required


# ===========================================================================
# Early returns
# ===========================================================================
class TestEarlyReturns:
    """Tests for cases where extract_contact_info_llm returns early."""

    def test_returns_empty_when_no_gcp_project(self):
        with patch.object(contact_extraction_utils, "GCP_PROJECT", ""):
            result = extract_contact_info_llm(
                queries=[{"hits": [{"title": "test"}]}],
                seed={"full_name": "John Smith", "email": "john@example.com"},
            )
        assert result == {"phones": [], "emails": [], "addresses": []}

    def test_returns_empty_when_no_hits(self):
        with (
            patch.object(contact_extraction_utils, "GCP_PROJECT", "test-project"),
            patch.object(contact_extraction_utils, "vertexai"),
        ):
            result = extract_contact_info_llm(
                queries=[{"hits": []}],
                seed={"full_name": "John Smith", "email": "john@example.com"},
            )
        assert result == {"phones": [], "emails": [], "addresses": []}

    def test_returns_empty_when_queries_have_no_hits_key(self):
        with (
            patch.object(contact_extraction_utils, "GCP_PROJECT", "test-project"),
            patch.object(contact_extraction_utils, "vertexai"),
        ):
            result = extract_contact_info_llm(
                queries=[{}],
                seed={"full_name": "John Smith", "email": "john@example.com"},
            )
        assert result == {"phones": [], "emails": [], "addresses": []}

    def test_returns_empty_when_vertex_ai_init_fails(self):
        mock_vertexai = MagicMock()
        mock_vertexai.init.side_effect = Exception("Auth failed")
        with (
            patch.object(contact_extraction_utils, "GCP_PROJECT", "test-project"),
            patch.object(contact_extraction_utils, "vertexai", mock_vertexai),
        ):
            result = extract_contact_info_llm(
                queries=[{"hits": [{"title": "test"}]}],
                seed={"full_name": "John Smith", "email": "john@example.com"},
            )
        assert result == {"phones": [], "emails": [], "addresses": []}


# ===========================================================================
# Phone normalization
# ===========================================================================
class TestPhoneNormalization:
    """Tests for phone number normalization and deduplication."""

    def test_basic_phone_extraction(self):
        result = _run_extraction({
            "phones": [
                {"number_raw": "(416) 555-1234", "number_digits": "4165551234",
                 "confidence": "high", "source_url": "https://a.com", "snippet": "Call John"},
            ],
            "emails": [],
            "addresses": [],
        })
        assert len(result["phones"]) == 1
        assert result["phones"][0]["number_digits"] == "4165551234"
        assert result["phones"][0]["confidence"] == "high"

    def test_phone_digits_re_extracted_from_raw(self):
        """Digits are re-computed from number_raw, not trusted from LLM."""
        result = _run_extraction({
            "phones": [
                {"number_raw": "+1 (416) 555-1234", "number_digits": "wrong_from_llm",
                 "confidence": "high", "source_url": "https://a.com"},
            ],
            "emails": [],
            "addresses": [],
        })
        assert result["phones"][0]["number_digits"] == "14165551234"

    def test_phone_deduplication_by_digits(self):
        """Duplicate phone numbers (same digits, different formatting) are deduplicated."""
        result = _run_extraction({
            "phones": [
                {"number_raw": "(416) 555-1234", "number_digits": "4165551234",
                 "confidence": "high", "source_url": "https://a.com"},
                {"number_raw": "416-555-1234", "number_digits": "4165551234",
                 "confidence": "medium", "source_url": "https://b.com"},
                {"number_raw": "4165551234", "number_digits": "4165551234",
                 "confidence": "low", "source_url": "https://c.com"},
            ],
            "emails": [],
            "addresses": [],
        })
        assert len(result["phones"]) == 1
        # First occurrence wins
        assert result["phones"][0]["source_url"] == "https://a.com"

    def test_phone_empty_number_raw_skipped(self):
        result = _run_extraction({
            "phones": [
                {"number_raw": "", "number_digits": "", "confidence": "high", "source_url": "https://a.com"},
                {"number_raw": "  ", "number_digits": "", "confidence": "high", "source_url": "https://a.com"},
            ],
            "emails": [],
            "addresses": [],
        })
        assert len(result["phones"]) == 0

    def test_phone_no_digits_in_raw_skipped(self):
        """If number_raw has no digits at all, the phone is skipped."""
        result = _run_extraction({
            "phones": [
                {"number_raw": "no digits here", "number_digits": "", "confidence": "high", "source_url": "https://a.com"},
            ],
            "emails": [],
            "addresses": [],
        })
        assert len(result["phones"]) == 0

    def test_phone_invalid_confidence_defaults_to_medium(self):
        result = _run_extraction({
            "phones": [
                {"number_raw": "4165551234", "number_digits": "4165551234",
                 "confidence": "very_high", "source_url": "https://a.com"},
            ],
            "emails": [],
            "addresses": [],
        })
        assert result["phones"][0]["confidence"] == "medium"

    def test_phone_valid_confidence_values_preserved(self):
        for conf in ("high", "medium", "low"):
            result = _run_extraction({
                "phones": [
                    {"number_raw": "4165551234", "number_digits": "4165551234",
                     "confidence": conf, "source_url": "https://a.com"},
                ],
                "emails": [],
                "addresses": [],
            })
            assert result["phones"][0]["confidence"] == conf

    def test_phone_non_dict_entries_skipped(self):
        result = _run_extraction({
            "phones": ["not a dict", 42, None],
            "emails": [],
            "addresses": [],
        })
        assert len(result["phones"]) == 0

    def test_phone_snippet_whitespace_stripped(self):
        result = _run_extraction({
            "phones": [
                {"number_raw": "4165551234", "number_digits": "4165551234",
                 "confidence": "high", "source_url": "https://a.com",
                 "snippet": "  call here  "},
            ],
            "emails": [],
            "addresses": [],
        })
        assert result["phones"][0]["snippet"] == "call here"

    def test_phone_missing_snippet_defaults_empty(self):
        result = _run_extraction({
            "phones": [
                {"number_raw": "4165551234", "number_digits": "4165551234",
                 "confidence": "high", "source_url": "https://a.com"},
            ],
            "emails": [],
            "addresses": [],
        })
        assert result["phones"][0]["snippet"] == ""


# ===========================================================================
# Email normalization
# ===========================================================================
class TestEmailNormalization:
    """Tests for email normalization and deduplication."""

    def test_basic_email_extraction(self):
        result = _run_extraction({
            "phones": [],
            "emails": [
                {"email": "john@work.com", "confidence": "high",
                 "source_url": "https://a.com", "snippet": "Contact: john@work.com"},
            ],
            "addresses": [],
        })
        assert len(result["emails"]) == 1
        assert result["emails"][0]["email"] == "john@work.com"

    def test_email_deduplication_case_insensitive(self):
        result = _run_extraction({
            "phones": [],
            "emails": [
                {"email": "John@Work.com", "confidence": "high", "source_url": "https://a.com"},
                {"email": "john@work.com", "confidence": "medium", "source_url": "https://b.com"},
                {"email": "JOHN@WORK.COM", "confidence": "low", "source_url": "https://c.com"},
            ],
            "addresses": [],
        })
        assert len(result["emails"]) == 1
        # First occurrence wins (preserves original casing)
        assert result["emails"][0]["email"] == "John@Work.com"

    def test_email_exclusion(self):
        """The exclude_email is filtered out (case insensitive)."""
        result = _run_extraction(
            {
                "phones": [],
                "emails": [
                    {"email": "john@example.com", "confidence": "high", "source_url": "https://a.com"},
                    {"email": "john@work.com", "confidence": "high", "source_url": "https://b.com"},
                ],
                "addresses": [],
            },
            exclude_email="John@Example.com",
        )
        assert len(result["emails"]) == 1
        assert result["emails"][0]["email"] == "john@work.com"

    def test_email_no_exclusion_when_none(self):
        result = _run_extraction(
            {
                "phones": [],
                "emails": [
                    {"email": "john@example.com", "confidence": "high", "source_url": "https://a.com"},
                ],
                "addresses": [],
            },
            exclude_email=None,
        )
        assert len(result["emails"]) == 1

    def test_email_empty_skipped(self):
        result = _run_extraction({
            "phones": [],
            "emails": [
                {"email": "", "confidence": "high", "source_url": "https://a.com"},
                {"email": "  ", "confidence": "high", "source_url": "https://a.com"},
            ],
            "addresses": [],
        })
        assert len(result["emails"]) == 0

    def test_email_non_dict_entries_skipped(self):
        result = _run_extraction({
            "phones": [],
            "emails": ["not-a-dict", 99],
            "addresses": [],
        })
        assert len(result["emails"]) == 0

    def test_email_invalid_confidence_defaults_to_medium(self):
        result = _run_extraction({
            "phones": [],
            "emails": [
                {"email": "test@example.com", "confidence": "super", "source_url": "https://a.com"},
            ],
            "addresses": [],
        })
        assert result["emails"][0]["confidence"] == "medium"


# ===========================================================================
# Address normalization
# ===========================================================================
class TestAddressNormalization:
    """Tests for address validation, normalization, and deduplication."""

    def test_address_with_street_number_kept(self):
        result = _run_extraction({
            "phones": [],
            "emails": [],
            "addresses": [
                {"address_raw": "123 Main St, Toronto, ON M5V 1A1", "confidence": "high",
                 "source_url": "https://a.com"},
            ],
        })
        assert len(result["addresses"]) == 1

    def test_address_with_street_name_pattern_kept(self):
        """Addresses with street name indicators (Avenue, Street, etc.) are kept."""
        for street_name in ["Bay Street, Toronto", "King Avenue, Hamilton", "Yonge Road, Markham"]:
            result = _run_extraction({
                "phones": [],
                "emails": [],
                "addresses": [
                    {"address_raw": street_name, "confidence": "medium", "source_url": "https://a.com"},
                ],
            })
            assert len(result["addresses"]) == 1, f"Expected '{street_name}' to be kept"

    def test_address_city_only_filtered(self):
        """Addresses with only city/province (no street info) are filtered out."""
        result = _run_extraction({
            "phones": [],
            "emails": [],
            "addresses": [
                {"address_raw": "Toronto, Ontario", "confidence": "high", "source_url": "https://a.com"},
                {"address_raw": "Vancouver, BC", "confidence": "medium", "source_url": "https://b.com"},
            ],
        })
        assert len(result["addresses"]) == 0

    def test_address_deduplication(self):
        """Duplicate addresses (normalized) are deduplicated."""
        result = _run_extraction({
            "phones": [],
            "emails": [],
            "addresses": [
                {"address_raw": "123 Main Street, Toronto, ON", "confidence": "high",
                 "source_url": "https://a.com"},
                {"address_raw": "123 Main Street, Toronto, ON", "confidence": "medium",
                 "source_url": "https://b.com"},
            ],
        })
        assert len(result["addresses"]) == 1

    def test_address_empty_skipped(self):
        result = _run_extraction({
            "phones": [],
            "emails": [],
            "addresses": [
                {"address_raw": "", "confidence": "high", "source_url": "https://a.com"},
                {"address_raw": "  ", "confidence": "high", "source_url": "https://a.com"},
            ],
        })
        assert len(result["addresses"]) == 0

    def test_address_non_dict_entries_skipped(self):
        result = _run_extraction({
            "phones": [],
            "emails": [],
            "addresses": ["not a dict", None],
        })
        assert len(result["addresses"]) == 0

    def test_address_invalid_confidence_defaults_to_medium(self):
        result = _run_extraction({
            "phones": [],
            "emails": [],
            "addresses": [
                {"address_raw": "123 Main Street, Toronto, ON", "confidence": "invalid",
                 "source_url": "https://a.com"},
            ],
        })
        assert result["addresses"][0]["confidence"] == "medium"

    def test_address_with_various_street_types(self):
        """Street type abbreviations and full names are recognized."""
        street_types = [
            "100 King Drive", "200 Queen Boulevard", "300 Bay Crescent",
            "400 Lake Lane", "500 Park Way", "600 Oak Court",
        ]
        for addr in street_types:
            result = _run_extraction({
                "phones": [],
                "emails": [],
                "addresses": [
                    {"address_raw": addr, "confidence": "high", "source_url": "https://a.com"},
                ],
            })
            assert len(result["addresses"]) == 1, f"Expected '{addr}' to be kept"


# ===========================================================================
# LLM response handling (edge cases)
# ===========================================================================
class TestLlmResponseHandling:
    """Tests for handling various LLM response shapes."""

    def test_missing_keys_default_to_empty_lists(self):
        """LLM response missing expected keys gets empty list defaults."""
        result = _run_extraction({"phones": []})  # missing emails, addresses
        assert result["emails"] == []
        assert result["addresses"] == []

    def test_non_list_values_default_to_empty_lists(self):
        """Non-list values for phones/emails/addresses are replaced with empty lists."""
        result = _run_extraction({
            "phones": "not a list",
            "emails": 42,
            "addresses": {"wrong": "type"},
        })
        assert result["phones"] == []
        assert result["emails"] == []
        assert result["addresses"] == []

    def test_markdown_wrapped_json_stripped(self):
        """JSON wrapped in markdown code blocks is handled."""
        llm_data = {"phones": [], "emails": [], "addresses": []}
        markdown_text = f"```json\n{json.dumps(llm_data)}\n```"

        mock_response = MagicMock()
        mock_response.text = markdown_text

        with (
            patch.object(contact_extraction_utils, "GCP_PROJECT", "test-project"),
            patch.object(contact_extraction_utils, "vertexai"),
            patch.object(contact_extraction_utils, "GenerativeModel") as MockGenModel,
        ):
            mock_model = MagicMock()
            mock_model.generate_content.return_value = mock_response
            MockGenModel.return_value = mock_model

            result = extract_contact_info_llm(
                [{"hits": [{"title": "test"}]}],
                {"full_name": "John Smith", "email": "j@e.com"},
            )

        assert result == {"phones": [], "emails": [], "addresses": []}

    @patch("retry_utils.time.sleep")
    def test_empty_llm_response_returns_empty_after_retries(self, mock_sleep):
        """Empty LLM responses exhaust retries and return empty results gracefully."""
        mock_response = MagicMock()
        mock_response.text = ""

        with (
            patch.object(contact_extraction_utils, "GCP_PROJECT", "test-project"),
            patch.object(contact_extraction_utils, "vertexai"),
            patch.object(contact_extraction_utils, "GenerativeModel") as MockGenModel,
        ):
            mock_model = MagicMock()
            mock_model.generate_content.return_value = mock_response
            MockGenModel.return_value = mock_model

            result = extract_contact_info_llm(
                [{"hits": [{"title": "test"}]}],
                {"full_name": "John Smith", "email": "j@e.com"},
            )

        assert result == {"phones": [], "emails": [], "addresses": []}

    @patch("retry_utils.time.sleep")
    def test_malformed_json_returns_empty_after_retries(self, mock_sleep):
        """Malformed JSON from LLM exhausts retries and returns empty results."""
        mock_response = MagicMock()
        mock_response.text = "not valid json {"

        with (
            patch.object(contact_extraction_utils, "GCP_PROJECT", "test-project"),
            patch.object(contact_extraction_utils, "vertexai"),
            patch.object(contact_extraction_utils, "GenerativeModel") as MockGenModel,
        ):
            mock_model = MagicMock()
            mock_model.generate_content.return_value = mock_response
            MockGenModel.return_value = mock_model

            result = extract_contact_info_llm(
                [{"hits": [{"title": "test"}]}],
                {"full_name": "John Smith", "email": "j@e.com"},
            )

        assert result == {"phones": [], "emails": [], "addresses": []}

    @patch("retry_utils.time.sleep")
    def test_null_response_object_returns_empty_after_retries(self, mock_sleep):
        """Falsy response object exhausts retries and returns empty results."""
        with (
            patch.object(contact_extraction_utils, "GCP_PROJECT", "test-project"),
            patch.object(contact_extraction_utils, "vertexai"),
            patch.object(contact_extraction_utils, "GenerativeModel") as MockGenModel,
        ):
            mock_model = MagicMock()
            mock_model.generate_content.return_value = None
            MockGenModel.return_value = mock_model

            result = extract_contact_info_llm(
                [{"hits": [{"title": "test"}]}],
                {"full_name": "John Smith", "email": "j@e.com"},
            )

        assert result == {"phones": [], "emails": [], "addresses": []}


# ===========================================================================
# Full end-to-end extraction
# ===========================================================================
class TestFullExtraction:
    """End-to-end tests with realistic LLM responses."""

    def test_realistic_extraction(self):
        """Realistic multi-result extraction with dedup and filtering."""
        result = _run_extraction(
            {
                "phones": [
                    {"number_raw": "(416) 555-0100", "number_digits": "4165550100",
                     "confidence": "high", "source_url": "https://linkedin.com/in/jsmith",
                     "snippet": "Contact: (416) 555-0100"},
                    {"number_raw": "416-555-0100", "number_digits": "4165550100",
                     "confidence": "medium", "source_url": "https://company.com/team",
                     "snippet": "Phone: 416-555-0100"},
                    {"number_raw": "(905) 555-0200", "number_digits": "9055550200",
                     "confidence": "medium", "source_url": "https://whitepages.com",
                     "snippet": "John Smith - (905) 555-0200"},
                ],
                "emails": [
                    {"email": "jsmith@company.com", "confidence": "high",
                     "source_url": "https://company.com/team"},
                    {"email": "john@example.com", "confidence": "high",
                     "source_url": "https://linkedin.com"},
                    {"email": "JSmith@Company.com", "confidence": "medium",
                     "source_url": "https://rocketreach.com"},
                ],
                "addresses": [
                    {"address_raw": "42 Elm Street, Toronto, ON M4C 1N5", "confidence": "high",
                     "source_url": "https://whitepages.com"},
                    {"address_raw": "Toronto, Ontario", "confidence": "low",
                     "source_url": "https://linkedin.com"},
                    {"address_raw": "42 Elm Street, Toronto, ON M4C 1N5", "confidence": "medium",
                     "source_url": "https://canada411.com"},
                ],
            },
            seed={"full_name": "John Smith", "email": "john@example.com"},
            exclude_email="john@example.com",
        )

        # 2 unique phones (one deduped)
        assert len(result["phones"]) == 2
        digits = {p["number_digits"] for p in result["phones"]}
        assert digits == {"4165550100", "9055550200"}

        # 1 email (seed excluded, case-insensitive dedup removes another)
        assert len(result["emails"]) == 1
        assert result["emails"][0]["email"] == "jsmith@company.com"

        # 1 address (city-only filtered, duplicate removed)
        assert len(result["addresses"]) == 1
        assert "Elm Street" in result["addresses"][0]["address_raw"]
