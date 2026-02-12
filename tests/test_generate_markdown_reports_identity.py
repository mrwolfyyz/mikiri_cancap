"""Tests for generate_identity_report() in generate_markdown_reports.py (origination).

Tests the full report generation function with mocked external dependencies
(WHOIS, MX, Gravatar, disposable email blocklist, street view).
"""

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timedelta
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
finally:
    try:
        sys.path.remove(FN_DIR)
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Test data builders
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
):
    """Build minimal investigation data dict for generate_identity_report()."""
    return {
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
        "contactability": contactability or {
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


def _mock_all_external_calls():
    """Return a dict of patch targets for all external service calls.

    Uses patch.object() on the saved module reference (_gm_module) so that
    patches survive other test files overwriting sys.modules["generate_markdown_reports"].
    """
    return {
        "whois": patch.object(_gm_module, "get_domain_registration_date", return_value={
            "success": False, "registration_date": None, "error": "mocked"
        }),
        "mx": patch.object(_gm_module, "check_domain_mx_records", return_value={
            "success": False, "error": "mocked"
        }),
        "gravatar": patch.object(_gm_module, "get_gravatar_profile", return_value={
            "success": False, "profile_url": None, "thumbnail_url": None, "error": "mocked"
        }),
        "blocklist": patch.object(_gm_module, "load_disposable_email_blocklist", return_value=set()),
        "street_view": patch.object(_gm_module, "generate_street_view_url",
                            return_value="https://maps.google.com/test"),
    }


class TestGenerateIdentityReportBasic:
    """Basic report generation tests."""

    def setup_method(self):
        """Reset module-level caches that persist between tests."""
        report_utils._disposable_email_blocklist_cache = None

    def test_minimal_data_creates_file(self, tmp_path):
        """Report is written to output_dir with correct filename."""
        data = _minimal_data()
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        output_file = tmp_path / "Identity___John_Doe.md"
        assert output_file.exists()
        content = output_file.read_text()
        assert len(content) > 100

    def test_report_contains_name(self, tmp_path):
        data = _minimal_data()
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "John Doe" in content

    def test_report_contains_email(self, tmp_path):
        data = _minimal_data(email="test@example.com")
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "test@example.com" in content

    def test_report_contains_location(self, tmp_path):
        data = _minimal_data(city="Vancouver")
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Vancouver" in content

    def test_report_contains_rationale(self, tmp_path):
        data = _minimal_data()
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Test rationale" in content

    def test_report_contains_required_sections(self, tmp_path):
        data = _minimal_data()
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "## Identity Confirmation" in content
        assert "## Social handles" in content
        assert "## Data Breaches" in content
        assert "## Sources" in content


class TestIdentityReportEnrichmentData:
    """Tests for enrichment_data pre-populated vs fallback behavior."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_enrichment_data_skips_inline_lookups(self, tmp_path):
        """When enrichment_data has domain data, no inline lookups should run."""
        data = _minimal_data(email="user@acme.com")
        enrichment = {
            "domains": {
                "acme.com": {
                    "whois": {"success": True, "registration_date": "2010-01-15", "error": None},
                    "mx": {"success": True, "risk_level": "LOW", "provider_detected": "Google Workspace",
                           "mx_records": ["aspmx.l.google.com"], "status": "Legitimate Business Email"},
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"] as mock_whois, mocks["mx"] as mock_mx, mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        # Inline WHOIS/MX should NOT have been called since enrichment data was provided
        mock_whois.assert_not_called()
        mock_mx.assert_not_called()

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Domain Registration" in content
        assert "2010-01-15" in content

    def test_personal_email_triggers_gravatar(self, tmp_path):
        """Personal emails (gmail, etc.) should trigger Gravatar lookup."""
        data = _minimal_data(email="john@gmail.com")
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"] as mock_grav, mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        mock_grav.assert_called_once_with("john@gmail.com")


class TestIdentityReportAlerts:
    """Tests for alert generation in the report."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_no_breaches_generates_warning(self, tmp_path):
        """Zero breaches should produce a 'No Breach History' alert."""
        data = _minimal_data(breaches=[], contactability={
            "score": "low", "reason": "Low footprint",
            "num_social": 0, "num_breaches": 0,
            "footprint_bucket": "LOW", "breach_bucket": "NO",
        })
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "No Breach History" in content

    def test_disposable_email_generates_danger(self, tmp_path):
        """Disposable email should produce a 'Disposable Email Detected' alert."""
        data = _minimal_data(email="user@tempmail.com")
        with patch.object(_gm_module, "get_domain_registration_date", return_value={"success": False, "registration_date": None, "error": "mocked"}), \
             patch.object(_gm_module, "check_domain_mx_records", return_value={"success": False, "error": "mocked"}), \
             patch.object(_gm_module, "get_gravatar_profile", return_value={"success": False, "profile_url": None, "thumbnail_url": None, "error": "mocked"}), \
             patch.object(_gm_module, "load_disposable_email_blocklist", return_value={"tempmail.com"}), \
             patch.object(_gm_module, "generate_street_view_url", return_value="https://maps.google.com/test"):
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Disposable Email Detected" in content

    def test_low_linkedin_connections_alert(self, tmp_path):
        """LinkedIn profile with very few connections generates an alert."""
        handles = [{"platform": "LinkedIn", "handle": "johndoe", "url": "https://linkedin.com/in/johndoe",
                     "confidence": "high"}]
        queries = [{"id": "name_linkedin", "type": "linkedin", "query": "John Doe linkedin",
                     "hits": [{"url": "https://linkedin.com/in/johndoe", "title": "John Doe",
                               "snippet": "5 connections on LinkedIn"}]}]
        data = _minimal_data(top_handles=handles, queries=queries)
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Low LinkedIn Connectivity" in content

    def test_new_domain_registration_alert(self, tmp_path):
        """Recently registered domain should generate an alert."""
        recent_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        data = _minimal_data(email="user@newcorp.com")
        enrichment = {
            "domains": {
                "newcorp.com": {
                    "whois": {"success": True, "registration_date": recent_date, "error": None},
                    "mx": {"success": True, "risk_level": "LOW", "provider_detected": "Google Workspace",
                           "mx_records": ["aspmx.l.google.com"], "status": "Legitimate"},
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Recently Registered Domain" in content


class TestIdentityReportSimplifiedMode:
    """Tests for simplified=True mode."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_simplified_omits_contactability(self, tmp_path):
        data = _minimal_data()
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, simplified=True)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "## Contact-ability" not in content

    def test_simplified_omits_public_sector(self, tmp_path):
        data = _minimal_data()
        data["ontario_salaries"] = {"search_executed": True, "ontario_salary_matches": []}
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, simplified=True)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Public Sector Employment" not in content

    def test_non_simplified_includes_contactability(self, tmp_path):
        data = _minimal_data()
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, simplified=False)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "## Contact-ability" in content


class TestIdentityReportBreaches:
    """Tests for breach section rendering."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_breaches_sorted_chronologically(self, tmp_path):
        breaches = [
            {"name": "BreachB", "date": "2020-06-15"},
            {"name": "BreachA", "date": "2019-01-01"},
            {"name": "BreachC", "date": "2021-12-25"},
        ]
        data = _minimal_data(breaches=breaches)
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        pos_a = content.index("BreachA")
        pos_b = content.index("BreachB")
        pos_c = content.index("BreachC")
        assert pos_a < pos_b < pos_c

    def test_no_breaches_shows_none(self, tmp_path):
        data = _minimal_data(breaches=[])
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "*(None)*" in content

    def test_breach_without_date_shows_unknown(self, tmp_path):
        breaches = [{"name": "SomeBreach", "date": ""}]
        data = _minimal_data(breaches=breaches)
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "SomeBreach" in content
        assert "*(Unknown)*" in content


class TestIdentityReportSocialHandles:
    """Tests for social handles section."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_handles_rendered(self, tmp_path):
        handles = [
            {"platform": "Twitter", "handle": "@johndoe", "url": "https://twitter.com/johndoe", "confidence": "high"},
        ]
        data = _minimal_data(top_handles=handles)
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Twitter" in content
        assert "@johndoe" in content
        assert "high" in content

    def test_handle_snippet_from_query(self, tmp_path):
        handles = [
            {"platform": "Twitter", "handle": "@johndoe", "url": "https://twitter.com/johndoe", "confidence": "high"},
        ]
        queries = [{"id": "test", "type": "search", "query": "test",
                     "hits": [{"url": "https://twitter.com/johndoe", "title": "John", "snippet": "Software Developer in Toronto"}]}]
        data = _minimal_data(top_handles=handles, queries=queries)
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Software Developer in Toronto" in content


class TestIdentityReportContactInfo:
    """Tests for phone/email/address sections from enrichment data."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_phones_rendered(self, tmp_path):
        data = _minimal_data()
        enrichment = {
            "domains": {},
            "addresses": {},
            "contacts": {
                "phones": [{"number_raw": "416-555-1234", "number_digits": "4165551234",
                            "confidence": "high", "source_url": "http://example.com", "snippet": "Call John"}],
                "emails": [],
                "addresses": [],
            },
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "416-555-1234" in content
        assert "Phone Number" in content

    def test_emails_rendered(self, tmp_path):
        data = _minimal_data()
        enrichment = {
            "domains": {},
            "addresses": {},
            "contacts": {
                "phones": [],
                "emails": [{"email": "alt@example.com", "confidence": "medium"}],
                "addresses": [],
            },
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "alt@example.com" in content
        assert "Email Address" in content

    def test_addresses_rendered(self, tmp_path):
        data = _minimal_data()
        enrichment = {
            "domains": {},
            "addresses": {},
            "contacts": {
                "phones": [],
                "emails": [],
                "addresses": [{"address_raw": "123 Main St, Toronto, ON M5V 2K1", "confidence": "high"}],
            },
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "123 Main St" in content
        assert "Address" in content

    def test_no_contact_info_omits_sections(self, tmp_path):
        data = _minimal_data()
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Phone Number" not in content
        assert "Email Address(es) of interest" not in content


class TestIdentityReportCompanyDomain:
    """Tests for company domain sections."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_company_domain_generates_sections(self, tmp_path):
        data = _minimal_data(email="user@acme.com")
        enrichment = {
            "domains": {
                "acme.com": {
                    "whois": {"success": True, "registration_date": "2010-01-15", "error": None},
                    "mx": {"success": True, "risk_level": "LOW", "provider_detected": "Google Workspace",
                           "mx_records": ["aspmx.l.google.com"], "status": "Legitimate"},
                },
                "acmecorp.com": {
                    "whois": {"success": True, "registration_date": "2015-06-01", "error": None},
                    "mx": {"success": True, "risk_level": "LOW", "provider_detected": "Microsoft 365",
                           "mx_records": ["acmecorp-com.mail.protection.outlook.com"], "status": "Legitimate"},
                },
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path,
                                    company_domain="acmecorp.com", enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "Company Domain Registration" in content
        assert "Company Email Infrastructure" in content
        assert "2015-06-01" in content


class TestIdentityReportFrontMatterTags:
    """Tests for YAML front matter tag generation."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_tags_include_borrower(self, tmp_path):
        data = _minimal_data()
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "borrower/john_doe" in content

    def test_tags_include_breach_names(self, tmp_path):
        breaches = [{"name": "LinkedIn", "date": "2021-06-22"}]
        data = _minimal_data(breaches=breaches)
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "breach/linkedin" in content

    def test_tags_include_social_platforms(self, tmp_path):
        handles = [{"platform": "Twitter", "handle": "@jdoe", "url": "https://twitter.com/jdoe", "confidence": "high"}]
        data = _minimal_data(top_handles=handles)
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "social/platform/twitter" in content
