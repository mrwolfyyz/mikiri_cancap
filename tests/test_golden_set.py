"""
Golden set integration tests.

Runs real investigations against a live deployment and validates results
against expected outcomes using structured Firestore data and markdown
reports. These tests are slow (60-180s each) and require a running
deployment with GCP credentials (``gcloud auth application-default login``).

Credentials are auto-discovered from ``frontend/skiptrace/public/firebase-config.json``
(API URL and key) and a Firebase anonymous auth token is generated automatically.
CLI overrides are available if needed.

Each golden case is parameterized as its own test, so you can run
individual cases:

    # Run all golden cases
    python3.13 -m pytest tests/test_golden_set.py -v

    # Run a single case
    python3.13 -m pytest tests/test_golden_set.py -v -k sari_cornfield

    # Run and save markdown reports to disk for inspection
    python3.13 -m pytest tests/test_golden_set.py -v --golden-save-reports

    # Override auto-discovered URL or token (optional)
    python3.13 -m pytest tests/test_golden_set.py -v \
        --golden-url=https://... --golden-token=...

Skip in CI (unit tests only):
    pytest tests/ -v --ignore=tests/test_golden_set.py
"""

import json
import time
from datetime import datetime
from pathlib import Path

import pytest
import requests
from google.cloud import firestore

# ---------------------------------------------------------------------------
# Auto-discovery helpers
# ---------------------------------------------------------------------------

_FIREBASE_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent
    / "frontend" / "skiptrace" / "public" / "firebase-config.json"
)


def _load_firebase_config() -> dict:
    """Load firebase-config.json from the repo."""
    if not _FIREBASE_CONFIG_PATH.exists():
        pytest.fail(
            f"firebase-config.json not found at {_FIREBASE_CONFIG_PATH}. "
            f"Run 'terraform apply' to generate it, or pass --golden-url and --golden-token."
        )
    return json.loads(_FIREBASE_CONFIG_PATH.read_text())


def _get_anonymous_token(api_key: str) -> str:
    """Generate a Firebase anonymous auth ID token via the REST API."""
    resp = requests.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={api_key}",
        headers={"Content-Type": "application/json"},
        json={"returnSecureToken": True},
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json().get("idToken")
    if not token:
        pytest.fail(f"Firebase anonymous sign-up did not return idToken: {resp.json()}")
    return token


# ---------------------------------------------------------------------------
# Load golden cases at module level for parameterization
# ---------------------------------------------------------------------------

_golden_cases = json.loads(
    (Path(__file__).parent / "golden_set.json").read_text()
)["cases"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def api_url(request):
    """API Gateway URL — from CLI or auto-discovered from firebase-config.json."""
    url = request.config.getoption("--golden-url")
    if not url:
        config = _load_firebase_config()
        url = config.get("apiUrl")
        if not url:
            pytest.fail("apiUrl not found in firebase-config.json")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def auth_token(request):
    """Firebase ID token — from CLI or auto-generated via anonymous auth."""
    token = request.config.getoption("--golden-token")
    if not token:
        config = _load_firebase_config()
        api_key = config.get("apiKey")
        if not api_key:
            pytest.fail("apiKey not found in firebase-config.json")
        token = _get_anonymous_token(api_key)
    return token


@pytest.fixture(scope="session")
def firestore_client():
    return firestore.Client()


@pytest.fixture(scope="session")
def save_reports_dir(request):
    """Return a directory path for saving markdown reports, or None."""
    if not request.config.getoption("--golden-save-reports"):
        return None
    out_dir = (
        Path(__file__).parent
        / "golden_reports"
        / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def submit_investigation(api_url: str, token: str, case_input: dict) -> str:
    """Submit an investigation and return the job_id."""
    resp = requests.post(
        f"{api_url}/investigate-skiptrace",
        json=case_input,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["job_id"]


def poll_until_complete(api_url: str, token: str, job_id: str, max_wait: int = 180) -> dict:
    """Poll job status until complete or timeout."""
    start = time.time()
    while time.time() - start < max_wait:
        resp = requests.get(
            f"{api_url}/jobs/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        job = resp.json()

        if job["status"] == "complete":
            return job
        if job["status"] == "failed":
            pytest.fail(f"Job {job_id} failed: {job.get('error')}")

        time.sleep(5)

    pytest.fail(f"Job {job_id} did not complete within {max_wait}s")


def get_firestore_result(client: firestore.Client, job_id: str) -> dict:
    """Read the structured result from Firestore for a completed job."""
    doc = client.collection("jobs").document(job_id).get()
    if not doc.exists:
        pytest.fail(f"Job document {job_id} not found in Firestore")

    doc_data = doc.to_dict()
    result_str = doc_data.get("result", "{}")

    if isinstance(result_str, str):
        try:
            return json.loads(result_str)
        except json.JSONDecodeError as e:
            pytest.fail(f"Failed to parse result JSON for {job_id}: {e}")
    elif isinstance(result_str, dict):
        return result_str

    pytest.fail(f"Unexpected result type for {job_id}: {type(result_str)}")


def get_markdown_report(api_url: str, token: str, job_id: str) -> dict:
    """Fetch the markdown reports for a completed job.

    Returns a dict like ``{"identity": "# Identity Report\\n..."}``.
    """
    resp = requests.get(
        f"{api_url}/get_markdown/{job_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Assertion helpers — structured Firestore data
# ---------------------------------------------------------------------------

def assert_phones_found(result: dict) -> list:
    """Check that at least one phone number was found."""
    phones = result.get("enrichment", {}).get("contacts", {}).get("phones", [])
    if not phones:
        return ["Expected phone number(s) but none found in enrichment.contacts.phones"]
    return []


def assert_emails_found(result: dict) -> list:
    """Check that at least one email was discovered (beyond the input email)."""
    emails = result.get("enrichment", {}).get("contacts", {}).get("emails", [])
    if not emails:
        return ["Expected discovered email(s) but none found in enrichment.contacts.emails"]
    return []


def assert_addresses_found(result: dict) -> list:
    """Check that at least one address was found."""
    addresses = result.get("enrichment", {}).get("contacts", {}).get("addresses", [])
    if not addresses:
        return ["Expected address(es) but none found in enrichment.contacts.addresses"]
    return []


def assert_breaches_found(result: dict) -> list:
    """Check that at least one breach record was found."""
    breaches = result.get("identity", {}).get("breaches", [])
    if not breaches:
        return ["Expected breach data but none found in identity.breaches"]
    return []


def assert_social_profiles(result: dict, expected_platforms: list) -> list:
    """Check that expected social media platforms appear in top_handles."""
    handles = result.get("identity", {}).get("scored", {}).get("top_handles", [])
    found_platforms = {h.get("platform", "").lower() for h in handles}

    failures = []
    for platform in expected_platforms:
        if platform.lower() not in found_platforms:
            failures.append(
                f"Expected social profile '{platform}' not found. "
                f"Found: {sorted(found_platforms)}"
            )
    return failures


def assert_company_domain_resolved(result: dict) -> list:
    """Check that at least one company domain was resolved with data."""
    domains = result.get("enrichment", {}).get("domains", {})
    if not domains:
        return ["Expected company domain data but enrichment.domains is empty"]

    # Check that at least one domain has whois or mx data
    for domain, data in domains.items():
        if isinstance(data, dict) and (data.get("whois") or data.get("mx")):
            return []

    return ["Company domain(s) found but none have whois or mx data"]


def assert_report_terms(result: dict, terms: list) -> list:
    """Check that expected terms appear somewhere in the full result JSON."""
    result_str = json.dumps(result).lower()
    failures = []
    for term in terms:
        if term.lower() not in result_str:
            failures.append(f"Expected term '{term}' not found in result data")
    return failures


# ---------------------------------------------------------------------------
# Assertion helpers — markdown report
# ---------------------------------------------------------------------------

def assert_markdown_report_exists(markdown: dict) -> list:
    """Check that the identity markdown report was generated and is non-empty."""
    failures = []
    identity_md = markdown.get("identity", "")
    if not identity_md:
        failures.append("Markdown report missing: 'identity' key is empty or absent")
    elif len(identity_md) < 100:
        failures.append(
            f"Markdown report suspiciously short ({len(identity_md)} chars)"
        )
    return failures


def assert_markdown_contains_terms(markdown: dict, terms: list) -> list:
    """Check that expected terms appear in the identity markdown report."""
    identity_md = markdown.get("identity", "").lower()
    failures = []
    for term in terms:
        if term.lower() not in identity_md:
            failures.append(
                f"Expected term '{term}' not found in markdown report"
            )
    return failures


# ---------------------------------------------------------------------------
# Parameterized test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "case",
    _golden_cases,
    ids=[c["id"] for c in _golden_cases],
)
def test_golden_case(api_url, auth_token, firestore_client, save_reports_dir, case):
    """Run a single golden set case end-to-end and validate results."""
    case_id = case["id"]
    assertions = case["assertions"]
    failures = []

    print(f"\n{'='*60}")
    print(f"Running: {case_id} — {case.get('description', '')}")
    print(f"{'='*60}")

    # ----- Submit & wait -----
    job_id = submit_investigation(api_url, auth_token, case["input"])
    print(f"  Job submitted: {job_id}")

    job = poll_until_complete(api_url, auth_token, job_id)
    print(f"  Job completed")

    if assertions.get("should_complete"):
        if job["status"] != "complete":
            failures.append("Job did not complete")

    # ----- Structured result assertions -----
    result = get_firestore_result(firestore_client, job_id)

    if assertions.get("expect_phone_found"):
        failures.extend(assert_phones_found(result))

    if assertions.get("expect_email_found"):
        failures.extend(assert_emails_found(result))

    if assertions.get("expect_address_found"):
        failures.extend(assert_addresses_found(result))

    if assertions.get("expect_breaches_found"):
        failures.extend(assert_breaches_found(result))

    if assertions.get("expect_social_profiles"):
        failures.extend(
            assert_social_profiles(result, assertions["expect_social_profiles"])
        )

    if assertions.get("expect_company_domain_resolved"):
        failures.extend(assert_company_domain_resolved(result))

    if assertions.get("report_should_contain"):
        failures.extend(
            assert_report_terms(result, assertions["report_should_contain"])
        )

    # ----- Markdown report assertions -----
    markdown = get_markdown_report(api_url, auth_token, job_id)
    print(f"  Markdown retrieved ({len(markdown.get('identity', ''))} chars)")

    failures.extend(assert_markdown_report_exists(markdown))

    # Check report_should_contain terms also appear in the markdown
    if assertions.get("report_should_contain"):
        failures.extend(
            assert_markdown_contains_terms(markdown, assertions["report_should_contain"])
        )

    # Check any markdown-specific terms
    if assertions.get("markdown_should_contain"):
        failures.extend(
            assert_markdown_contains_terms(
                markdown, assertions["markdown_should_contain"]
            )
        )

    # ----- Optionally save markdown to disk -----
    if save_reports_dir and markdown.get("identity"):
        report_path = save_reports_dir / f"{case_id}.md"
        report_path.write_text(markdown["identity"])
        print(f"  Markdown saved: {report_path}")

    # ----- Final result -----
    if failures:
        for f in failures:
            print(f"    FAIL: {f}")
        pytest.fail(
            f"Golden case '{case_id}' (job {job_id}) — "
            f"{len(failures)} failure(s):\n"
            + "\n".join(f"  - {f}" for f in failures)
        )

    print(f"  Result: PASS")
