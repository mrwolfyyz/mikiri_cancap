"""Tests for gcp/shared/report_utils.py.

Pure function tests run directly. Network-dependent functions
(geocode_address, generate_street_view_url, get_domain_registration_date,
get_gravatar_profile, check_domain_mx_records, load_disposable_email_blocklist)
are tested with mocked HTTP/DNS/file I/O.
"""

import json
import sys
from unittest.mock import MagicMock, patch

import report_utils
from report_utils import (
    check_domain_mx_records,
    generate_google_search_url,
    generate_google_search_url_for_email,
    generate_google_search_url_for_phone,
    generate_phone_variations,
    generate_street_view_url,
    geocode_address,
    get_domain_registration_date,
    get_gravatar_profile,
    is_disposable_email_domain,
    load_disposable_email_blocklist,
    normalize_address,
    slugify,
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


# ===========================================================================
# load_disposable_email_blocklist (file I/O)
# ===========================================================================
class TestLoadDisposableEmailBlocklist:
    """Tests for loading the disposable email blocklist from file."""

    def setup_method(self):
        """Reset the module-level cache before each test."""
        report_utils._disposable_email_blocklist_cache = None

    def test_valid_file(self, tmp_path):
        blocklist_file = tmp_path / "blocklist.conf"
        blocklist_file.write_text("tempmail.com\nthrowaway.email\n# comment\n\n")
        result = load_disposable_email_blocklist(blocklist_file)
        assert "tempmail.com" in result
        assert "throwaway.email" in result
        assert len(result) == 2  # comment and blank line excluded

    def test_empty_file(self, tmp_path):
        blocklist_file = tmp_path / "blocklist.conf"
        blocklist_file.write_text("")
        result = load_disposable_email_blocklist(blocklist_file)
        assert result == set()

    def test_missing_file(self, tmp_path):
        blocklist_file = tmp_path / "nonexistent.conf"
        result = load_disposable_email_blocklist(blocklist_file)
        assert result == set()

    def test_caching(self, tmp_path):
        """Second call returns cached result without re-reading file."""
        blocklist_file = tmp_path / "blocklist.conf"
        blocklist_file.write_text("tempmail.com\n")
        result1 = load_disposable_email_blocklist(blocklist_file)
        # Delete file — should still return cached result
        blocklist_file.unlink()
        result2 = load_disposable_email_blocklist(blocklist_file)
        assert result1 == result2
        assert "tempmail.com" in result2


# ===========================================================================
# generate_street_view_url
# ===========================================================================
class TestGenerateStreetViewUrl:
    """Tests for Street View URL generation."""

    def test_cached_coords_uses_pano_url(self):
        url = generate_street_view_url("123 Main St", geocode=False, cached_coords={"lat": 43.6532, "lon": -79.3832})
        assert "map_action=pano" in url
        assert "43.6532" in url
        assert "-79.3832" in url

    def test_no_coords_no_geocode_returns_search_url(self):
        url = generate_street_view_url("123 Main St, Toronto", geocode=False)
        assert "maps/search" in url
        assert "123" in url

    @patch("report_utils.geocode_address", return_value=(43.65, -79.38))
    def test_geocode_true_with_result(self, mock_geo):
        url = generate_street_view_url("123 Main St, Toronto", geocode=True)
        assert "map_action=pano" in url
        mock_geo.assert_called_once()

    @patch("report_utils.geocode_address", return_value=(None, None))
    def test_geocode_true_no_result_falls_back(self, mock_geo):
        url = generate_street_view_url("123 Main St, Toronto", geocode=True)
        assert "maps/search" in url

    def test_empty_cached_coords_ignored(self):
        url = generate_street_view_url("123 Main St", geocode=False, cached_coords={"lat": None, "lon": None})
        assert "maps/search" in url


# ===========================================================================
# geocode_address (mocked HTTP)
# ===========================================================================
class TestGeocodeAddress:
    """Tests for Nominatim geocoding with mocked HTTP."""

    @patch("time.sleep")  # skip rate-limit delay
    @patch("urllib.request.urlopen")
    def test_successful_geocode(self, mock_urlopen, mock_sleep):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps([{"lat": "43.6532", "lon": "-79.3832"}]).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        lat, lon = geocode_address("123 Main St, Toronto")
        assert lat == 43.6532
        assert lon == -79.3832

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_empty_results(self, mock_urlopen, mock_sleep):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps([]).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        lat, lon = geocode_address("Nonexistent Place XYZ")
        assert lat is None
        assert lon is None

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_http_error(self, mock_urlopen, mock_sleep):
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("Connection refused")

        lat, lon = geocode_address("123 Main St")
        assert lat is None
        assert lon is None


# ===========================================================================
# get_gravatar_profile (mocked HTTP)
# ===========================================================================
class TestGetGravatarProfile:
    """Tests for Gravatar profile lookup with mocked HTTP."""

    @patch("urllib.request.urlopen")
    def test_profile_found(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"entry": [{"id": "123"}]}).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = get_gravatar_profile("test@example.com")
        assert result["success"] is True
        assert result["profile_url"] is not None
        assert result["thumbnail_url"] is not None

    @patch("urllib.request.urlopen")
    def test_profile_not_found_404(self, mock_urlopen):
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError("url", 404, "Not Found", {}, None)

        result = get_gravatar_profile("nobody@example.com")
        assert result["success"] is False
        assert "404" in result["error"]

    def test_invalid_email(self):
        result = get_gravatar_profile("")
        assert result["success"] is False
        assert "Invalid" in result["error"]

    @patch("urllib.request.urlopen")
    def test_network_error(self, mock_urlopen):
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("timeout")

        result = get_gravatar_profile("test@example.com")
        assert result["success"] is False

    @patch("urllib.request.urlopen")
    def test_response_no_entry_key(self, mock_urlopen):
        """Lines 305-306: API returns data but no 'entry' key."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"other_key": "value"}).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = get_gravatar_profile("noentry@example.com")
        assert result["success"] is False
        assert "No profile found" in result["error"]

    @patch("urllib.request.urlopen")
    def test_response_empty_entry_list(self, mock_urlopen):
        """Lines 305-306: API returns data with empty entry list."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"entry": []}).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = get_gravatar_profile("empty@example.com")
        assert result["success"] is False
        assert "No profile found" in result["error"]

    @patch("urllib.request.urlopen")
    def test_http_error_non_404(self, mock_urlopen):
        """Lines 313-314: HTTPError with non-404 status code (e.g. 500)."""
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError("url", 500, "Internal Server Error", {}, None)

        result = get_gravatar_profile("servererror@example.com")
        assert result["success"] is False
        assert "HTTP 500" in result["error"]

    @patch("urllib.request.urlopen")
    def test_generic_exception(self, mock_urlopen):
        """Lines 318-320: Generic exception handler."""
        mock_urlopen.side_effect = RuntimeError("Unexpected error")

        result = get_gravatar_profile("error@example.com")
        assert result["success"] is False
        assert "Unexpected error" in result["error"]


# ===========================================================================
# get_domain_registration_date (mocked whois)
# ===========================================================================
class TestGetDomainRegistrationDate:
    """Tests for WHOIS domain registration lookup."""

    def test_successful_lookup(self):
        from datetime import datetime

        mock_whois_result = MagicMock()
        mock_whois_result.creation_date = datetime(2010, 1, 15)
        mock_whois_result.text = ""

        mock_whois_module = MagicMock()
        mock_whois_module.whois.return_value = mock_whois_result

        with patch.dict(sys.modules, {"whois": mock_whois_module}):
            result = get_domain_registration_date("example.com")

        assert result["success"] is True
        assert result["registration_date"] == "2010-01-15"

    def test_list_of_dates_takes_first(self):
        from datetime import datetime

        mock_whois_result = MagicMock()
        mock_whois_result.creation_date = [datetime(2010, 1, 15), datetime(2015, 6, 1)]
        mock_whois_result.text = ""

        mock_whois_module = MagicMock()
        mock_whois_module.whois.return_value = mock_whois_result

        with patch.dict(sys.modules, {"whois": mock_whois_module}):
            result = get_domain_registration_date("example.com")

        assert result["success"] is True
        assert result["registration_date"] == "2010-01-15"

    def test_no_registration_date(self):
        mock_whois_result = MagicMock()
        mock_whois_result.creation_date = None
        mock_whois_result.created = None
        mock_whois_result.registered = None
        mock_whois_result.registration_date = None
        mock_whois_result.domain_date_created = None
        mock_whois_result.text = ""

        mock_whois_module = MagicMock()
        mock_whois_module.whois.return_value = mock_whois_result

        with patch.dict(sys.modules, {"whois": mock_whois_module}):
            result = get_domain_registration_date("unknown.com")

        assert result["success"] is False

    def test_whois_exception(self):
        mock_whois_module = MagicMock()
        mock_whois_module.whois.side_effect = Exception("WHOIS timeout")

        with patch.dict(sys.modules, {"whois": mock_whois_module}):
            result = get_domain_registration_date("broken.com")

        assert result["success"] is False
        assert "timeout" in result["error"].lower()

    def test_string_date_parsed(self):
        mock_whois_result = MagicMock()
        mock_whois_result.creation_date = "2020-03-15"
        mock_whois_result.text = ""

        mock_whois_module = MagicMock()
        mock_whois_module.whois.return_value = mock_whois_result

        with patch.dict(sys.modules, {"whois": mock_whois_module}):
            result = get_domain_registration_date("stringdate.com")

        assert result["success"] is True
        assert result["registration_date"] == "2020-03-15"

    def test_dict_fallback_field(self):
        """Lines 180-181: When getattr returns None/falsy for all standard fields,
        fallback to __dict__ lookup finds the date.

        This simulates whois libraries where a descriptor/property returns None
        for the attribute but the raw __dict__ has the parsed value.
        """
        from datetime import datetime

        # Use a class with properties that return None for field names,
        # while __dict__ contains the actual truthy value.
        class FakeWhoisResult:
            # Properties shadow the instance __dict__ — getattr sees the property (None),
            # but __dict__[key] sees the instance value.
            creation_date = property(lambda self: None)
            created = property(lambda self: None)
            registered = property(lambda self: None)
            registration_date = property(lambda self: None)
            domain_date_created = property(lambda self: None)

            def __init__(self):
                self.text = ""

        mock_whois_result = FakeWhoisResult()
        # Insert directly into __dict__ without going through the property setter
        mock_whois_result.__dict__["creation_date"] = datetime(2018, 5, 20)

        mock_whois_module = MagicMock()
        mock_whois_module.whois.return_value = mock_whois_result

        with patch.dict(sys.modules, {"whois": mock_whois_module}):
            result = get_domain_registration_date("dict-fallback.com")

        assert result["success"] is True
        assert result["registration_date"] == "2018-05-20"

    def test_raw_text_regex_fallback_iso_date(self):
        """Lines 185-207: Parse creation date from raw WHOIS text (YYYY-MM-DD)."""
        mock_whois_result = MagicMock()
        # Return None for all standard getattr field lookups
        mock_whois_result.creation_date = None
        mock_whois_result.created = None
        mock_whois_result.registered = None
        mock_whois_result.registration_date = None
        mock_whois_result.domain_date_created = None
        # Ensure __dict__ fallback also finds nothing (MagicMock __dict__ won't have
        # the field names as truthy since we set them to None above, so this is fine)
        mock_whois_result.text = "Domain: example.ai\nCreation Date: 2019-07-04\nExpiry: 2025-07-04"

        mock_whois_module = MagicMock()
        mock_whois_module.whois.return_value = mock_whois_result

        with patch.dict(sys.modules, {"whois": mock_whois_module}):
            result = get_domain_registration_date("example.ai")

        assert result["success"] is True
        assert result["registration_date"] == "2019-07-04"

    def test_raw_text_regex_fallback_slash_date(self):
        """Lines 185-207: Parse creation date from raw WHOIS text (MM/DD/YYYY)."""
        mock_whois_result = MagicMock()
        mock_whois_result.creation_date = None
        mock_whois_result.created = None
        mock_whois_result.registered = None
        mock_whois_result.registration_date = None
        mock_whois_result.domain_date_created = None
        mock_whois_result.text = "Created: 03/15/2017\nUpdated: 01/10/2023"

        mock_whois_module = MagicMock()
        mock_whois_module.whois.return_value = mock_whois_result

        with patch.dict(sys.modules, {"whois": mock_whois_module}):
            result = get_domain_registration_date("slashdate.com")

        assert result["success"] is True
        assert result["registration_date"] == "2017-03-15"

    def test_string_date_valueerror_then_dateutil_fallback(self):
        """Lines 221-229: String date that matches no strptime format, falls back to dateutil."""
        from datetime import datetime

        mock_whois_result = MagicMock()
        # Non-standard date string that strptime patterns won't match but dateutil will
        mock_whois_result.creation_date = "January 5, 2021"
        mock_whois_result.text = ""

        mock_whois_module = MagicMock()
        mock_whois_module.whois.return_value = mock_whois_result

        # Mock dateutil.parser.parse to return a datetime
        mock_dateutil_parser = MagicMock()
        mock_dateutil_parser.parse.return_value = datetime(2021, 1, 5)
        mock_dateutil = MagicMock()
        mock_dateutil.parser = mock_dateutil_parser

        with patch.dict(
            sys.modules,
            {"whois": mock_whois_module, "dateutil": mock_dateutil, "dateutil.parser": mock_dateutil_parser},
        ):
            result = get_domain_registration_date("dateutil-domain.com")

        assert result["success"] is True
        assert result["registration_date"] == "2021-01-05"

    def test_string_date_all_parsers_fail(self):
        """Lines 221-229: String date that no parser can handle."""
        mock_whois_result = MagicMock()
        mock_whois_result.creation_date = "not-a-date-at-all"
        mock_whois_result.text = ""

        mock_whois_module = MagicMock()
        mock_whois_module.whois.return_value = mock_whois_result

        with patch.dict(sys.modules, {"whois": mock_whois_module}):
            result = get_domain_registration_date("unparseable.com")

        # creation_date remains a string (not datetime), so it falls through
        assert result["success"] is False
        assert "No registration date" in result["error"]

    def test_import_error_whois_not_installed(self):
        """Lines 241-242: ImportError when python-whois is not available."""
        mock_whois_module = MagicMock()
        mock_whois_module.whois.side_effect = ImportError("No module named 'whois'")

        # We need the import inside get_domain_registration_date to raise ImportError.
        # The function does `import whois` inside the try block.
        with patch.dict(sys.modules, {"whois": None}):
            result = get_domain_registration_date("noimport.com")

        assert result["success"] is False
        assert "not installed" in result["error"]

    def test_exception_with_date_in_error_message(self):
        """Lines 260-267: Extract registration date from exception error message."""
        mock_whois_module = MagicMock()
        mock_whois_module.whois.side_effect = Exception(
            "Failed to parse whois data. creation date: 2016-08-22T10:30:00Z"
        )

        with patch.dict(sys.modules, {"whois": mock_whois_module}):
            result = get_domain_registration_date("error-date.ai")

        assert result["success"] is True
        assert result["registration_date"] == "2016-08-22"

    def test_exception_with_no_date_in_error_message(self):
        """Lines 260-267: Exception with error message that has no parseable date."""
        mock_whois_module = MagicMock()
        mock_whois_module.whois.side_effect = Exception("Connection timed out")

        with patch.dict(sys.modules, {"whois": mock_whois_module}):
            result = get_domain_registration_date("timeout.com")

        assert result["success"] is False
        assert "Connection timed out" in result["error"]

    def test_exception_error_message_invalid_date_continues(self):
        """Lines 266-267: Error message regex matches but date is invalid, continues to next pattern."""
        mock_whois_module = MagicMock()
        # The regex matches "creation date: 2016-13-45" but strptime fails (month 13 invalid)
        mock_whois_module.whois.side_effect = Exception("Error: creation date: 2016-13-45 some other text")

        with patch.dict(sys.modules, {"whois": mock_whois_module}):
            result = get_domain_registration_date("bad-error-date.com")

        assert result["success"] is False
        assert "2016-13-45" in result["error"]


# ===========================================================================
# check_domain_mx_records (mocked DNS)
# ===========================================================================
class TestCheckDomainMxRecords:
    """Tests for MX record analysis with mocked DNS."""

    def _make_mx_record(self, exchange, preference=10):
        record = MagicMock()
        record.exchange.to_text.return_value = exchange
        record.preference = preference
        return record

    def test_google_workspace_detected(self):
        mock_dns = MagicMock()
        mock_dns.resolver.resolve.return_value = [
            self._make_mx_record("aspmx.l.google.com.", 1),
        ]

        with patch.dict(sys.modules, {"dns": mock_dns, "dns.resolver": mock_dns.resolver}):
            result = check_domain_mx_records("google-user.com")

        assert result["success"] is True
        assert result["risk_level"] == "LOW"
        assert "Google" in result["provider_detected"]

    def test_godaddy_default_detected(self):
        mock_dns = MagicMock()
        mock_dns.resolver.resolve.return_value = [
            self._make_mx_record("mailstore1.secureserver.net.", 10),
        ]

        with patch.dict(sys.modules, {"dns": mock_dns, "dns.resolver": mock_dns.resolver}):
            result = check_domain_mx_records("parked-domain.com")

        assert result["success"] is True
        assert result["risk_level"] == "HIGH"
        assert "GoDaddy" in result["provider_detected"]

    def test_self_hosted_detected(self):
        mock_dns = MagicMock()
        mock_dns.resolver.resolve.return_value = [
            self._make_mx_record("mail.mycorp.com.", 10),
        ]

        with patch.dict(sys.modules, {"dns": mock_dns, "dns.resolver": mock_dns.resolver}):
            result = check_domain_mx_records("mycorp.com")

        assert result["success"] is True
        assert result["risk_level"] == "MEDIUM"

    def test_nxdomain(self):
        mock_dns = MagicMock()
        nxdomain_exc = type("NXDOMAIN", (Exception,), {})
        mock_dns.resolver.NXDOMAIN = nxdomain_exc
        mock_dns.resolver.resolve.side_effect = nxdomain_exc()
        # Also need NoAnswer for the except chain
        mock_dns.resolver.NoAnswer = type("NoAnswer", (Exception,), {})

        with patch.dict(sys.modules, {"dns": mock_dns, "dns.resolver": mock_dns.resolver}):
            result = check_domain_mx_records("doesnotexist.xyz")

        assert result["success"] is False
        assert result["risk_level"] == "CRITICAL"
        assert "Not Found" in result["status"]

    def test_no_mx_records(self):
        mock_dns = MagicMock()
        no_answer_exc = type("NoAnswer", (Exception,), {})
        mock_dns.resolver.NoAnswer = no_answer_exc
        mock_dns.resolver.NXDOMAIN = type("NXDOMAIN", (Exception,), {})
        mock_dns.resolver.resolve.side_effect = no_answer_exc()

        with patch.dict(sys.modules, {"dns": mock_dns, "dns.resolver": mock_dns.resolver}):
            result = check_domain_mx_records("no-email.com")

        assert result["risk_level"] == "CRITICAL"
        assert "No Email" in result["status"]

    def test_microsoft_365_detected(self):
        mock_dns = MagicMock()
        mock_dns.resolver.resolve.return_value = [
            self._make_mx_record("corp-com.mail.protection.outlook.com.", 10),
        ]

        with patch.dict(sys.modules, {"dns": mock_dns, "dns.resolver": mock_dns.resolver}):
            result = check_domain_mx_records("corp.com")

        assert result["success"] is True
        assert result["risk_level"] == "LOW"
        assert "Microsoft" in result["provider_detected"]

    def test_standard_trust_zoho_detected(self):
        """Lines 419-428: STANDARD_TRUST_PROVIDERS branch (Zoho)."""
        mock_dns = MagicMock()
        mock_dns.resolver.resolve.return_value = [
            self._make_mx_record("mx.zoho.com.", 10),
        ]

        with patch.dict(sys.modules, {"dns": mock_dns, "dns.resolver": mock_dns.resolver}):
            result = check_domain_mx_records("small-biz.com")

        assert result["success"] is True
        assert result["risk_level"] == "LOW/MEDIUM"
        assert "Zoho" in result["provider_detected"]

    def test_standard_trust_fastmail_detected(self):
        """Lines 419-428: STANDARD_TRUST_PROVIDERS branch (Fastmail)."""
        mock_dns = MagicMock()
        mock_dns.resolver.resolve.return_value = [
            self._make_mx_record("in1-smtp.messagingengine.fastmail.com.", 10),
        ]

        with patch.dict(sys.modules, {"dns": mock_dns, "dns.resolver": mock_dns.resolver}):
            result = check_domain_mx_records("fastmail-user.com")

        assert result["success"] is True
        assert result["risk_level"] == "LOW/MEDIUM"
        assert "Fastmail" in result["provider_detected"]

    def test_unrecognized_provider(self):
        """Lines 455-464: MX record that matches no known provider and is not self-hosted."""
        mock_dns = MagicMock()
        mock_dns.resolver.resolve.return_value = [
            self._make_mx_record("mail.obscure-email-host.net.", 10),
        ]

        with patch.dict(sys.modules, {"dns": mock_dns, "dns.resolver": mock_dns.resolver}):
            result = check_domain_mx_records("custom-domain.com")

        assert result["success"] is True
        assert result["risk_level"] == "MEDIUM"
        assert result["status"] == "Unknown Email Provider"
        assert "Unrecognized provider" in result["provider_detected"]

    def test_empty_mx_records_after_resolve(self):
        """Lines 397-400: dns.resolver.resolve returns records but none append (edge case)."""
        mock_dns = MagicMock()
        # Return an empty iterable (no records to iterate)
        mock_dns.resolver.resolve.return_value = []

        with patch.dict(sys.modules, {"dns": mock_dns, "dns.resolver": mock_dns.resolver}):
            result = check_domain_mx_records("empty-mx.com")

        assert result["success"] is False
        assert result["risk_level"] == "CRITICAL"
        assert result["status"] == "No MX Records Found"

    def test_import_error_dnspython_not_installed(self):
        """Lines 467-469: ImportError when dnspython is not available."""
        mock_dns = MagicMock()
        mock_dns.resolver.resolve.side_effect = ImportError("No module named 'dns'")
        # Set NoAnswer and NXDOMAIN so the except chain doesn't match them
        mock_dns.resolver.NoAnswer = type("NoAnswer", (Exception,), {})
        mock_dns.resolver.NXDOMAIN = type("NXDOMAIN", (Exception,), {})

        with patch.dict(sys.modules, {"dns": mock_dns, "dns.resolver": mock_dns.resolver}):
            result = check_domain_mx_records("noimport.com")

        assert result["success"] is False
        assert "not installed" in result["error"]

    def test_generic_exception(self):
        """Lines 478-481: Generic exception handler in MX lookup."""
        mock_dns = MagicMock()
        mock_dns.resolver.NoAnswer = type("NoAnswer", (Exception,), {})
        mock_dns.resolver.NXDOMAIN = type("NXDOMAIN", (Exception,), {})
        mock_dns.resolver.resolve.side_effect = RuntimeError("DNS timeout")

        with patch.dict(sys.modules, {"dns": mock_dns, "dns.resolver": mock_dns.resolver}):
            result = check_domain_mx_records("timeout.com")

        assert result["success"] is False
        assert "DNS timeout" in result["error"]
