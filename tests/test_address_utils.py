"""Tests for gcp/shared/address_utils.py"""

from address_utils import clean_address_for_geocoding


class TestCleanAddressForGeocoding:
    """Tests for the clean_address_for_geocoding function."""

    def test_returns_address_unchanged_when_clean(self):
        assert clean_address_for_geocoding("123 Main St, Toronto, ON") == "123 Main St, Toronto, ON"

    def test_strips_copyright_prefix(self):
        raw = "© 2024 Acme Corp. All Rights Reserved. 456 Queen St, Vancouver, BC"
        result = clean_address_for_geocoding(raw)
        assert "©" not in result
        assert "Reserved" not in result
        assert "456 Queen St" in result

    def test_strips_year_reserved_prefix(self):
        raw = "2023 Acme Inc. All Rights Reserved. 789 King Rd, Calgary, AB"
        result = clean_address_for_geocoding(raw)
        assert "Reserved" not in result
        assert "789 King Rd" in result

    def test_strips_head_office_prefix(self):
        raw = "HEAD OFFICE. 100 Bay St, Toronto, ON"
        result = clean_address_for_geocoding(raw)
        assert "HEAD OFFICE" not in result
        assert "100 Bay St" in result

    def test_strips_office_prefix(self):
        raw = "OFFICE. 200 Front St W, Toronto, ON"
        result = clean_address_for_geocoding(raw)
        assert "OFFICE" not in result
        assert "200 Front St W" in result

    def test_strips_contact_prefix(self):
        raw = "Contact: 50 Wellington St, Ottawa, ON"
        result = clean_address_for_geocoding(raw)
        assert "Contact:" not in result
        assert "50 Wellington St" in result

    def test_strips_whitespace(self):
        assert clean_address_for_geocoding("  123 Main St  ") == "123 Main St"

    def test_empty_string(self):
        assert clean_address_for_geocoding("") == ""

    def test_case_insensitive_pattern_removal(self):
        raw = "head office. 10 Dundas St, Toronto, ON"
        result = clean_address_for_geocoding(raw)
        assert "head office" not in result.lower()
        assert "Dundas St" in result
