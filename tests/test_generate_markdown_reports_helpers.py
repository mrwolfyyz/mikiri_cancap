"""Tests for pure helper functions in generate_markdown_reports.py (origination).

These are all pure functions with no external dependencies — easy to test
and high-confidence gains for the report generation pipeline.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Load the module using the same pattern as other test files.
# We need to add the function directory to sys.path so local imports resolve.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
FN_DIR = str(REPO_ROOT / "gcp" / "functions" / "report_generator_origination")

# Mock heavy dependencies before importing the module.
# generate_markdown_reports.py -> contact_extraction_utils -> vertexai
_mock_pyap = MagicMock()
_mock_vertexai = MagicMock()
_mock_vertexai_gm = MagicMock()

sys.modules.setdefault("pyap", _mock_pyap)
sys.modules.setdefault("vertexai", _mock_vertexai)
sys.modules.setdefault("vertexai.generative_models", _mock_vertexai_gm)

sys.path.insert(0, FN_DIR)
try:
    from generate_markdown_reports import (
        clean_address,
        extract_1st_addresses_fallback,
        extract_address_components,
        extract_addresses_from_queries,
        extract_email_handle,
        extract_linkedin_connections,
        format_name,
        generate_canada411_url,
        generate_google_doc_search_url,
        generate_google_doc_search_url_for_email,
        generate_google_doc_search_url_for_phone,
        get_all_linkedin_snippets,
        get_domain_age_callout,
        get_mx_callout,
    )
finally:
    try:
        sys.path.remove(FN_DIR)
    except ValueError:
        pass


# ===========================================================================
# format_name
# ===========================================================================
class TestFormatName:
    """Tests for format_name() — simple title case."""

    def test_lowercase(self):
        assert format_name("john doe") == "John Doe"

    def test_uppercase(self):
        assert format_name("JOHN DOE") == "John Doe"

    def test_mixed_case(self):
        assert format_name("jOhN dOe") == "John Doe"

    def test_single_name(self):
        assert format_name("alice") == "Alice"

    def test_already_title_case(self):
        assert format_name("John Doe") == "John Doe"


# ===========================================================================
# get_mx_callout
# ===========================================================================
class TestGetMxCallout:
    """Tests for get_mx_callout() — risk level to callout type mapping."""

    def test_critical_returns_danger(self):
        assert get_mx_callout({"success": True, "risk_level": "CRITICAL"}) == "danger"

    def test_high_returns_danger(self):
        assert get_mx_callout({"success": True, "risk_level": "HIGH"}) == "danger"

    def test_medium_returns_warning(self):
        assert get_mx_callout({"success": True, "risk_level": "MEDIUM"}) == "warning"

    def test_low_medium_returns_warning(self):
        assert get_mx_callout({"success": True, "risk_level": "LOW/MEDIUM"}) == "warning"

    def test_low_returns_info(self):
        assert get_mx_callout({"success": True, "risk_level": "LOW"}) == "info"

    def test_unknown_returns_warning(self):
        assert get_mx_callout({"success": True, "risk_level": "UNKNOWN"}) == "warning"

    def test_none_returns_warning(self):
        assert get_mx_callout(None) == "warning"

    def test_failed_returns_warning(self):
        assert get_mx_callout({"success": False}) == "warning"

    def test_missing_risk_level_returns_warning(self):
        assert get_mx_callout({"success": True}) == "warning"


# ===========================================================================
# get_domain_age_callout
# ===========================================================================
class TestGetDomainAgeCallout:
    """Tests for get_domain_age_callout() — domain age to callout type."""

    def test_very_new_domain_returns_danger(self):
        recent = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        assert get_domain_age_callout(recent) == "danger"

    def test_domain_under_year_returns_warning(self):
        six_months = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        assert get_domain_age_callout(six_months) == "warning"

    def test_old_domain_returns_info(self):
        old = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
        assert get_domain_age_callout(old) == "info"

    def test_exactly_90_days_returns_warning(self):
        ninety = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        assert get_domain_age_callout(ninety) == "warning"

    def test_exactly_365_days_returns_info(self):
        one_year = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        assert get_domain_age_callout(one_year) == "info"

    def test_invalid_date_returns_info(self):
        assert get_domain_age_callout("not-a-date") == "info"

    def test_empty_string_returns_info(self):
        assert get_domain_age_callout("") == "info"


# ===========================================================================
# extract_linkedin_connections
# ===========================================================================
class TestExtractLinkedinConnections:
    """Tests for extract_linkedin_connections() — regex extraction."""

    def test_500_plus_connections(self):
        assert extract_linkedin_connections("500+ connections") == 500

    def test_plain_connections(self):
        assert extract_linkedin_connections("200 connections") == 200

    def test_1k_connections(self):
        assert extract_linkedin_connections("1K+ connections") == 1000

    def test_5k_connections(self):
        assert extract_linkedin_connections("5K connections") == 5000

    def test_connected_to(self):
        assert extract_linkedin_connections("connected to 300") == 300

    def test_connected_with(self):
        assert extract_linkedin_connections("connected with 150") == 150

    def test_no_match(self):
        assert extract_linkedin_connections("Some random text about LinkedIn") is None

    def test_empty_string(self):
        assert extract_linkedin_connections("") is None

    def test_none_input(self):
        assert extract_linkedin_connections(None) is None

    def test_connection_singular(self):
        assert extract_linkedin_connections("1 connection") == 1

    def test_embedded_in_longer_text(self):
        snippet = "John Doe - Software Developer at Acme Corp. 500+ connections on LinkedIn."
        assert extract_linkedin_connections(snippet) == 500


# ===========================================================================
# get_all_linkedin_snippets
# ===========================================================================
class TestGetAllLinkedinSnippets:
    """Tests for get_all_linkedin_snippets()."""

    def test_no_handles(self):
        assert get_all_linkedin_snippets([], []) == []

    def test_none_handles(self):
        assert get_all_linkedin_snippets(None, []) == []

    def test_no_linkedin_handles(self):
        handles = [{"platform": "Twitter", "handle": "@user", "url": "https://twitter.com/user"}]
        assert get_all_linkedin_snippets(handles, []) == []

    def test_linkedin_handle_with_matching_query(self):
        handles = [{"platform": "LinkedIn", "handle": "johndoe", "url": "https://linkedin.com/in/johndoe"}]
        queries = [{"hits": [{"url": "https://linkedin.com/in/johndoe", "snippet": "500+ connections"}]}]
        result = get_all_linkedin_snippets(handles, queries)
        assert result == ["500+ connections"]

    def test_trailing_slash_normalization(self):
        handles = [{"platform": "LinkedIn", "handle": "johndoe", "url": "https://linkedin.com/in/johndoe/"}]
        queries = [{"hits": [{"url": "https://linkedin.com/in/johndoe", "snippet": "250 connections"}]}]
        result = get_all_linkedin_snippets(handles, queries)
        assert result == ["250 connections"]

    def test_linkedin_detected_by_url(self):
        handles = [{"platform": "Professional", "handle": "johndoe", "url": "https://linkedin.com/in/johndoe"}]
        queries = [{"hits": [{"url": "https://linkedin.com/in/johndoe", "snippet": "test snippet"}]}]
        result = get_all_linkedin_snippets(handles, queries)
        assert result == ["test snippet"]

    def test_no_matching_query_returns_empty(self):
        handles = [{"platform": "LinkedIn", "handle": "johndoe", "url": "https://linkedin.com/in/johndoe"}]
        queries = [{"hits": [{"url": "https://linkedin.com/in/other", "snippet": "other person"}]}]
        assert get_all_linkedin_snippets(handles, queries) == []


# ===========================================================================
# extract_email_handle
# ===========================================================================
class TestExtractEmailHandle:
    """Tests for extract_email_handle()."""

    def test_normal_email(self):
        assert extract_email_handle("john.doe@example.com") == "john.doe"

    def test_no_at_sign(self):
        assert extract_email_handle("not-an-email") == ""

    def test_empty_string(self):
        assert extract_email_handle("") == ""

    def test_none_input(self):
        assert extract_email_handle(None) == ""

    def test_multiple_at_signs(self):
        assert extract_email_handle("user@domain@extra") == "user"


# ===========================================================================
# clean_address
# ===========================================================================
class TestCleanAddress:
    """Tests for clean_address() — junk prefix removal."""

    def test_canadian_address_with_junk_prefix(self):
        addr = "6 Marvin Igelman 148 Arnold Avenue Vaughan ON L4J 1B7 Canada"
        result = clean_address(addr)
        assert "148" in result
        assert "Arnold" in result
        assert "ON" in result
        assert "L4J" in result

    def test_us_address(self):
        addr = "123 Main Street, Springfield, IL 62701"
        result = clean_address(addr)
        assert "123" in result
        assert "Main" in result
        assert "IL" in result

    def test_simple_address_unchanged(self):
        addr = "456 Queen Ave Toronto ON M5V 2K1"
        result = clean_address(addr)
        assert "456" in result
        assert "Queen" in result

    def test_us_address_with_zip_plus_4(self):
        addr = "789 Oak Blvd, Chicago, IL 60601-1234"
        result = clean_address(addr)
        assert "789" in result
        assert "60601" in result

    def test_fallback_no_state_or_province(self):
        addr = "123 Some Street SomeTown"
        result = clean_address(addr)
        assert "123" in result

    def test_empty_fallback(self):
        addr = "No address here"
        result = clean_address(addr)
        assert result == "No address here"


# ===========================================================================
# extract_address_components
# ===========================================================================
class TestExtractAddressComponents:
    """Tests for extract_address_components()."""

    def test_full_structured_data(self):
        data = {
            "address_raw": "123 Main St, Toronto, ON M5V 2K1",
            "street_number": "123",
            "street_name": "Main St",
            "city": "Toronto",
            "province": "ON",
            "postal_code": "M5V2K1",
            "state": None,
            "zip_code": None,
        }
        result = extract_address_components(data)
        assert result["street"] == "123 Main St"
        assert result["city"] == "Toronto"
        assert result["province"] == "ON"
        assert result["postal_code"] == "M5V2K1"

    def test_street_name_only(self):
        data = {
            "address_raw": "",
            "street_number": None,
            "street_name": "Main St",
            "city": "Toronto",
            "province": "ON",
            "state": None,
            "postal_code": "M5V2K1",
            "zip_code": None,
        }
        result = extract_address_components(data)
        assert result["street"] == "Main St"

    def test_street_number_only(self):
        data = {
            "address_raw": "",
            "street_number": "123",
            "street_name": None,
            "city": "Toronto",
            "province": "ON",
            "state": None,
            "postal_code": "M5V2K1",
            "zip_code": None,
        }
        result = extract_address_components(data)
        assert result["street"] == "123"

    def test_fallback_to_raw_canadian(self):
        data = {
            "address_raw": "456 Queen Ave Toronto ON M5V 2K1",
            "street_number": None,
            "street_name": None,
            "city": None,
            "province": None,
            "state": None,
            "postal_code": None,
            "zip_code": None,
        }
        result = extract_address_components(data)
        assert result["province"] == "ON"

    def test_fallback_to_raw_us(self):
        data = {
            "address_raw": "789 Oak Blvd, Chicago, IL 60601",
            "street_number": None,
            "street_name": None,
            "city": None,
            "province": None,
            "state": None,
            "postal_code": None,
            "zip_code": None,
        }
        result = extract_address_components(data)
        assert result["state"] == "IL"
        assert result["zip_code"] == "60601"

    def test_no_components_no_raw_fallback(self):
        data = {
            "address_raw": "unknown location",
            "street_number": None,
            "street_name": None,
            "city": None,
            "province": None,
            "state": None,
            "postal_code": None,
            "zip_code": None,
        }
        result = extract_address_components(data)
        assert result["street"] is None
        assert result["city"] is None


# ===========================================================================
# generate_canada411_url
# ===========================================================================
class TestGenerateCanada411Url:
    """Tests for generate_canada411_url()."""

    def test_full_components(self):
        data = {
            "address_raw": "123 Main St, Toronto, ON M5V 2K1",
            "street_number": "123",
            "street_name": "Main St",
            "city": "Toronto",
            "province": "ON",
            "postal_code": "M5V2K1",
            "state": None,
            "zip_code": None,
        }
        url = generate_canada411_url(data)
        assert "canada411.ca" in url
        assert "stype=ad" in url
        assert "st=" in url
        assert "ci=" in url
        assert "pv=" in url

    def test_partial_components(self):
        data = {
            "address_raw": "123 Main St, Toronto",
            "street_number": None,
            "street_name": None,
            "city": None,
            "province": None,
            "state": None,
            "postal_code": None,
            "zip_code": None,
        }
        url = generate_canada411_url(data)
        assert "canada411.ca" in url

    def test_empty_address_fallback(self):
        data = {
            "address_raw": "unknown",
            "street_number": None,
            "street_name": None,
            "city": None,
            "province": None,
            "state": None,
            "postal_code": None,
            "zip_code": None,
        }
        url = generate_canada411_url(data)
        assert "canada411.ca" in url


# ===========================================================================
# Google doc search URL functions
# ===========================================================================
class TestGoogleDocSearchUrls:
    """Tests for Google document search URL generation."""

    def test_address_doc_search(self):
        url = generate_google_doc_search_url({"address_raw": "123 Main St Toronto"})
        assert "google.com/search" in url
        assert "filetype" in url

    def test_address_doc_search_empty(self):
        url = generate_google_doc_search_url({"address_raw": ""})
        assert "google.com/search" in url
        assert "filetype" in url

    def test_phone_doc_search_with_digits(self):
        url = generate_google_doc_search_url_for_phone({"number_digits": "4165551234"})
        assert "google.com/search" in url
        assert "filetype" in url

    def test_phone_doc_search_no_digits_fallback(self):
        url = generate_google_doc_search_url_for_phone({"number_raw": "416-555-1234"})
        assert "google.com/search" in url
        assert "filetype" in url

    def test_phone_doc_search_empty(self):
        url = generate_google_doc_search_url_for_phone({})
        assert "google.com/search" in url

    def test_email_doc_search(self):
        url = generate_google_doc_search_url_for_email("test@example.com")
        assert "google.com/search" in url
        assert "filetype" in url

    def test_email_doc_search_empty(self):
        url = generate_google_doc_search_url_for_email("")
        assert "google.com/search" in url


# ===========================================================================
# extract_1st_addresses_fallback
# ===========================================================================
class TestExtract1stAddressesFallback:
    """Tests for extract_1st_addresses_fallback() — regex for '1st' addresses."""

    def test_1st_street_address(self):
        text = "Located at 123 1st Avenue NW, Calgary, AB, 12345"
        result = extract_1st_addresses_fallback(text)
        assert len(result) >= 1
        assert "123" in result[0]
        assert "1st" in result[0]

    def test_first_street_address_with_direction(self):
        text = "Office at 456 First Street NW, Denver, CO, 80201"
        result = extract_1st_addresses_fallback(text)
        assert len(result) >= 1
        assert "456" in result[0]
        assert "NW" in result[0]

    def test_no_match(self):
        text = "No 1st address patterns here at all"
        result = extract_1st_addresses_fallback(text)
        assert result == []

    def test_empty_string(self):
        result = extract_1st_addresses_fallback("")
        assert result == []


# ===========================================================================
# extract_addresses_from_queries (with mocked pyap)
# ===========================================================================
class TestExtractAddressesFromQueries:
    """Tests for extract_addresses_from_queries()."""

    def test_empty_queries(self):
        result = extract_addresses_from_queries([])
        assert result == []

    def test_none_queries(self):
        result = extract_addresses_from_queries(None)
        assert result == []

    def test_queries_with_no_hits(self):
        queries = [{"hits": []}]
        result = extract_addresses_from_queries(queries)
        assert result == []

    def test_deduplication(self):
        """Duplicate addresses (after normalization) should be deduplicated."""
        # Mock pyap.parse to return consistent address objects
        mock_addr = MagicMock()
        mock_addr.__str__ = lambda self: "123 Main St, Toronto, ON M5V 2K1"
        mock_addr.street_number = "123"
        mock_addr.street_name = "Main St"
        mock_addr.city = "Toronto"
        mock_addr.province = "ON"
        mock_addr.postal_code = "M5V2K1"
        mock_addr.state = None
        mock_addr.zip_code = None

        # Use the actual pyap mock from sys.modules (may differ from local _mock_pyap
        # when other test files run first and install their own mock via setdefault)
        pyap_mock = sys.modules["pyap"]
        pyap_mock.parse.return_value = [mock_addr]

        queries = [
            {
                "hits": [
                    {"title": "Page 1", "snippet": "at 123 Main St Toronto ON M5V 2K1", "url": "http://a.com"},
                    {"title": "Page 2", "snippet": "at 123 Main St Toronto ON M5V 2K1", "url": "http://b.com"},
                ]
            }
        ]
        result = extract_addresses_from_queries(queries)
        # Should be deduplicated to 1 result
        assert len(result) == 1

    def test_city_only_address_filtered(self):
        """Addresses without street components should be filtered out."""
        mock_addr = MagicMock()
        mock_addr.__str__ = lambda self: "Toronto, ON M5V 2K1"
        mock_addr.street_number = None
        mock_addr.street_name = None
        mock_addr.city = "Toronto"
        mock_addr.province = "ON"
        mock_addr.postal_code = "M5V2K1"
        mock_addr.state = None
        mock_addr.zip_code = None

        pyap_mock = sys.modules["pyap"]
        pyap_mock.parse.return_value = [mock_addr]

        queries = [{"hits": [{"title": "Page", "snippet": "Toronto, ON M5V 2K1", "url": "http://a.com"}]}]
        result = extract_addresses_from_queries(queries)
        assert len(result) == 0
