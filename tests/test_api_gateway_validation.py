"""Tests for api_gateway validation functions.

The validation functions in api_gateway/main.py are pure functions (no GCP
dependencies), but main.py has heavy module-level imports (Firestore,
Firebase Admin, etc.). Rather than mocking those, we extract the functions
from the source file at test time using exec().
"""

import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Extract validation functions from main.py source without importing the module
# ---------------------------------------------------------------------------
_MAIN_PY = Path(__file__).resolve().parent.parent / "gcp" / "functions" / "api_gateway" / "main.py"
_source = _MAIN_PY.read_text()

# Extract standalone validation functions and constants via exec.
# We only need: validate_email, validate_full_name, validate_city,
# validate_province, VALID_PROVINCES.  These depend only on the `re` module.
_namespace = {"re": re}

# Extract VALID_PROVINCES
exec(
    "\n".join(
        line
        for line in _source.splitlines()
        if line.startswith("VALID_PROVINCES")
    ),
    _namespace,
)

# Extract each validation function (they are self-contained)
for fn_name in ("validate_email", "validate_full_name", "validate_city", "validate_province"):
    # Find function boundaries
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
        fn_source = "\n".join(lines[start:end])
        exec(fn_source, _namespace)

validate_email = _namespace["validate_email"]
validate_full_name = _namespace["validate_full_name"]
validate_city = _namespace["validate_city"]
validate_province = _namespace["validate_province"]
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
        assert msg == ""

    def test_valid_name_three_parts(self):
        valid, _ = validate_full_name("John Michael Smith")
        assert valid is True

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

    def test_too_long(self):
        valid, msg = validate_full_name("A" * 101)
        assert valid is False

    def test_name_with_hyphen(self):
        valid, _ = validate_full_name("Jean-Pierre Tremblay")
        assert valid is True

    def test_name_with_apostrophe(self):
        valid, _ = validate_full_name("Patrick O'Brien")
        assert valid is True


class TestValidateCity:
    """Tests for the validate_city function."""

    def test_valid_city(self):
        valid, msg = validate_city("Toronto")
        assert valid is True
        assert msg == ""

    def test_empty_is_valid(self):
        # City is optional
        valid, msg = validate_city("")
        assert valid is True

    def test_city_with_hyphen(self):
        valid, _ = validate_city("Sault Ste-Marie")
        assert valid is True

    def test_city_with_apostrophe(self):
        valid, _ = validate_city("St John's")
        assert valid is True

    def test_city_with_period(self):
        valid, _ = validate_city("St. John's")
        assert valid is True

    def test_city_with_accents(self):
        valid, _ = validate_city("Montréal")
        assert valid is True

    def test_city_with_numbers_rejected(self):
        valid, msg = validate_city("Toronto123")
        assert valid is False

    def test_too_short(self):
        valid, msg = validate_city("A")
        assert valid is False

    def test_too_long(self):
        valid, msg = validate_city("A" * 101)
        assert valid is False


class TestValidateProvince:
    """Tests for the validate_province function."""

    def test_valid_province_on(self):
        valid, msg = validate_province("ON")
        assert valid is True
        assert msg == ""

    def test_valid_province_bc(self):
        valid, _ = validate_province("BC")
        assert valid is True

    def test_valid_province_qc(self):
        valid, _ = validate_province("QC")
        assert valid is True

    def test_empty_province(self):
        valid, msg = validate_province("")
        assert valid is False
        assert "required" in msg.lower()

    def test_invalid_province(self):
        valid, msg = validate_province("XX")
        assert valid is False

    def test_lowercase_rejected(self):
        valid, msg = validate_province("on")
        assert valid is False

    def test_full_name_rejected(self):
        valid, msg = validate_province("Ontario")
        assert valid is False

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
