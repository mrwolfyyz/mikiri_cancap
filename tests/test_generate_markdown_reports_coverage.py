"""Additional tests for generate_markdown_reports.py (origination) to improve coverage.

Targets uncovered lines: 122-123, 231, 258-289, 424-425, 446-463, 506-507, 558-577,
592-604, 617, 627, 652-665, 679-725, 745-746, 892-894, 941-943, 947-950, 956-959,
976, 981-982, 1021, 1062-1068, 1107, 1112-1122, 1177-1180, 1197, 1207, 1213-1215,
1240-1301, 1331, 1497-1498, 1559-1560.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import report_utils

# ---------------------------------------------------------------------------
# Mock heavy dependencies before importing the module.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
FN_DIR = str(REPO_ROOT / "gcp" / "functions" / "report_generator_origination")

_mock_pyap = MagicMock()
_mock_vertexai = MagicMock()
_mock_vertexai_gm = MagicMock()

sys.modules.setdefault("pyap", _mock_pyap)
sys.modules.setdefault("vertexai", _mock_vertexai)
sys.modules.setdefault("vertexai.generative_models", _mock_vertexai_gm)

sys.path.insert(0, FN_DIR)
try:
    import generate_markdown_reports as _gm_module

    generate_identity_report = _gm_module.generate_identity_report
    extract_addresses_from_queries = _gm_module.extract_addresses_from_queries
    extract_linkedin_connections = _gm_module.extract_linkedin_connections
    extract_1st_addresses_fallback = _gm_module.extract_1st_addresses_fallback
    extract_email_handle = _gm_module.extract_email_handle
    generate_canada411_url = _gm_module.generate_canada411_url
finally:
    try:
        sys.path.remove(FN_DIR)
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Test data builders (mirrors the pattern from test_generate_markdown_reports_identity.py)
# ---------------------------------------------------------------------------
def _minimal_data(
    email="john.doe@gmail.com",
    full_name="John Doe",
    city="Toronto",
    province="ON",
    breaches=None,
    top_handles=None,
    contactability=None,
    queries=None,
    grounding_metadata=None,
    ontario_salaries=None,
):
    """Build minimal investigation data dict for generate_identity_report()."""
    result = {
        "seed": {
            "email": email,
            "full_name": full_name,
            "last_known_city": city,
            "province": province,
            "company_name": "",
        },
        "scored": {
            "location": {"city": city, "confidence": "high"},
            "rationale": "Test rationale for identity confirmation.",
            "top_handles": top_handles or [],
        },
        "breaches": breaches or [],
        "contactability": contactability
        or {
            "score": "medium",
            "reason": "Moderate digital footprint",
            "num_social": 2,
            "num_breaches": 1,
            "footprint_bucket": "MED",
            "breach_bucket": "FEW",
        },
        "queries": queries or [],
        "grounding_metadata": grounding_metadata or {},
    }
    if ontario_salaries is not None:
        result["ontario_salaries"] = ontario_salaries
    return result


def _mock_all_external_calls():
    """Return a dict of patch targets for all external service calls."""
    return {
        "whois": patch.object(
            _gm_module,
            "get_domain_registration_date",
            return_value={"success": False, "registration_date": None, "error": "mocked"},
        ),
        "mx": patch.object(_gm_module, "check_domain_mx_records", return_value={"success": False, "error": "mocked"}),
        "gravatar": patch.object(
            _gm_module,
            "get_gravatar_profile",
            return_value={"success": False, "profile_url": None, "thumbnail_url": None, "error": "mocked"},
        ),
        "blocklist": patch.object(_gm_module, "load_disposable_email_blocklist", return_value=set()),
        "street_view": patch.object(
            _gm_module, "generate_street_view_url", return_value="https://maps.google.com/test"
        ),
    }


# ===========================================================================
# extract_linkedin_connections — Pattern 4 (lines 122-123)
# ===========================================================================
class TestExtractLinkedinConnectionsPattern4:
    """Test Pattern 4: '10 connections' (without +) that only matches pattern4."""

    def test_pattern4_space_connections(self):
        """'10 connections' without '+' — exercises lines 122-123."""
        # Pattern 1 matches "NNN connections" and "NNN+ connections", but
        # the code comment says pattern 4 is "without +". Actually pattern 1
        # also catches this. We need a snippet where only pattern 4 fires.
        # Looking closer, pattern1 = r"(\d+)\s*\+?\s*connections?" also matches "10 connections".
        # Pattern 4 = r"(\d+)\s+connections?" is reached only if pattern1 doesn't match.
        # Since pattern1 has \s* (zero or more spaces) before \+?, it always matches first.
        # Lines 122-123 are actually unreachable as pattern1 always wins.
        # However, we should still verify the function works for all connection patterns.
        assert extract_linkedin_connections("Has 10 connections") == 10


# ===========================================================================
# extract_1st_addresses_fallback — line 231 (no direction)
# ===========================================================================
class TestExtract1stAddressesFallbackNoDirection:
    """Test fallback 1st addresses without compass direction (line 231)."""

    def test_1st_street_no_direction(self):
        """Address with 1st but no compass direction exercises line 231.

        The regex requires a space before the comma when no direction is present:
        \\s+(direction)?, -> for no direction this is \\s+,
        """
        text = "Located at 100 1st Avenue , Smalltown, TX, 75001"
        result = extract_1st_addresses_fallback(text)
        assert len(result) >= 1
        assert "100" in result[0]
        assert "1st" in result[0]
        # No direction means addr = "{street_num} {ordinal} {street_type}, {city}, {state}, {zip_code}"
        assert "Smalltown" in result[0]
        assert "TX" in result[0]
        assert "75001" in result[0]

    def test_first_street_no_direction(self):
        """Address with 'First' but no compass direction."""
        text = "Office is at 200 First Boulevard , Portland, OR, 97201"
        result = extract_1st_addresses_fallback(text)
        assert len(result) >= 1
        assert "200" in result[0]
        assert "First" in result[0]


# ===========================================================================
# extract_addresses_from_queries — fallback string addresses (lines 258-289)
# ===========================================================================
class TestExtractAddressesFromQueriesFallback:
    """Test address extraction fallback branch for string addresses (lines 258-289)."""

    def test_fallback_string_address_with_street_number(self):
        """Fallback string with street number passes validation (lines 266-289).

        The 1st address regex requires a space before comma when no direction.
        We mock pyap.parse to return [] AND mock extract_1st_addresses_fallback
        to return a known fallback address with a street number.
        """
        with (
            patch.object(_gm_module, "pyap") as mock_pyap_local,
            patch.object(
                _gm_module,
                "extract_1st_addresses_fallback",
                return_value=["100 1st Avenue, Smalltown, TX, 75001"],
            ),
        ):
            mock_pyap_local.parse.return_value = []

            queries = [
                {
                    "hits": [
                        {
                            "title": "Page about 1st Ave",
                            "snippet": "Located at 100 1st Avenue , Smalltown, TX, 75001",
                            "url": "http://example.com",
                        }
                    ]
                }
            ]
            result = extract_addresses_from_queries(queries)
            # The fallback fires because pyap returned nothing and "1st" is in the text.
            # The extracted address has a street number so it passes the filter.
            assert len(result) >= 1
            assert result[0]["address_raw"] is not None
            # Fallback addresses have None for structured components
            assert result[0]["street_number"] is None

    def test_fallback_string_address_with_street_name(self):
        """Fallback string with street name pattern passes validation (lines 276-282)."""
        with (
            patch.object(_gm_module, "pyap") as mock_pyap_local,
            patch.object(
                _gm_module,
                "extract_1st_addresses_fallback",
                return_value=["456 First Street NW, Denver, CO, 80201"],
            ),
        ):
            mock_pyap_local.parse.return_value = []

            queries = [
                {
                    "hits": [
                        {
                            "title": "Page about First Street",
                            "snippet": "Office at 456 First Street NW, Denver, CO, 80201",
                            "url": "http://example.com",
                        }
                    ]
                }
            ]
            result = extract_addresses_from_queries(queries)
            assert len(result) >= 1

    def test_fallback_city_only_filtered_out(self):
        """Fallback string without street info is filtered (lines 284-287)."""
        with (
            patch.object(_gm_module, "pyap") as mock_pyap_local,
            patch.object(
                _gm_module,
                "extract_1st_addresses_fallback",
                return_value=["Smalltown, TX"],
            ),
        ):
            mock_pyap_local.parse.return_value = []

            queries = [
                {
                    "hits": [
                        {
                            "title": "1st mention",
                            "snippet": "Something about 1st in Smalltown TX",
                            "url": "http://example.com",
                        }
                    ]
                }
            ]
            result = extract_addresses_from_queries(queries)
            # City-only address should be filtered out
            assert len(result) == 0


# ===========================================================================
# Inline lookup exception handling (lines 424-425)
# ===========================================================================
class TestIdentityReportInlineLookupExceptions:
    """Test that inline lookup exceptions are handled gracefully (lines 424-425)."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_inline_whois_exception_handled(self, tmp_path):
        """Inline whois lookup that raises an exception is caught (line 424-425)."""
        data = _minimal_data(email="user@nonpersonal.com")
        with (
            patch.object(
                _gm_module,
                "get_domain_registration_date",
                side_effect=Exception("whois network error"),
            ),
            patch.object(
                _gm_module,
                "check_domain_mx_records",
                side_effect=Exception("mx network error"),
            ),
            patch.object(
                _gm_module,
                "get_gravatar_profile",
                return_value={"success": False, "profile_url": None, "thumbnail_url": None, "error": "mocked"},
            ),
            patch.object(_gm_module, "load_disposable_email_blocklist", return_value=set()),
            patch.object(_gm_module, "generate_street_view_url", return_value="https://maps.google.com/test"),
        ):
            # Should not raise — exceptions handled inside the function
            generate_identity_report(data, "John Doe", tmp_path)

        output_file = tmp_path / "Identity___John_Doe.md"
        assert output_file.exists()


# ===========================================================================
# Company domain inline fallback lookups (lines 446-463)
# ===========================================================================
class TestIdentityReportCompanyDomainFallback:
    """Test company domain fallback to inline lookups (lines 446-463)."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_company_domain_inline_lookups_when_no_enrichment(self, tmp_path):
        """When enrichment_data has no company domain, inline lookups fire (lines 446-461)."""
        data = _minimal_data(email="user@gmail.com")
        enrichment = {
            "domains": {},  # No company domain data
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        with (
            patch.object(
                _gm_module,
                "get_domain_registration_date",
                return_value={"success": True, "registration_date": "2015-01-01", "error": None},
            ) as mock_whois,
            patch.object(
                _gm_module,
                "check_domain_mx_records",
                return_value={
                    "success": True,
                    "risk_level": "LOW",
                    "provider_detected": "Google",
                    "mx_records": ["mx.google.com"],
                    "status": "OK",
                },
            ) as mock_mx,
            patch.object(
                _gm_module,
                "get_gravatar_profile",
                return_value={"success": False, "profile_url": None, "thumbnail_url": None, "error": "mocked"},
            ),
            patch.object(_gm_module, "load_disposable_email_blocklist", return_value=set()),
            patch.object(_gm_module, "generate_street_view_url", return_value="https://maps.google.com/test"),
        ):
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="acmecorp.com", enrichment_data=enrichment
            )

        # The whois and mx mocks should have been called for the company domain inline fallback
        assert mock_whois.call_count >= 1
        assert mock_mx.call_count >= 1

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Company Domain Registration" in content
        assert "Company Email Infrastructure" in content

    def test_company_domain_inline_whois_exception(self, tmp_path):
        """Company domain inline whois exception is handled (lines 456-457)."""
        data = _minimal_data(email="user@gmail.com")
        enrichment = {
            "domains": {},
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        with (
            patch.object(
                _gm_module,
                "get_domain_registration_date",
                side_effect=Exception("company whois error"),
            ),
            patch.object(
                _gm_module,
                "check_domain_mx_records",
                side_effect=Exception("company mx error"),
            ),
            patch.object(
                _gm_module,
                "get_gravatar_profile",
                return_value={"success": False, "profile_url": None, "thumbnail_url": None, "error": "mocked"},
            ),
            patch.object(_gm_module, "load_disposable_email_blocklist", return_value=set()),
            patch.object(_gm_module, "generate_street_view_url", return_value="https://maps.google.com/test"),
        ):
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="acmecorp.com", enrichment_data=enrichment
            )

        output_file = tmp_path / "Identity___John_Doe.md"
        assert output_file.exists()

    def test_company_domain_empty_after_strip(self, tmp_path):
        """Company domain that is only whitespace (line 463)."""
        data = _minimal_data(email="user@gmail.com")
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, company_domain="   ")

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        # Company sections should not appear
        assert "Company Domain Registration" not in content


# ===========================================================================
# LinkedIn connections warning level (lines 506-507)
# ===========================================================================
class TestIdentityReportLinkedinWarning:
    """Test LinkedIn connections warning level (10 < connections <= 100)."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_linkedin_connections_warning_level(self, tmp_path):
        """50 connections triggers warning level, not danger (lines 506-507)."""
        handles = [
            {
                "platform": "LinkedIn",
                "handle": "johndoe",
                "url": "https://linkedin.com/in/johndoe",
                "confidence": "high",
            }
        ]
        queries = [
            {
                "id": "name_linkedin",
                "type": "linkedin",
                "query": "John Doe linkedin",
                "hits": [
                    {
                        "url": "https://linkedin.com/in/johndoe",
                        "title": "John Doe",
                        "snippet": "50 connections on LinkedIn",
                    }
                ],
            }
        ]
        data = _minimal_data(top_handles=handles, queries=queries)
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Low LinkedIn Connectivity" in content
        # Should use warning callout, not danger
        assert "[!warning]" in content


# ===========================================================================
# Domain registration alert — age text branches (lines 558, 562-564, 576-577)
# ===========================================================================
class TestIdentityReportDomainAgeAlertText:
    """Test domain registration alert age text calculation branches."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_domain_age_under_30_days_text(self, tmp_path):
        """Domain < 30 days shows 'X days' (line 558)."""
        recent_date = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
        data = _minimal_data(email="user@newbiz.com")
        enrichment = {
            "domains": {
                "newbiz.com": {
                    "whois": {"success": True, "registration_date": recent_date, "error": None},
                    "mx": {
                        "success": True,
                        "risk_level": "LOW",
                        "provider_detected": "Google",
                        "mx_records": ["mx.google.com"],
                        "status": "OK",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "days" in content
        assert "Recently Registered Domain" in content
        assert "[!danger]" in content

    def test_domain_age_months_text(self, tmp_path):
        """Domain 30-365 days shows 'X months' (lines 562-564)."""
        months_ago = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        data = _minimal_data(email="user@midbiz.com")
        enrichment = {
            "domains": {
                "midbiz.com": {
                    "whois": {"success": True, "registration_date": months_ago, "error": None},
                    "mx": {
                        "success": True,
                        "risk_level": "LOW",
                        "provider_detected": "Google",
                        "mx_records": ["mx.google.com"],
                        "status": "OK",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "months" in content
        assert "Recently Registered Domain" in content
        # Warning callout for domain < 1 year (line 576-577)
        assert "[!warning]" in content

    def test_domain_age_older_info(self, tmp_path):
        """Domain > 1 year old domain age text shows 'X years' (line 976, 981-982)."""
        old_date = (datetime.now() - timedelta(days=800)).strftime("%Y-%m-%d")
        data = _minimal_data(email="user@oldbiz.com")
        enrichment = {
            "domains": {
                "oldbiz.com": {
                    "whois": {"success": True, "registration_date": old_date, "error": None},
                    "mx": {
                        "success": True,
                        "risk_level": "LOW",
                        "provider_detected": "Google",
                        "mx_records": ["mx.google.com"],
                        "status": "OK",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Domain Registration" in content
        assert "years" in content
        assert "[!info]" in content


# ===========================================================================
# MX alert branches — email domain (lines 592-604, 617, 627)
# ===========================================================================
class TestIdentityReportMxAlerts:
    """Test MX record alert branches for email domain (lines 592-644)."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_mx_high_risk_alert(self, tmp_path):
        """HIGH risk MX generates danger alert (lines 592-602)."""
        data = _minimal_data(email="user@sketchy.com")
        enrichment = {
            "domains": {
                "sketchy.com": {
                    "whois": {"success": False, "registration_date": None, "error": "failed"},
                    "mx": {
                        "success": True,
                        "risk_level": "HIGH",
                        "provider_detected": "Parking Page",
                        "mx_records": ["park.registrar.com"],
                        "status": "Default Registrar Services",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Default Registrar Email Services" in content
        assert "Business Email Not Deliverable" in content

    def test_mx_medium_risk_alert(self, tmp_path):
        """MEDIUM risk MX generates warning alert (lines 603-612)."""
        data = _minimal_data(email="user@selfhosted.com")
        enrichment = {
            "domains": {
                "selfhosted.com": {
                    "whois": {"success": False, "registration_date": None, "error": "failed"},
                    "mx": {
                        "success": True,
                        "risk_level": "MEDIUM",
                        "provider_detected": "Self-hosted",
                        "mx_records": ["mail.selfhosted.com"],
                        "status": "Self-Hosted Email",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Self-Hosted Email Infrastructure" in content

    def test_mx_critical_no_email_configured(self, tmp_path):
        """CRITICAL + No Email Configured generates danger alert (line 617)."""
        data = _minimal_data(email="user@nomail.com")
        enrichment = {
            "domains": {
                "nomail.com": {
                    "whois": {"success": False, "registration_date": None, "error": "failed"},
                    "mx": {
                        "success": False,
                        "risk_level": "CRITICAL",
                        "status": "No Email Configured",
                        "error": "No MX records found",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "No MX Records" in content
        assert "Business Email Not Deliverable" in content

    def test_mx_critical_domain_not_found(self, tmp_path):
        """CRITICAL + Domain Not Found generates danger alert (line 627)."""
        data = _minimal_data(email="user@nonexist.com")
        enrichment = {
            "domains": {
                "nonexist.com": {
                    "whois": {"success": False, "registration_date": None, "error": "failed"},
                    "mx": {
                        "success": False,
                        "risk_level": "CRITICAL",
                        "status": "Domain Not Found",
                        "error": "NXDOMAIN",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Domain Not Found" in content
        assert "Business Email Invalid" in content


# ===========================================================================
# Company domain MX alerts (lines 652-725)
# ===========================================================================
class TestIdentityReportCompanyMxAlerts:
    """Test company domain MX and WHOIS alert branches (lines 652-733)."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def _enrichment_with_company(self, email_domain_data, company_domain_data):
        """Helper to build enrichment data with both email and company domain."""
        return {
            "domains": {
                **email_domain_data,
                **company_domain_data,
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }

    def test_company_domain_recent_whois_alert(self, tmp_path):
        """Recently registered company domain generates alert (lines 652-673)."""
        recent_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        data = _minimal_data(email="user@gmail.com")
        enrichment = self._enrichment_with_company(
            {},
            {
                "newcompany.com": {
                    "whois": {"success": True, "registration_date": recent_date, "error": None},
                    "mx": {
                        "success": True,
                        "risk_level": "LOW",
                        "provider_detected": "Google",
                        "mx_records": ["mx.google.com"],
                        "status": "OK",
                    },
                }
            },
        )
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="newcompany.com", enrichment_data=enrichment
            )

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Recently Registered Company Domain" in content
        assert "days" in content

    def test_company_domain_months_old_whois_alert(self, tmp_path):
        """Company domain 3-12 months old generates warning (lines 658-661)."""
        months_ago = (datetime.now() - timedelta(days=150)).strftime("%Y-%m-%d")
        data = _minimal_data(email="user@gmail.com")
        enrichment = self._enrichment_with_company(
            {},
            {
                "midcompany.com": {
                    "whois": {"success": True, "registration_date": months_ago, "error": None},
                    "mx": {
                        "success": True,
                        "risk_level": "LOW",
                        "provider_detected": "Google",
                        "mx_records": ["mx.google.com"],
                        "status": "OK",
                    },
                }
            },
        )
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="midcompany.com", enrichment_data=enrichment
            )

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Recently Registered Company Domain" in content
        assert "months" in content

    def test_company_mx_high_risk(self, tmp_path):
        """Company domain HIGH risk MX (lines 679-690)."""
        data = _minimal_data(email="user@gmail.com")
        enrichment = self._enrichment_with_company(
            {},
            {
                "badcompany.com": {
                    "whois": {"success": False, "registration_date": None, "error": "failed"},
                    "mx": {
                        "success": True,
                        "risk_level": "HIGH",
                        "provider_detected": "Parking",
                        "mx_records": ["park.registrar.com"],
                        "status": "Default Registrar",
                    },
                }
            },
        )
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="badcompany.com", enrichment_data=enrichment
            )

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        # Alert at top of report
        assert "Default Registrar Email Services" in content

    def test_company_mx_medium_risk(self, tmp_path):
        """Company domain MEDIUM risk MX (lines 691-700)."""
        data = _minimal_data(email="user@gmail.com")
        enrichment = self._enrichment_with_company(
            {},
            {
                "selfhosted-co.com": {
                    "whois": {"success": False, "registration_date": None, "error": "failed"},
                    "mx": {
                        "success": True,
                        "risk_level": "MEDIUM",
                        "provider_detected": "Self-hosted",
                        "mx_records": ["mail.selfhosted-co.com"],
                        "status": "Self-Hosted",
                    },
                }
            },
        )
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="selfhosted-co.com", enrichment_data=enrichment
            )

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Self-Hosted Email Infrastructure" in content

    def test_company_mx_critical_no_email(self, tmp_path):
        """Company domain CRITICAL + No Email Configured (lines 704-713)."""
        data = _minimal_data(email="user@gmail.com")
        enrichment = self._enrichment_with_company(
            {},
            {
                "nomx-co.com": {
                    "whois": {"success": False, "registration_date": None, "error": "failed"},
                    "mx": {
                        "success": False,
                        "risk_level": "CRITICAL",
                        "status": "No Email Configured",
                        "error": "No MX records",
                    },
                }
            },
        )
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="nomx-co.com", enrichment_data=enrichment
            )

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "No MX Records" in content

    def test_company_mx_critical_domain_not_found(self, tmp_path):
        """Company domain CRITICAL + Domain Not Found (lines 714-723)."""
        data = _minimal_data(email="user@gmail.com")
        enrichment = self._enrichment_with_company(
            {},
            {
                "ghost-co.com": {
                    "whois": {"success": False, "registration_date": None, "error": "failed"},
                    "mx": {
                        "success": False,
                        "risk_level": "CRITICAL",
                        "status": "Domain Not Found",
                        "error": "NXDOMAIN",
                    },
                }
            },
        )
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="ghost-co.com", enrichment_data=enrichment
            )

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Domain Not Found" in content
        assert "Business Email Invalid" in content

    def test_company_mx_generic_failure(self, tmp_path):
        """Company MX generic failure with danger/warning callout (lines 724-733)."""
        data = _minimal_data(email="user@gmail.com")
        enrichment = self._enrichment_with_company(
            {},
            {
                "broken-co.com": {
                    "whois": {"success": False, "registration_date": None, "error": "failed"},
                    "mx": {
                        "success": False,
                        "risk_level": "HIGH",
                        "status": "Lookup Failed",
                        "error": "DNS timeout",
                    },
                }
            },
        )
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="broken-co.com", enrichment_data=enrichment
            )

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Email Infrastructure Verification Failed" in content


# ===========================================================================
# Breach sort with invalid dates (lines 892-894, 745-746)
# ===========================================================================
class TestIdentityReportBreachSorting:
    """Test breach sorting with invalid date formats."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_breach_with_invalid_date_sorted_to_end(self, tmp_path):
        """Breaches with unparseable dates go to the end (lines 892-894)."""
        breaches = [
            {"name": "BadDateBreach", "date": "not-a-date"},
            {"name": "EarlyBreach", "date": "2018-01-01"},
            {"name": "LateBreach", "date": "2022-06-15"},
        ]
        data = _minimal_data(breaches=breaches)
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        pos_early = content.index("EarlyBreach")
        pos_late = content.index("LateBreach")
        pos_bad = content.index("BadDateBreach")
        # EarlyBreach < LateBreach < BadDateBreach (invalid goes to end)
        assert pos_early < pos_late < pos_bad

    def test_earliest_breach_date_skips_invalid(self, tmp_path):
        """Invalid breach dates are ignored when calculating earliest_breach_date (lines 745-746)."""
        breaches = [
            {"name": "ValidBreach", "date": "2020-03-15"},
            {"name": "BadBreach", "date": "invalid-date-format"},
        ]
        data = _minimal_data(
            breaches=breaches,
            contactability={
                "score": "medium",
                "reason": "test",
                "num_social": 1,
                "num_breaches": 2,
                "footprint_bucket": "MED",
                "breach_bucket": "SOME",
            },
        )
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "2020-03-15" in content  # Earliest valid breach date shown


# ===========================================================================
# Gravatar breach + deleted profile (lines 941-943, 947-950, 956-959)
# ===========================================================================
class TestIdentityReportGravatarHygiene:
    """Test Gravatar breach detection and digital footprint hygiene."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_gravatar_breach_with_deleted_profile_with_date(self, tmp_path):
        """Gravatar in breaches + failed profile = high hygiene with date (lines 941-943, 947-948)."""
        breaches = [
            {"name": "Gravatar", "date": "2020-10-01"},
        ]
        data = _minimal_data(
            email="user@gmail.com",
            breaches=breaches,
            contactability={
                "score": "medium",
                "reason": "test",
                "num_social": 1,
                "num_breaches": 1,
                "footprint_bucket": "MED",
                "breach_bucket": "FEW",
            },
        )
        mocks = _mock_all_external_calls()
        # Gravatar profile lookup fails (user deleted it)
        with (
            mocks["whois"],
            mocks["mx"],
            patch.object(
                _gm_module,
                "get_gravatar_profile",
                return_value={"success": False, "profile_url": None, "thumbnail_url": None, "error": "not found"},
            ),
            mocks["blocklist"],
            mocks["street_view"],
        ):
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Digital footprint hygiene" in content
        assert "High" in content
        assert "2020-10-01" in content

    def test_gravatar_breach_without_date(self, tmp_path):
        """Gravatar breach without date still shows hygiene (lines 949-950)."""
        breaches = [
            {"name": "Gravatar", "date": ""},
        ]
        data = _minimal_data(
            email="user@gmail.com",
            breaches=breaches,
            contactability={
                "score": "medium",
                "reason": "test",
                "num_social": 1,
                "num_breaches": 1,
                "footprint_bucket": "MED",
                "breach_bucket": "FEW",
            },
        )
        mocks = _mock_all_external_calls()
        with (
            mocks["whois"],
            mocks["mx"],
            patch.object(
                _gm_module,
                "get_gravatar_profile",
                return_value={"success": False, "profile_url": None, "thumbnail_url": None, "error": "not found"},
            ),
            mocks["blocklist"],
            mocks["street_view"],
        ):
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Digital footprint hygiene" in content
        assert "deleted Gravatar profile after breach" in content

    def test_gravatar_profile_success_renders_section(self, tmp_path):
        """Successful Gravatar profile renders profile section (lines 956-959)."""
        data = _minimal_data(email="user@gmail.com")
        mocks = _mock_all_external_calls()
        with (
            mocks["whois"],
            mocks["mx"],
            patch.object(
                _gm_module,
                "get_gravatar_profile",
                return_value={
                    "success": True,
                    "profile_url": "https://gravatar.com/profile/user",
                    "thumbnail_url": "https://gravatar.com/avatar/abc123",
                },
            ),
            mocks["blocklist"],
            mocks["street_view"],
        ):
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "### Gravatar Profile" in content
        assert "gravatar.com/avatar/abc123" in content
        assert "View Full Profile" in content


# ===========================================================================
# Domain registration section — age text branches (lines 976, 981-982)
# ===========================================================================
class TestIdentityReportDomainRegistrationSection:
    """Test domain registration section age text."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_domain_age_under_30_days_in_section(self, tmp_path):
        """Domain age < 30 days shown as 'X days' in the section (line 976)."""
        recent = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        data = _minimal_data(email="user@brand-new.com")
        enrichment = {
            "domains": {
                "brand-new.com": {
                    "whois": {"success": True, "registration_date": recent, "error": None},
                    "mx": {
                        "success": True,
                        "risk_level": "LOW",
                        "provider_detected": "Google",
                        "mx_records": ["mx.google.com"],
                        "status": "OK",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Domain Age:" in content
        assert "days" in content


# ===========================================================================
# MX LOW/MEDIUM risk (line 1021) and company MX LOW/MEDIUM (line 1107)
# ===========================================================================
class TestIdentityReportMxLowMedium:
    """Test MX LOW/MEDIUM risk level rendering."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_mx_low_medium_shows_caution(self, tmp_path):
        """LOW/MEDIUM risk shows caution message in MX section (line 1021)."""
        data = _minimal_data(email="user@midtier.com")
        enrichment = {
            "domains": {
                "midtier.com": {
                    "whois": {"success": False, "registration_date": None, "error": "failed"},
                    "mx": {
                        "success": True,
                        "risk_level": "LOW/MEDIUM",
                        "provider_detected": "Standard Business",
                        "mx_records": ["mx.midtier.com"],
                        "status": "Standard",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Caution" in content
        assert "standard business email services" in content

    def test_company_mx_low_medium_shows_caution(self, tmp_path):
        """Company domain LOW/MEDIUM risk shows caution (line 1107)."""
        data = _minimal_data(email="user@gmail.com")
        enrichment = {
            "domains": {
                "company-mid.com": {
                    "whois": {"success": False, "registration_date": None, "error": "failed"},
                    "mx": {
                        "success": True,
                        "risk_level": "LOW/MEDIUM",
                        "provider_detected": "Standard Business",
                        "mx_records": ["mx.company-mid.com"],
                        "status": "Standard",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="company-mid.com", enrichment_data=enrichment
            )

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        # Company MX section rendered
        assert "Company Email Infrastructure" in content
        assert "Caution" in content


# ===========================================================================
# Company MX failed/error card (lines 1112-1122)
# ===========================================================================
class TestIdentityReportCompanyMxFailed:
    """Test company MX failed lookup rendering (lines 1112-1122)."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_company_mx_failed_renders_error_card(self, tmp_path):
        """Company MX lookup that failed renders error details (lines 1112-1122)."""
        data = _minimal_data(email="user@gmail.com")
        enrichment = {
            "domains": {
                "broken-co.com": {
                    "whois": {"success": False, "registration_date": None, "error": "failed"},
                    "mx": {
                        "success": False,
                        "risk_level": "CRITICAL",
                        "status": "Lookup Failed",
                        "error": "DNS timeout error",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="broken-co.com", enrichment_data=enrichment
            )

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Company Email Infrastructure" in content
        assert "Lookup Failed" in content
        assert "DNS timeout error" in content

    def test_company_mx_none_renders_not_performed(self, tmp_path):
        """No company MX result renders 'MX lookup not performed' (lines 1112-1116)."""
        data = _minimal_data(email="user@gmail.com")
        enrichment = {
            "domains": {
                "no-mx-co.com": {
                    "whois": {"success": False, "registration_date": None, "error": "failed"},
                    "mx": None,  # No MX result at all
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="no-mx-co.com", enrichment_data=enrichment
            )

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Company Email Infrastructure" in content
        assert "MX lookup not performed" in content


# ===========================================================================
# Company domain registration — age text (lines 1062-1068)
# ===========================================================================
class TestIdentityReportCompanyDomainRegistration:
    """Test company domain registration section age text branches."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_company_domain_age_under_30_days(self, tmp_path):
        """Company domain < 30 days shows 'days' text (line 1062)."""
        recent = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        data = _minimal_data(email="user@gmail.com")
        enrichment = {
            "domains": {
                "brand-new-co.com": {
                    "whois": {"success": True, "registration_date": recent, "error": None},
                    "mx": {
                        "success": True,
                        "risk_level": "LOW",
                        "provider_detected": "Google",
                        "mx_records": ["mx.google.com"],
                        "status": "OK",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="brand-new-co.com", enrichment_data=enrichment
            )

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Company Domain Registration" in content
        assert "days" in content

    def test_company_domain_age_months(self, tmp_path):
        """Company domain 30-365 days shows 'months' (line 1064)."""
        months_ago = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
        data = _minimal_data(email="user@gmail.com")
        enrichment = {
            "domains": {
                "mid-co.com": {
                    "whois": {"success": True, "registration_date": months_ago, "error": None},
                    "mx": {
                        "success": True,
                        "risk_level": "LOW",
                        "provider_detected": "Google",
                        "mx_records": ["mx.google.com"],
                        "status": "OK",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="mid-co.com", enrichment_data=enrichment
            )

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Company Domain Registration" in content
        assert "months" in content

    def test_company_domain_age_years(self, tmp_path):
        """Company domain > 1 year shows 'years' (line 1066)."""
        old = (datetime.now() - timedelta(days=1000)).strftime("%Y-%m-%d")
        data = _minimal_data(email="user@gmail.com")
        enrichment = {
            "domains": {
                "old-co.com": {
                    "whois": {"success": True, "registration_date": old, "error": None},
                    "mx": {
                        "success": True,
                        "risk_level": "LOW",
                        "provider_detected": "Google",
                        "mx_records": ["mx.google.com"],
                        "status": "OK",
                    },
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(
                data, "John Doe", tmp_path, company_domain="old-co.com", enrichment_data=enrichment
            )

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Company Domain Registration" in content
        assert "years" in content


# ===========================================================================
# Contact extraction rendering — email source/snippet, address geocoding (lines 1177-1215)
# ===========================================================================
class TestIdentityReportContactRendering:
    """Test contact info section rendering details."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_email_with_source_and_snippet(self, tmp_path):
        """Email with source_url and snippet renders both (lines 1177, 1179-1180)."""
        data = _minimal_data(email="user@gmail.com")
        enrichment = {
            "domains": {},
            "addresses": {},
            "contacts": {
                "phones": [],
                "emails": [
                    {
                        "email": "alt@corp.com",
                        "confidence": "high",
                        "source_url": "http://corp.com/team",
                        "snippet": "Contact alt@corp.com for inquiries",
                    }
                ],
                "addresses": [],
            },
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "alt@corp.com" in content
        assert "http://corp.com/team" in content
        assert "Contact alt@corp.com for inquiries" in content

    def test_address_with_geocoding_data(self, tmp_path):
        """Address with geocoding enrichment data uses cached coords (lines 1197, 1207, 1213-1215)."""
        data = _minimal_data(email="user@gmail.com")
        enrichment = {
            "domains": {},
            "addresses": {
                "123 Main St, Toronto, ON M5V 2K1": {
                    "lat": 43.6532,
                    "lon": -79.3832,
                }
            },
            "contacts": {
                "phones": [],
                "emails": [],
                "addresses": [
                    {
                        "address_raw": "123 Main St, Toronto, ON M5V 2K1",
                        "confidence": "high",
                        "source_url": "http://realty.com",
                        "snippet": "Property at this address",
                    }
                ],
            },
        }
        mocks = _mock_all_external_calls()
        with (
            mocks["whois"],
            mocks["mx"],
            mocks["gravatar"],
            mocks["blocklist"],
            patch.object(
                _gm_module, "generate_street_view_url", return_value="https://maps.google.com/test"
            ) as mock_sv,
        ):
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "123 Main St" in content
        assert "View Property" in content
        # Verify generate_street_view_url was called with cached_coords
        mock_sv.assert_called()
        call_kwargs = mock_sv.call_args
        # The call uses positional args and keyword args
        assert call_kwargs[1].get("cached_coords") is not None or (
            len(call_kwargs[0]) > 2 and call_kwargs[0][2] is not None
        )

    def test_address_different_raw_vs_cleaned(self, tmp_path):
        """Address where raw != cleaned triggers extra debug print (line 1207)."""
        data = _minimal_data(email="user@gmail.com")
        enrichment = {
            "domains": {},
            "addresses": {},
            "contacts": {
                "phones": [],
                "emails": [],
                "addresses": [
                    {
                        "address_raw": "  123 Main St, Toronto, ON M5V 2K1  ",
                        "confidence": "medium",
                    }
                ],
            },
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Address" in content


# ===========================================================================
# Ontario Public Sector Employment (lines 1240-1301)
# ===========================================================================
class TestIdentityReportOntarioSalaries:
    """Test Ontario Public Sector Employment section rendering."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_ontario_salaries_no_records(self, tmp_path):
        """search_executed=True but no matches shows 'No Records Found' (lines 1295-1299)."""
        data = _minimal_data(
            ontario_salaries={"search_executed": True, "ontario_salary_matches": []},
        )
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Public Sector Employment (Ontario)" in content
        assert "No Records Found" in content
        assert "Only includes public sector employees earning $100k+" in content

    def test_ontario_salaries_single_match(self, tmp_path):
        """Single match renders table with salary progression (lines 1247-1291)."""
        data = _minimal_data(
            ontario_salaries={
                "search_executed": True,
                "ontario_salary_matches": [
                    {
                        "matched_name": "John A. Doe",
                        "confidence": "high",
                        "match_score": 92,
                        "city_match": True,
                        "years_span": "2021-2023",
                        "salary_progression": {
                            "oldest_total": 105000.00,
                            "newest_total": 120000.00,
                            "change_amount": 15000.00,
                            "change_percent": 14.3,
                        },
                        "records": [
                            {
                                "year": 2023,
                                "employer": "Ontario Ministry of Health",
                                "job_title": "Senior Analyst",
                                "sector": "Public",
                                "total_comp_formatted": "$120,000.00",
                            },
                            {
                                "year": 2022,
                                "employer": "Ontario Ministry of Health",
                                "job_title": "Analyst",
                                "sector": "Public",
                                "total_comp_formatted": "$112,000.00",
                            },
                            {
                                "year": 2021,
                                "employer": "Ontario Ministry of Health",
                                "job_title": "Junior Analyst",
                                "sector": "Public",
                                "total_comp_formatted": "$105,000.00",
                            },
                        ],
                    }
                ],
            },
        )
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Public Sector Employment (Ontario)" in content
        assert "John A. Doe" in content
        assert "High confidence match" in content
        assert "92%" in content
        assert "City alignment:" in content
        assert "Matched" in content
        assert "Senior Analyst" in content
        assert "Employment span:" in content
        assert "2021-2023" in content
        assert "3 years on record" in content
        assert "Salary progression:" in content
        assert "$105,000.00" in content
        assert "$120,000.00" in content

    def test_ontario_salaries_multiple_matches(self, tmp_path):
        """Multiple matches show warning and separator (lines 1243-1245, 1293-1294)."""
        data = _minimal_data(
            ontario_salaries={
                "search_executed": True,
                "ontario_salary_matches": [
                    {
                        "matched_name": "John A. Doe",
                        "confidence": "high",
                        "match_score": 90,
                        "city_match": True,
                        "years_span": "2022-2023",
                        "salary_progression": {
                            "oldest_total": 110000.00,
                            "newest_total": 115000.00,
                            "change_amount": 5000.00,
                            "change_percent": 4.5,
                        },
                        "records": [
                            {
                                "year": 2023,
                                "employer": "City of Toronto",
                                "job_title": "Manager",
                                "sector": "Municipal",
                                "total_comp_formatted": "$115,000.00",
                            },
                            {
                                "year": 2022,
                                "employer": "City of Toronto",
                                "job_title": "Manager",
                                "sector": "Municipal",
                                "total_comp_formatted": "$110,000.00",
                            },
                        ],
                    },
                    {
                        "matched_name": "John B. Doe",
                        "confidence": "medium",
                        "match_score": 75,
                        "city_match": False,
                        "years_span": "2021-2021",
                        "salary_progression": {},
                        "records": [
                            {
                                "year": 2021,
                                "employer": "Ontario Power Generation",
                                "job_title": "Engineer",
                                "sector": "Electricity",
                                "total_comp_formatted": "$130,000.00",
                            },
                        ],
                    },
                ],
            },
        )
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Multiple Potential Matches" in content
        assert "John A. Doe" in content
        assert "John B. Doe" in content
        assert "No city data or no match" in content  # city_match=False for second match

    def test_ontario_salaries_not_executed(self, tmp_path):
        """search_executed=False omits the entire section."""
        data = _minimal_data(
            ontario_salaries={"search_executed": False, "ontario_salary_matches": []},
        )
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Public Sector Employment" not in content


# ===========================================================================
# Sources section — unknown source label (line 1331)
# ===========================================================================
class TestIdentityReportSourcesSection:
    """Test sources section rendering."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_unknown_source_label(self, tmp_path):
        """Query with unknown source type gets title-cased label (line 1331)."""
        queries = [
            {
                "id": "custom_search",
                "type": "custom",
                "query": "John Doe custom",
                "hits": [
                    {
                        "url": "http://example.com",
                        "title": "Example",
                        "snippet": "Found John",
                        "source": "bing_web_search",
                    }
                ],
            }
        ]
        data = _minimal_data(queries=queries)
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        # "bing_web_search" -> "Bing Web Search"
        assert "Bing Web Search" in content


# ===========================================================================
# generate_canada411_url — comma-split fallback (lines 1497-1498)
# ===========================================================================
class TestCanada411UrlFallback:
    """Test Canada411 URL generation fallback to comma-split."""

    def test_comma_split_fallback(self):
        """When no structured components and no regex match, comma split is used (lines 1497-1498)."""
        data = {
            "address_raw": "SomePlace, SomeCity, Somewhere",
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
        # The comma-split should extract street and city
        assert "st=" in url
        assert "ci=" in url


# ===========================================================================
# extract_email_handle — exception path (lines 1559-1560)
# These lines are the try/except in extract_email_handle — the except branch
# is technically unreachable with normal strings but we test edge cases.
# ===========================================================================
class TestExtractEmailHandleEdge:
    """Edge case tests for extract_email_handle."""

    def test_whitespace_email(self):
        """Email with whitespace around handle."""
        result = extract_email_handle(" user @domain.com")
        assert result == "user"
