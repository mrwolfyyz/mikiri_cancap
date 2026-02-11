"""Tests for gcp/shared/report_utils.py — pure functions only.

Functions that make network calls (geocode_address, generate_street_view_url,
get_domain_registration_date, get_gravatar_profile, check_domain_mx_records)
are excluded because they require external services. Those would be covered
by integration tests.
"""

from report_utils import (
    slugify,
    normalize_address,
    generate_phone_variations,
    generate_google_search_url,
    generate_google_search_url_for_email,
    generate_google_search_url_for_phone,
    is_disposable_email_domain,
)


class TestSlugify:
    """Tests for the slugify function."""

    def test_simple_string(self):
        assert slugify("Hello World") == "hello_world"

    def test_email(self):
        assert slugify("user@example.com") == "user_example_com"

    def test_url_path(self):
        assert slugify("path/to/file") == "path_to_file"

    def test_dots_replaced(self):
        assert slugify("first.last") == "first_last"

    def test_special_chars_stripped(self):
        assert slugify("hello!@#world") == "hello_world"

    def test_multiple_underscores_collapsed(self):
        assert slugify("a   b   c") == "a_b_c"

    def test_leading_trailing_underscores_stripped(self):
        assert slugify("  hello  ") == "hello"

    def test_none_returns_unknown(self):
        assert slugify(None) == "unknown"

    def test_empty_string_returns_unknown(self):
        assert slugify("") == "unknown"

    def test_only_special_chars_returns_unknown(self):
        assert slugify("!@#$%") == "unknown"


class TestNormalizeAddress:
    """Tests for the normalize_address function."""

    def test_lowercase(self):
        assert "toronto" in normalize_address("123 Main St, TORONTO, ON")

    def test_removes_commas(self):
        result = normalize_address("123 Main St, Toronto, ON")
        assert "," not in result

    def test_collapses_whitespace(self):
        result = normalize_address("123  Main   St   Toronto")
        assert "  " not in result

    def test_abbreviation_st_to_street(self):
        result = normalize_address("123 Main St")
        assert "street" in result

    def test_abbreviation_ave_to_avenue(self):
        result = normalize_address("456 Queen Ave")
        assert "avenue" in result

    def test_abbreviation_rd_to_road(self):
        result = normalize_address("789 King Rd")
        assert "road" in result

    def test_abbreviation_dr_to_drive(self):
        result = normalize_address("10 Park Dr")
        assert "drive" in result

    def test_abbreviation_blvd_to_boulevard(self):
        result = normalize_address("100 Sunset Blvd")
        assert "boulevard" in result

    def test_multiple_abbreviations(self):
        result = normalize_address("123 Oak St, Suite 100")
        assert "street" in result

    def test_strips_whitespace(self):
        result = normalize_address("  123 Main St  ")
        assert result == normalize_address("123 Main St")


class TestGeneratePhoneVariations:
    """Tests for the generate_phone_variations function."""

    def test_ten_digit_number(self):
        variations = generate_phone_variations({"number_digits": "4165551234"})
        assert len(variations) == 4
        assert "416-555-1234" in variations
        assert "(416) 555-1234" in variations
        assert "416.555.1234" in variations
        assert "+1 416 555 1234" in variations

    def test_eleven_digit_with_country_code(self):
        variations = generate_phone_variations({"number_digits": "14165551234"})
        assert len(variations) == 4
        assert "416-555-1234" in variations

    def test_short_number_returns_empty(self):
        assert generate_phone_variations({"number_digits": "12345"}) == []

    def test_long_number_returns_empty(self):
        assert generate_phone_variations({"number_digits": "123456789012"}) == []

    def test_empty_digits_returns_empty(self):
        assert generate_phone_variations({"number_digits": ""}) == []

    def test_missing_key_returns_empty(self):
        assert generate_phone_variations({}) == []


class TestGenerateGoogleSearchUrl:
    """Tests for URL generation functions."""

    def test_address_search_url(self):
        url = generate_google_search_url({"address_raw": "123 Main St, Toronto"})
        assert "google.com/search" in url
        assert "123" in url
        assert "Toronto" in url

    def test_email_search_url(self):
        url = generate_google_search_url_for_email("test@example.com")
        assert "google.com/search" in url
        assert "test" in url
        assert "example.com" in url

    def test_phone_search_url_with_variations(self):
        url = generate_google_search_url_for_phone({"number_digits": "4165551234"})
        assert "google.com/search" in url
        # Should contain quoted phone variations connected by OR
        assert "%7C" in url or "|" in url  # URL-encoded pipe

    def test_phone_search_url_no_digits_uses_raw(self):
        url = generate_google_search_url_for_phone({"number_raw": "416-555-1234"})
        assert "google.com/search" in url


class TestIsDisposableEmailDomain:
    """Tests for the is_disposable_email_domain function."""

    def test_returns_true_for_blocklisted_domain(self):
        blocklist = {"tempmail.com", "throwaway.email"}
        assert is_disposable_email_domain("user@tempmail.com", blocklist) is True

    def test_returns_false_for_clean_domain(self):
        blocklist = {"tempmail.com"}
        assert is_disposable_email_domain("user@gmail.com", blocklist) is False

    def test_returns_false_for_empty_email(self):
        blocklist = {"tempmail.com"}
        assert is_disposable_email_domain("", blocklist) is False

    def test_returns_false_for_invalid_email(self):
        blocklist = {"tempmail.com"}
        assert is_disposable_email_domain("not-an-email", blocklist) is False

    def test_case_insensitive(self):
        blocklist = {"tempmail.com"}
        assert is_disposable_email_domain("USER@TEMPMAIL.COM", blocklist) is True
