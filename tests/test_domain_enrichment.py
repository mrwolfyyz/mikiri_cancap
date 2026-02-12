"""Tests for the domain_enrichment Cloud Function (gcp/functions/domain_enrichment/main.py).

Covers:
- _is_transient_error (pattern matching for retry)
- get_domain_registration_date (WHOIS lookup with multiple fallbacks)
- check_domain_mx_records (MX record classification)
- _retry_lookup (result-based retry logic)
- enrich_single_domain (parallel WHOIS + MX)
- main HTTP handler (validation, domain extraction, parallel enrichment)
"""

import sys
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

# ---------------------------------------------------------------------------
# Mock heavy dependencies BEFORE loading the module
# ---------------------------------------------------------------------------
_mock_ff = MagicMock()
_mock_ff.http = lambda f: f
sys.modules.setdefault("functions_framework", _mock_ff)

# Mock dns.resolver — need real exception subclasses for except clauses
class _NoAnswer(Exception):
    pass

class _NXDOMAIN(Exception):
    pass

_mock_dns = MagicMock()
_mock_dns_resolver = MagicMock()
_mock_dns_resolver.NoAnswer = _NoAnswer
_mock_dns_resolver.NXDOMAIN = _NXDOMAIN
_mock_dns.resolver = _mock_dns_resolver
sys.modules.setdefault("dns", _mock_dns)
sys.modules.setdefault("dns.resolver", _mock_dns_resolver)

# Mock whois
_mock_whois = MagicMock()
sys.modules.setdefault("whois", _mock_whois)

# Mock dateutil
_mock_dateutil = MagicMock()
_mock_dateutil_parser = MagicMock()
sys.modules.setdefault("dateutil", _mock_dateutil)
sys.modules.setdefault("dateutil.parser", _mock_dateutil_parser)

# ---------------------------------------------------------------------------
# Load domain_enrichment/main.py
# ---------------------------------------------------------------------------
from conftest import load_function_module

de_main = load_function_module("domain_enrichment", "domain_enrichment_main")

_is_transient_error = de_main._is_transient_error
get_domain_registration_date = de_main.get_domain_registration_date
check_domain_mx_records = de_main.check_domain_mx_records
_retry_lookup = de_main._retry_lookup
enrich_single_domain = de_main.enrich_single_domain
main_handler = de_main.main


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


def _mock_whois_result(creation_date=None, text=None):
    """Build a mock whois result object using SimpleNamespace to avoid MagicMock issues."""
    return SimpleNamespace(
        creation_date=creation_date,
        created=None,
        registered=None,
        registration_date=None,
        domain_date_created=None,
        text=text or "",
    )


def _mock_mx_record(preference, exchange):
    """Build a mock MX record."""
    record = MagicMock()
    record.preference = preference
    record.exchange.to_text.return_value = exchange
    return record


# ===========================================================================
# _is_transient_error
# ===========================================================================
class TestIsTransientError:
    def test_timeout(self):
        assert _is_transient_error("Connection timeout occurred") is True

    def test_rate_limit(self):
        assert _is_transient_error("too many requests") is True

    def test_connection_reset(self):
        assert _is_transient_error("Connection reset by peer") is True

    def test_permanent_error(self):
        assert _is_transient_error("Domain not found") is False

    def test_empty_string(self):
        assert _is_transient_error("") is False

    def test_none(self):
        assert _is_transient_error(None) is False


# ===========================================================================
# get_domain_registration_date
# ===========================================================================
class TestGetDomainRegistrationDate:
    """Tests for WHOIS lookup with multiple date-extraction fallbacks."""

    def test_successful_datetime(self):
        whois_result = _mock_whois_result(creation_date=datetime(2020, 1, 15))
        with patch.object(de_main, "whois") as mock_whois_mod:
            mock_whois_mod.whois.return_value = whois_result
            result = get_domain_registration_date("example.com")

        assert result["success"] is True
        assert result["registration_date"] == "2020-01-15"

    def test_list_of_dates(self):
        whois_result = _mock_whois_result(creation_date=[datetime(2018, 6, 1), datetime(2019, 1, 1)])
        with patch.object(de_main, "whois") as mock_whois_mod:
            mock_whois_mod.whois.return_value = whois_result
            result = get_domain_registration_date("example.com")

        assert result["success"] is True
        assert result["registration_date"] == "2018-06-01"

    def test_string_date(self):
        whois_result = _mock_whois_result(creation_date="2019-03-20")
        with patch.object(de_main, "whois") as mock_whois_mod:
            mock_whois_mod.whois.return_value = whois_result
            result = get_domain_registration_date("example.com")

        assert result["success"] is True
        assert result["registration_date"] == "2019-03-20"

    def test_no_date_found(self):
        whois_result = _mock_whois_result(creation_date=None)
        with patch.object(de_main, "whois") as mock_whois_mod:
            mock_whois_mod.whois.return_value = whois_result
            result = get_domain_registration_date("example.com")

        assert result["success"] is False
        assert result["registration_date"] is None

    def test_raw_text_parsing(self):
        """Fallback to parsing creation date from raw WHOIS text."""
        whois_result = _mock_whois_result(
            creation_date=None,
            text="Domain Name: example.com\nCreation Date: 2021-07-15\nExpiry Date: 2025-07-15"
        )
        with patch.object(de_main, "whois") as mock_whois_mod:
            mock_whois_mod.whois.return_value = whois_result
            result = get_domain_registration_date("example.com")

        assert result["success"] is True
        assert result["registration_date"] == "2021-07-15"

    def test_exception_with_date_in_error(self):
        """Some WHOIS libraries include date data in exception messages."""
        with patch.object(de_main, "whois") as mock_whois_mod:
            mock_whois_mod.whois.side_effect = Exception(
                "WhoisCommandFailed: creation date: 2022-01-01"
            )
            result = get_domain_registration_date("example.com")

        assert result["success"] is True
        assert result["registration_date"] == "2022-01-01"

    def test_exception_without_date(self):
        with patch.object(de_main, "whois") as mock_whois_mod:
            mock_whois_mod.whois.side_effect = Exception("Connection refused")
            result = get_domain_registration_date("example.com")

        assert result["success"] is False
        assert "Connection refused" in result["error"]


# ===========================================================================
# check_domain_mx_records
# ===========================================================================
class TestCheckDomainMxRecords:
    """Tests for MX record lookup and classification."""

    def test_google_workspace(self):
        records = [_mock_mx_record(10, "aspmx.l.google.com.")]
        with patch.object(de_main.dns.resolver, "resolve", return_value=records):
            result = check_domain_mx_records("example.com")

        assert result["success"] is True
        assert result["risk_level"] == "LOW"
        assert "Google" in result["provider_detected"]

    def test_microsoft_365(self):
        records = [_mock_mx_record(10, "mail.protection.outlook.com.")]
        with patch.object(de_main.dns.resolver, "resolve", return_value=records):
            result = check_domain_mx_records("example.com")

        assert result["success"] is True
        assert result["risk_level"] == "LOW"
        assert "Microsoft" in result["provider_detected"]

    def test_zoho_standard_trust(self):
        records = [_mock_mx_record(10, "mx.zoho.com.")]
        with patch.object(de_main.dns.resolver, "resolve", return_value=records):
            result = check_domain_mx_records("example.com")

        assert result["success"] is True
        assert result["risk_level"] == "LOW/MEDIUM"

    def test_godaddy_low_trust(self):
        records = [_mock_mx_record(10, "smtp.secureserver.net.")]
        with patch.object(de_main.dns.resolver, "resolve", return_value=records):
            result = check_domain_mx_records("example.com")

        assert result["success"] is True
        assert result["risk_level"] == "HIGH"

    def test_self_hosted(self):
        records = [_mock_mx_record(10, "mail.example.com.")]
        with patch.object(de_main.dns.resolver, "resolve", return_value=records):
            result = check_domain_mx_records("example.com")

        assert result["success"] is True
        assert result["risk_level"] == "MEDIUM"
        assert "Self-Hosted" in result["status"]

    def test_unknown_provider(self):
        records = [_mock_mx_record(10, "mx.obscure-provider.net.")]
        with patch.object(de_main.dns.resolver, "resolve", return_value=records):
            result = check_domain_mx_records("example.com")

        assert result["success"] is True
        assert result["risk_level"] == "MEDIUM"
        assert "Unknown" in result["status"]

    def test_no_answer(self):
        with patch.object(de_main.dns.resolver, "resolve", side_effect=_NoAnswer()):
            result = check_domain_mx_records("example.com")

        assert result["risk_level"] == "CRITICAL"
        assert "No Email" in result["status"]

    def test_nxdomain(self):
        with patch.object(de_main.dns.resolver, "resolve", side_effect=_NXDOMAIN()):
            result = check_domain_mx_records("example.com")

        assert result["risk_level"] == "CRITICAL"
        assert "Not Found" in result["status"]

    def test_generic_exception(self):
        with patch.object(de_main.dns.resolver, "resolve",
                          side_effect=RuntimeError("resolver error")):
            result = check_domain_mx_records("example.com")

        assert result["success"] is False
        assert "resolver error" in result["error"]

    def test_priority_sorting(self):
        """Lower priority number should be the primary MX."""
        records = [
            _mock_mx_record(20, "backup.example.com."),
            _mock_mx_record(10, "aspmx.l.google.com."),
        ]
        with patch.object(de_main.dns.resolver, "resolve", return_value=records):
            result = check_domain_mx_records("example.com")

        assert result["success"] is True
        assert "Google" in result["provider_detected"]


# ===========================================================================
# _retry_lookup
# ===========================================================================
class TestRetryLookup:
    def test_success_first_attempt(self):
        def lookup(domain):
            return {"success": True, "data": "ok"}

        result = _retry_lookup(lookup, "example.com", "Test", time.time())
        assert result["success"] is True

    def test_permanent_failure_no_retry(self):
        call_count = 0

        def lookup(domain):
            nonlocal call_count
            call_count += 1
            return {"success": False, "error": "Domain not found"}

        with patch.object(de_main, "time") as mock_time:
            mock_time.time.return_value = 0.0
            mock_time.sleep = MagicMock()
            result = _retry_lookup(lookup, "example.com", "Test", 0.0)

        assert result["success"] is False
        assert call_count == 1  # No retry for non-transient error

    def test_transient_failure_retries(self):
        call_count = 0

        def lookup(domain):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return {"success": False, "error": "Connection timeout"}
            return {"success": True, "data": "ok"}

        with patch.object(de_main, "time") as mock_time:
            mock_time.time.return_value = 0.0
            mock_time.sleep = MagicMock()
            result = _retry_lookup(lookup, "example.com", "Test", 0.0)

        assert result["success"] is True
        assert call_count == 3

    def test_time_budget_exceeded(self):
        call_count = 0

        def lookup(domain):
            nonlocal call_count
            call_count += 1
            return {"success": False, "error": "Connection timeout"}

        with patch.object(de_main, "time") as mock_time:
            # Simulate time well past budget
            mock_time.time.return_value = 100.0
            mock_time.sleep = MagicMock()
            result = _retry_lookup(lookup, "example.com", "Test", 0.0)

        assert result["success"] is False
        assert call_count == 1  # Only initial attempt (time exceeded before retry)

    def test_all_retries_exhausted_transient(self):
        """All retry attempts exhausted with transient errors returns last result."""
        call_count = 0

        def lookup(domain):
            nonlocal call_count
            call_count += 1
            return {"success": False, "error": "Connection timeout"}

        with patch.object(de_main, "time") as mock_time:
            mock_time.time.return_value = 0.0
            mock_time.sleep = MagicMock()
            result = _retry_lookup(lookup, "example.com", "Test", 0.0)

        assert result["success"] is False
        assert "timeout" in result["error"].lower()
        # Should have been called initial + _MAX_RETRY_ATTEMPTS times
        assert call_count == de_main._MAX_RETRY_ATTEMPTS + 1

    def test_time_budget_during_retry(self):
        """Time budget exceeded between first and second retry breaks early."""
        call_count = 0
        time_values = iter([0.0, 50.0])  # First call ok, second exceeds budget

        def lookup(domain):
            nonlocal call_count
            call_count += 1
            return {"success": False, "error": "Connection timeout"}

        with patch.object(de_main, "time") as mock_time:
            mock_time.time.side_effect = lambda: next(time_values, 100.0)
            mock_time.sleep = MagicMock()
            result = _retry_lookup(lookup, "example.com", "Test", 0.0)

        assert result["success"] is False
        # Only initial attempt + possibly 1 retry before budget check
        assert call_count <= 2


# ===========================================================================
# enrich_single_domain
# ===========================================================================
class TestEnrichSingleDomain:
    """Tests for the per-domain parallel WHOIS + MX orchestrator."""

    def test_both_lookups_succeed(self):
        whois_result = {"success": True, "registration_date": "2020-01-01", "error": None}
        mx_result = {"success": True, "status": "Legitimate", "risk_level": "LOW",
                     "provider_detected": "Google", "mx_records": [], "domain": "example.com", "error": None}

        with patch.object(de_main, "_retry_lookup", side_effect=[whois_result, mx_result]):
            result = enrich_single_domain("example.com", time.time())

        assert result["domain"] == "example.com"
        assert result["whois"] == whois_result
        assert result["mx"] == mx_result
        assert result["error"] is None

    def test_whois_exception_mx_succeeds(self):
        """WHOIS future raises but MX succeeds."""
        mx_result = {"success": True, "status": "Legitimate", "risk_level": "LOW",
                     "provider_detected": "Google", "mx_records": [], "domain": "example.com", "error": None}

        def mock_retry(fn, domain, name, start_time):
            if name == "WHOIS":
                raise RuntimeError("WHOIS timeout")
            return mx_result

        with patch.object(de_main, "_retry_lookup", side_effect=mock_retry):
            result = enrich_single_domain("example.com", time.time())

        assert "WHOIS" in result["error"]
        assert result["mx"] == mx_result

    def test_mx_exception_whois_succeeds(self):
        """MX future raises but WHOIS succeeds."""
        whois_result = {"success": True, "registration_date": "2020-01-01", "error": None}

        def mock_retry(fn, domain, name, start_time):
            if name == "MX":
                raise RuntimeError("DNS resolver failed")
            return whois_result

        with patch.object(de_main, "_retry_lookup", side_effect=mock_retry):
            result = enrich_single_domain("example.com", time.time())

        assert result["whois"] == whois_result
        assert "MX" in result["error"]

    def test_both_lookups_fail(self):
        """Both WHOIS and MX futures raise, error contains both messages."""
        def mock_retry(fn, domain, name, start_time):
            raise RuntimeError(f"{name} failed")

        with patch.object(de_main, "_retry_lookup", side_effect=mock_retry):
            result = enrich_single_domain("example.com", time.time())

        assert "WHOIS" in result["error"]
        assert "MX" in result["error"]
        assert ";" in result["error"]  # Messages concatenated


# ===========================================================================
# main HTTP handler
# ===========================================================================
class TestMainHandler:
    def test_missing_email(self):
        result, status = main_handler(_make_request({}))
        assert status == 400
        assert "email" in result["error"]

    def test_invalid_json(self):
        result, status = main_handler(_make_request(bad_json=True))
        assert status == 400

    def test_personal_email_no_enrichment(self):
        """Personal email domains (gmail, etc.) are skipped."""
        result, status = main_handler(_make_request({"email": "john@gmail.com"}))
        assert status == 200
        assert result["domains"] == {}

    def test_business_email_enriched(self):
        body = {"email": "john@acmecorp.com"}
        mock_enrichment = {
            "domain": "acmecorp.com",
            "whois": {"success": True, "registration_date": "2020-01-01", "error": None},
            "mx": {"success": True, "status": "Legitimate", "risk_level": "LOW",
                   "provider_detected": "Google", "mx_records": [], "domain": "acmecorp.com", "error": None},
            "error": None,
        }

        with patch.object(de_main, "enrich_single_domain", return_value=mock_enrichment):
            result, status = main_handler(_make_request(body))

        assert status == 200
        assert "acmecorp.com" in result["domains"]

    def test_company_domain_dedup(self):
        """If company_domain == email domain, it's only enriched once."""
        body = {"email": "john@acmecorp.com", "company_domain": "acmecorp.com"}

        call_count = 0
        def mock_enrich(domain, start_time):
            nonlocal call_count
            call_count += 1
            return {"domain": domain, "whois": None, "mx": None, "error": None}

        with patch.object(de_main, "enrich_single_domain", side_effect=mock_enrich):
            result, status = main_handler(_make_request(body))

        assert status == 200
        assert call_count == 1

    def test_multiple_domains_enriched(self):
        """Email domain + different company domain → 2 enrichments."""
        body = {"email": "john@acmecorp.com", "company_domain": "otherco.com"}

        call_count = 0
        def mock_enrich(domain, start_time):
            nonlocal call_count
            call_count += 1
            return {"domain": domain, "whois": None, "mx": None, "error": None}

        with patch.object(de_main, "enrich_single_domain", side_effect=mock_enrich):
            result, status = main_handler(_make_request(body))

        assert status == 200
        assert call_count == 2
        assert "acmecorp.com" in result["domains"]
        assert "otherco.com" in result["domains"]

    def test_threadpool_exception_per_domain(self):
        """Exception in enrich_single_domain is captured per-domain in the result."""
        body = {"email": "john@acmecorp.com"}

        def mock_enrich(domain, start_time):
            raise RuntimeError("enrichment boom")

        with patch.object(de_main, "enrich_single_domain", side_effect=mock_enrich):
            result, status = main_handler(_make_request(body))

        assert status == 200
        domain_result = result["domains"]["acmecorp.com"]
        assert "Enrichment failed" in domain_result["error"]
        assert domain_result["whois"] is None
        assert domain_result["mx"] is None

    def test_company_domain_adds_second_enrichment(self):
        """Different company_domain results in two enriched domains."""
        body = {"email": "john@acmecorp.com", "company_domain": "different.com"}

        def mock_enrich(domain, start_time):
            return {"domain": domain, "whois": {"success": True}, "mx": {"success": True}, "error": None}

        with patch.object(de_main, "enrich_single_domain", side_effect=mock_enrich):
            result, status = main_handler(_make_request(body))

        assert status == 200
        assert len(result["domains"]) == 2
        assert "acmecorp.com" in result["domains"]
        assert "different.com" in result["domains"]
