"""Tests for api_gateway validation functions.

The validation functions in api_gateway/main.py are pure functions (no GCP
dependencies), but main.py has heavy module-level imports (Firestore,
Firebase Admin, etc.). Rather than mocking those, we extract the functions
from the source file at test time using exec(), with llm_input_validators
injected into the namespace (same symbols main.py imports).
"""

import re
from pathlib import Path

from llm_input_validators import (
    MAX_CITY_LEN,
    MAX_FULL_NAME_LEN,
    PROVINCE_NAMES,
    normalize_and_validate_allowlist_text,
    normalize_province_for_query,
)

# ---------------------------------------------------------------------------
# Extract validation functions from main.py source without importing the module
# ---------------------------------------------------------------------------
_MAIN_PY = Path(__file__).resolve().parent.parent / "gcp" / "functions" / "api_gateway" / "main.py"
_source = _MAIN_PY.read_text()

_namespace = {
    "re": re,
    "MAX_FULL_NAME_LEN": MAX_FULL_NAME_LEN,
    "MAX_CITY_LEN": MAX_CITY_LEN,
    "PROVINCE_NAMES": PROVINCE_NAMES,
    "normalize_and_validate_allowlist_text": normalize_and_validate_allowlist_text,
    "normalize_province_for_query": normalize_province_for_query,
}

# VALID_PROVINCES = list(PROVINCE_NAMES.keys())
exec(
    "\n".join(line for line in _source.splitlines() if line.startswith("VALID_PROVINCES")),
    _namespace,
)

# Order matters: _province_validation_message before validate_province
for fn_name in (
    "validate_email",
    "validate_full_name",
    "validate_city",
    "_province_validation_message",
    "validate_province",
    "validate_cars_reference_number",
):
    lines = _source.splitlines()
    start = None
    end = None
    for i, line in enumerate(lines):
        if line.startswith(f"def {fn_name}("):
            start = i
        elif start is not None and i > start and (line and not line[0].isspace() and not line.startswith("#")):
            end = i
            break
    if start is not None:
        fn_source = "\n".join(lines[start : end if end is not None else len(lines)])
        exec(fn_source, _namespace)

validate_email = _namespace["validate_email"]
validate_full_name = _namespace["validate_full_name"]
validate_city = _namespace["validate_city"]
validate_province = _namespace["validate_province"]
validate_cars_reference_number = _namespace["validate_cars_reference_number"]
VALID_PROVINCES = _namespace["VALID_PROVINCES"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidateEmail:
    """Tests for the validate_email function."""

    def test_valid_email(self):
        valid, msg = validate_email("user@example.com")
        assert valid is True
        assert msg == ""

    def test_valid_email_with_dots(self):
        valid, _ = validate_email("first.last@company.ca")
        assert valid is True

    def test_valid_email_with_plus(self):
        valid, _ = validate_email("user+tag@gmail.com")
        assert valid is True

    def test_empty_email(self):
        valid, msg = validate_email("")
        assert valid is False
        assert "required" in msg.lower()

    def test_too_short(self):
        valid, msg = validate_email("a@b")
        assert valid is False

    def test_too_long(self):
        valid, msg = validate_email("a" * 250 + "@b.com")
        assert valid is False

    def test_no_at_sign(self):
        valid, msg = validate_email("userexample.com")
        assert valid is False

    def test_no_domain(self):
        valid, msg = validate_email("user@")
        assert valid is False

    def test_no_tld(self):
        valid, msg = validate_email("user@example")
        assert valid is False

    def test_spaces_rejected(self):
        valid, msg = validate_email("user @example.com")
        assert valid is False


class TestValidateFullName:
    """Tests for the validate_full_name function."""

    def test_valid_name(self):
        valid, msg = validate_full_name("John Smith")
        assert valid is True
        assert msg == "John Smith"

    def test_valid_name_three_parts(self):
        valid, norm = validate_full_name("John Michael Smith")
        assert valid is True
        assert norm == "John Michael Smith"

    def test_empty_name(self):
        valid, msg = validate_full_name("")
        assert valid is False
        assert "required" in msg.lower()

    def test_single_name_no_space(self):
        valid, msg = validate_full_name("John")
        assert valid is False
        assert "first and last" in msg.lower()

    def test_too_short(self):
        valid, msg = validate_full_name("A")
        assert valid is False
        assert "2-200" in msg or "character" in msg.lower()

    def test_too_long(self):
        # Two words, total length > MAX_FULL_NAME_LEN after normalization
        long_two_word = "John " + "x" * (MAX_FULL_NAME_LEN - 5) + " Smith"
        assert len(long_two_word) > MAX_FULL_NAME_LEN
        valid, msg = validate_full_name(long_two_word)
        assert valid is False

    def test_name_with_hyphen(self):
        valid, norm = validate_full_name("Jean-Pierre Tremblay")
        assert valid is True
        assert norm == "Jean-Pierre Tremblay"

    def test_name_with_apostrophe(self):
        valid, norm = validate_full_name("Patrick O'Brien")
        assert valid is True
        assert norm == "Patrick O'Brien"

    def test_injection_style_rejected(self):
        valid, msg = validate_full_name('Jane"; DROP TABLE users--')
        assert valid is False

    def test_digits_rejected(self):
        valid, msg = validate_full_name("Jane Smith 2")
        assert valid is False

    def test_brackets_backticks_quotes_rejected(self):
        for bad in ("Jane [Smith]", "Jane `Smith`", 'Jane "Smith"'):
            valid, msg = validate_full_name(bad + " Doe")
            assert valid is False, bad

    def test_unicode_name_accepted(self):
        valid, norm = validate_full_name("François 李明 Smith")
        assert valid is True
        assert "François" in norm and "Smith" in norm

    def test_nfc_whitespace_collapsed(self):
        valid, norm = validate_full_name("  John   Smith  ")
        assert valid is True
        assert norm == "John Smith"


class TestValidateCity:
    """Tests for the validate_city function."""

    def test_valid_city(self):
        valid, msg = validate_city("Toronto")
        assert valid is True
        assert msg == "Toronto"

    def test_empty_is_valid(self):
        # City is optional
        valid, msg = validate_city("")
        assert valid is True
        assert msg == ""

    def test_city_with_hyphen(self):
        valid, norm = validate_city("Sault Ste-Marie")
        assert valid is True
        assert norm == "Sault Ste-Marie"

    def test_city_with_apostrophe(self):
        valid, norm = validate_city("St John's")
        assert valid is True
        assert norm == "St John's"

    def test_city_with_period(self):
        valid, norm = validate_city("St. John's")
        assert valid is True
        assert norm == "St. John's"

    def test_city_with_accents(self):
        valid, norm = validate_city("Montréal")
        assert valid is True
        assert norm == "Montréal"

    def test_city_with_numbers_rejected(self):
        valid, msg = validate_city("Toronto123")
        assert valid is False

    def test_too_short(self):
        valid, msg = validate_city("A")
        assert valid is False

    def test_too_long(self):
        valid, msg = validate_city("A" * (MAX_CITY_LEN + 1))
        assert valid is False

    def test_injection_rejected(self):
        valid, msg = validate_city("Toronto; DROP--")
        assert valid is False


class TestValidateProvince:
    """Tests for the validate_province function."""

    def test_valid_province_on(self):
        valid, msg = validate_province("ON")
        assert valid is True
        assert msg == "ON"

    def test_valid_province_bc(self):
        valid, norm = validate_province("BC")
        assert valid is True
        assert norm == "BC"

    def test_valid_province_qc(self):
        valid, norm = validate_province("QC")
        assert valid is True
        assert norm == "QC"

    def test_empty_province(self):
        valid, msg = validate_province("")
        assert valid is False
        assert "required" in msg.lower()

    def test_invalid_province(self):
        valid, msg = validate_province("XX")
        assert valid is False

    def test_lowercase_code_normalized_to_upper(self):
        """Aligned with query_constructor: two-letter alpha codes are uppercased."""
        valid, norm = validate_province("on")
        assert valid is True
        assert norm == "ON"

    def test_free_text_province_ontario_accepted(self):
        """Full province name allowed when it passes shared allow-list (matches query_constructor)."""
        valid, norm = validate_province("Ontario")
        assert valid is True
        assert norm == "Ontario"

    def test_all_valid_provinces(self):
        for prov in VALID_PROVINCES:
            valid, _ = validate_province(prov)
            assert valid is True, f"Province {prov} should be valid"


class TestValidProvinces:
    """Tests for the VALID_PROVINCES constant."""

    def test_contains_all_provinces(self):
        expected = {"ON", "BC", "AB", "QC", "MB", "SK", "NS", "NB", "NL", "PE"}
        assert expected.issubset(set(VALID_PROVINCES))

    def test_contains_territories(self):
        expected = {"NT", "YT", "NU"}
        assert expected.issubset(set(VALID_PROVINCES))

    def test_count(self):
        # 10 provinces + 3 territories = 13
        assert len(VALID_PROVINCES) == 13


class TestValidateCarsReferenceNumber:
    """Tests for the CARS reference number validator."""

    def test_valid_reference_number(self):
        valid, norm = validate_cars_reference_number("ABCDE123")
        assert valid is True
        assert norm == "ABCDE123"

    def test_lowercase_normalized_to_uppercase(self):
        valid, norm = validate_cars_reference_number("abcde123")
        assert valid is True
        assert norm == "ABCDE123"

    def test_empty_reference_number(self):
        valid, msg = validate_cars_reference_number("")
        assert valid is False
        assert "required" in msg.lower()

    def test_too_few_letters(self):
        valid, msg = validate_cars_reference_number("ABCD123")
        assert valid is False
        assert "5 letters" in msg

    def test_non_alpha_prefix_rejected(self):
        valid, msg = validate_cars_reference_number("AB1DE123")
        assert valid is False
        assert "5 letters" in msg

    def test_non_numeric_suffix_rejected(self):
        valid, msg = validate_cars_reference_number("ABCDE12X")
        assert valid is False
        assert "followed by numbers" in msg
