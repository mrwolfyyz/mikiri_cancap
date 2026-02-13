"""Smoke tests for markdown report generation.

Exercises the actual report rendering functions (generate_identity_report_skiptrace
and generate_identity_report) with realistic data shapes extracted from production
Firestore jobs.  Catches crashes from unexpected null fields, missing keys, etc.

Network-dependent helpers (Gravatar, WHOIS, MX) are mocked so tests run offline.
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

SKIPTRACE_FN_DIR = REPO_ROOT / "gcp" / "functions" / "report_generator_skiptrace"
ORIGINATION_FN_DIR = REPO_ROOT / "gcp" / "functions" / "report_generator_origination"


# ---------------------------------------------------------------------------
# Module loaders (import the generators directly, not main.py)
# ---------------------------------------------------------------------------
def _load_module(fn_dir: Path, filename: str, alias: str):
    """Import a Python file from a function directory with its local deps on sys.path."""
    fn_dir_str = str(fn_dir)
    filepath = str(fn_dir / filename)

    sys.path.insert(0, fn_dir_str)
    try:
        spec = importlib.util.spec_from_file_location(alias, filepath)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[alias] = mod
        spec.loader.exec_module(mod)
    finally:
        try:
            sys.path.remove(fn_dir_str)
        except ValueError:
            pass
    return mod


# Load the generator modules (not main.py — we only want the report functions)
skiptrace_gen = _load_module(
    SKIPTRACE_FN_DIR,
    "generate_markdown_reports_skiptrace.py",
    "_test_gen_skiptrace",
)

origination_gen = _load_module(
    ORIGINATION_FN_DIR,
    "generate_markdown_reports.py",
    "_test_gen_origination",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / f"{name}.json") as f:
        return json.load(f)


def _transform_identity(fixture: dict) -> dict:
    """Extract the identity sub-dict in the shape expected by the generators."""
    identity = fixture["identity"]
    return {
        "seed": identity.get("seed", {}),
        "scored": identity.get("scored", {}),
        "contactability": identity.get("contactability", {}),
        "breaches": identity.get("breaches", []),
        "queries": identity.get("queries", []),
        "grounding_metadata": identity.get("grounding_metadata", {}),
    }


# Gravatar result stub (no real HTTP calls)
_GRAVATAR_NONE = None

# WHOIS / MX stubs
_WHOIS_OK = {"success": True, "registration_date": "2005-03-15"}
_MX_OK = {
    "success": True,
    "domain": "example.com",
    "mx_records": ["mx.example.com"],
    "provider_detected": "Google Workspace",
    "risk_level": "LOW",
    "status": "Legitimate Business Email",
}


# ---------------------------------------------------------------------------
# Skip Trace report tests
# ---------------------------------------------------------------------------
class TestSkipTraceReportGeneration:
    """Smoke tests for generate_identity_report_skiptrace."""

    @pytest.fixture(autouse=True)
    def _mock_network(self):
        """Mock all network-dependent helpers in the skiptrace generator."""
        with (
            patch.object(skiptrace_gen, "get_gravatar_profile", return_value=_GRAVATAR_NONE),
            patch.object(skiptrace_gen, "get_domain_registration_date", return_value=_WHOIS_OK),
            patch.object(skiptrace_gen, "check_domain_mx_records", return_value=_MX_OK),
        ):
            yield

    def test_rich_data(self, tmp_path):
        """Full data: 4 handles, 14 breaches, phone, address, domain enrichment."""
        fixture = _load_fixture("skiptrace_rich")
        data = _transform_identity(fixture)
        enrichment = fixture["enrichment"]
        company_domain = fixture["input"].get("company_domain")

        skiptrace_gen.generate_identity_report_skiptrace(
            data=data,
            name=fixture["input"]["full_name"],
            output_dir=tmp_path,
            company_domain=company_domain,
            enrichment_data=enrichment,
        )

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1, f"Expected 1 markdown file, got {len(md_files)}"
        content = md_files[0].read_text()
        assert len(content) > 500, "Report suspiciously short"
        assert fixture["input"]["full_name"] in content

    def test_null_handles(self, tmp_path):
        """Handles with null names must not crash (the production bug)."""
        fixture = _load_fixture("skiptrace_null_handles")
        data = _transform_identity(fixture)
        enrichment = fixture["enrichment"]
        company_domain = fixture["input"].get("company_domain")

        # Verify the fixture actually has null handles
        handles = data["scored"]["top_handles"]
        null_count = sum(1 for h in handles if h.get("handle") is None)
        assert null_count >= 1, "Fixture should contain at least one null handle"

        skiptrace_gen.generate_identity_report_skiptrace(
            data=data,
            name=fixture["input"]["full_name"],
            output_dir=tmp_path,
            company_domain=company_domain,
            enrichment_data=enrichment,
        )

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text()
        assert "None" not in content.split("---")[1], "Literal 'None' should not appear in snapshot section"

    def test_empty_enrichment(self, tmp_path):
        """No enrichment data at all — should degrade gracefully."""
        fixture = _load_fixture("skiptrace_rich")
        data = _transform_identity(fixture)

        skiptrace_gen.generate_identity_report_skiptrace(
            data=data,
            name=fixture["input"]["full_name"],
            output_dir=tmp_path,
            company_domain=None,
            enrichment_data=None,
        )

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1

    def test_no_handles_no_breaches(self, tmp_path):
        """Minimal data: strip handles and breaches entirely."""
        fixture = _load_fixture("skiptrace_rich")
        data = _transform_identity(fixture)
        data["scored"]["top_handles"] = []
        data["breaches"] = []
        data["queries"] = []

        skiptrace_gen.generate_identity_report_skiptrace(
            data=data,
            name=fixture["input"]["full_name"],
            output_dir=tmp_path,
        )

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1


# ---------------------------------------------------------------------------
# Origination report tests
# ---------------------------------------------------------------------------
class TestOriginationReportGeneration:
    """Smoke tests for generate_identity_report."""

    @pytest.fixture(autouse=True)
    def _mock_network(self):
        """Mock all network-dependent helpers in the origination generator."""
        with (
            patch.object(origination_gen, "get_gravatar_profile", return_value=_GRAVATAR_NONE),
            patch.object(origination_gen, "get_domain_registration_date", return_value=_WHOIS_OK),
            patch.object(origination_gen, "check_domain_mx_records", return_value=_MX_OK),
        ):
            yield

    def test_rich_data(self, tmp_path):
        """Full origination data: 2 handles, phone, address, domain enrichment."""
        fixture = _load_fixture("origination_rich")
        data = _transform_identity(fixture)
        enrichment = fixture["enrichment"]
        company_domain = fixture["input"].get("company_domain")

        origination_gen.generate_identity_report(
            data=data,
            name=fixture["input"]["full_name"],
            output_dir=tmp_path,
            company_domain=company_domain,
            enrichment_data=enrichment,
        )

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text()
        assert len(content) > 500
        assert fixture["input"]["full_name"] in content

    def test_null_handles_origination(self, tmp_path):
        """Inject null handles into origination data — must not crash."""
        fixture = _load_fixture("origination_rich")
        data = _transform_identity(fixture)
        enrichment = fixture["enrichment"]

        # Inject null handles to exercise the guard
        data["scored"]["top_handles"].append(
            {"platform": "instagram", "handle": None, "url": "https://instagram.com/p/test123/", "confidence": "high"}
        )

        origination_gen.generate_identity_report(
            data=data,
            name=fixture["input"]["full_name"],
            output_dir=tmp_path,
            enrichment_data=enrichment,
        )

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1

    def test_simplified_mode(self, tmp_path):
        """Simplified mode omits Contactability and Public Sector sections."""
        fixture = _load_fixture("origination_rich")
        data = _transform_identity(fixture)
        enrichment = fixture["enrichment"]

        origination_gen.generate_identity_report(
            data=data,
            name=fixture["input"]["full_name"],
            output_dir=tmp_path,
            enrichment_data=enrichment,
            simplified=True,
        )

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text()
        assert "Contact-ability" not in content

    def test_empty_enrichment(self, tmp_path):
        """No enrichment data — should degrade gracefully."""
        fixture = _load_fixture("origination_rich")
        data = _transform_identity(fixture)

        origination_gen.generate_identity_report(
            data=data,
            name=fixture["input"]["full_name"],
            output_dir=tmp_path,
            company_domain=None,
            enrichment_data=None,
        )

        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1
