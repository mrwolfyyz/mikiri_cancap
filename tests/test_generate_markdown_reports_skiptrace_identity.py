"""Tests for generate_identity_report_skiptrace() in generate_markdown_reports_skiptrace.py.

Tests the skip trace report generation with mocked external dependencies.
Skip trace reports differ from origination: no alerts/warnings, no contactability section,
no public sector employment.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import report_utils

# ---------------------------------------------------------------------------
# Mock heavy dependencies before importing.
# generate_markdown_reports_skiptrace.py imports report_utils which may
# import whois/dns at function call time (not at import time), but we still
# need to mock them for the test execution.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
FN_DIR = str(REPO_ROOT / "gcp" / "functions" / "report_generator_skiptrace")

sys.path.insert(0, FN_DIR)
try:
    import generate_markdown_reports_skiptrace as _gm_st_module
    get_navigation_bar_skiptrace = _gm_st_module.get_navigation_bar_skiptrace
    generate_identity_report_skiptrace = _gm_st_module.generate_identity_report_skiptrace
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
    """Build minimal investigation data dict."""
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
            "rationale": "Test rationale for skip trace.",
            "top_handles": top_handles or [],
        },
        "breaches": breaches or [],
        "contactability": contactability or {
            "score": "medium",
            "reason": "Moderate footprint",
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

    Uses patch.object() on the saved module reference (_gm_st_module) so that
    patches survive other test files overwriting sys.modules["generate_markdown_reports_skiptrace"].
    """
    return {
        "whois": patch.object(_gm_st_module, "get_domain_registration_date", return_value={
            "success": False, "registration_date": None, "error": "mocked"
        }),
        "mx": patch.object(_gm_st_module, "check_domain_mx_records", return_value={
            "success": False, "error": "mocked"
        }),
        "gravatar": patch.object(_gm_st_module, "get_gravatar_profile", return_value={
            "success": False, "profile_url": None, "thumbnail_url": None, "error": "mocked"
        }),
        "blocklist": patch.object(_gm_st_module, "load_disposable_email_blocklist", return_value=set()),
        "street_view": patch.object(_gm_st_module, "generate_street_view_url",
                            return_value="https://maps.google.com/test"),
    }


# ===========================================================================
# get_navigation_bar_skiptrace
# ===========================================================================
class TestGetNavigationBarSkiptrace:
    """Tests for the skip trace navigation bar."""

    def test_contains_identity_link(self):
        nav = get_navigation_bar_skiptrace({}, "John Doe", "identity")
        assert "Identity" in nav

    def test_wiki_name_format(self):
        nav = get_navigation_bar_skiptrace({}, "John Doe", "identity")
        assert "John_Doe" in nav


# ===========================================================================
# generate_identity_report_skiptrace - Basic tests
# ===========================================================================
class TestSkipTraceIdentityReportBasic:
    """Basic skip trace report generation tests."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_creates_file(self, tmp_path):
        data = _minimal_data()
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report_skiptrace(data, "John Doe", tmp_path)

        output_file = tmp_path / "Identity___John_Doe.md"
        assert output_file.exists()

    def test_report_contains_name(self, tmp_path):
        data = _minimal_data()
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report_skiptrace(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "John Doe" in content

    def test_report_contains_email(self, tmp_path):
        data = _minimal_data(email="test@example.com")
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report_skiptrace(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "test@example.com" in content

    def test_report_contains_required_sections(self, tmp_path):
        data = _minimal_data()
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report_skiptrace(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "## Identity Confirmation" in content
        assert "## Social handles" in content
        assert "## Data Breaches" in content
        assert "## Sources" in content

    def test_skip_trace_has_no_alerts(self, tmp_path):
        """Skip trace reports should NOT contain alert/warning callouts."""
        data = _minimal_data(breaches=[], contactability={
            "score": "low", "reason": "Low footprint",
            "num_social": 0, "num_breaches": 0,
            "footprint_bucket": "LOW", "breach_bucket": "NO",
        })
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report_skiptrace(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        # Skip trace does NOT have alert-style warnings
        assert "No Breach History Detected" not in content
        assert "Disposable Email" not in content

    def test_skip_trace_has_no_contactability(self, tmp_path):
        """Skip trace reports should NOT have a Contact-ability section."""
        data = _minimal_data()
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report_skiptrace(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "## Contact-ability" not in content


# ===========================================================================
# Enrichment data
# ===========================================================================
class TestSkipTraceEnrichmentData:
    """Tests for enrichment data handling."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_enrichment_data_skips_inline_lookups(self, tmp_path):
        data = _minimal_data(email="user@acme.com")
        enrichment = {
            "domains": {
                "acme.com": {
                    "whois": {"success": True, "registration_date": "2010-01-15", "error": None},
                    "mx": {"success": True, "risk_level": "LOW", "provider_detected": "Google Workspace",
                           "mx_records": ["aspmx.l.google.com"], "status": "Legitimate"},
                }
            },
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"] as mock_whois, mocks["mx"] as mock_mx, mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report_skiptrace(data, "John Doe", tmp_path, enrichment_data=enrichment)

        mock_whois.assert_not_called()
        mock_mx.assert_not_called()

    def test_contact_info_rendered(self, tmp_path):
        data = _minimal_data()
        enrichment = {
            "domains": {},
            "addresses": {},
            "contacts": {
                "phones": [{"number_raw": "416-555-9999", "number_digits": "4165559999",
                            "confidence": "high"}],
                "emails": [{"email": "alt@example.com", "confidence": "medium"}],
                "addresses": [{"address_raw": "789 Oak St, Vancouver, BC V6B 1A1", "confidence": "high"}],
            },
        }
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report_skiptrace(data, "John Doe", tmp_path, enrichment_data=enrichment)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "416-555-9999" in content
        assert "alt@example.com" in content
        assert "789 Oak St" in content


# ===========================================================================
# Sources section
# ===========================================================================
class TestSkipTraceSourcesSection:
    """Tests for the Sources section rendering."""

    def setup_method(self):
        report_utils._disposable_email_blocklist_cache = None

    def test_sources_rendered(self, tmp_path):
        queries = [
            {"id": "name_search", "type": "precision", "query": "John Doe Toronto",
             "hits": [{"url": "https://example.com", "title": "John Doe Page",
                        "snippet": "Found on example.com", "source": "vertex_ai_precision"}]},
        ]
        data = _minimal_data(queries=queries)
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report_skiptrace(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "John Doe Toronto" in content
        assert "example.com" in content
        assert "Vertex AI Search" in content

    def test_linkedin_source_uses_site_prefix(self, tmp_path):
        queries = [
            {"id": "company_name_linkedin", "type": "linkedin", "query": "John Doe Acme",
             "hits": [{"url": "https://linkedin.com/in/johndoe", "title": "John Doe",
                        "snippet": "Software Engineer", "source": "vertex_ai_linkedin"}]},
        ]
        data = _minimal_data(queries=queries)
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report_skiptrace(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "site%3Alinkedin.com" in content

    def test_empty_hits_shows_none(self, tmp_path):
        queries = [
            {"id": "name_search", "type": "precision", "query": "Jane Doe Unknown",
             "hits": []},
        ]
        data = _minimal_data(queries=queries)
        mocks = _mock_all_external_calls()
        with mocks["whois"], mocks["mx"], mocks["gravatar"], mocks["blocklist"], mocks["street_view"]:
            generate_identity_report_skiptrace(data, "John Doe", tmp_path)

        content = (tmp_path / "Identity___John_Doe.md").read_text()
        assert "*(None)*" in content
