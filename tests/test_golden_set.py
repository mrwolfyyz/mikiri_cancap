"""
Golden set integration tests.

Runs real investigations against a live deployment and validates results
against expected outcomes using structured Firestore data. These tests are
slow (60-180s each) and require a running deployment with valid credentials.

Usage:
    pytest tests/test_golden_set.py -v \
        --golden-url=https://<region>-<project>.cloudfunctions.net/api_gateway \
        --golden-token=<firebase-id-token>

Skip in CI (unit tests only):
    pytest tests/ -v --ignore=tests/test_golden_set.py
"""

import json
import time
from pathlib import Path

import pytest
import requests
from google.cloud import firestore


# ---------------------------------------------------------------------------
# CLI options
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption("--golden-url", action="store", default=None, help="API Gateway base URL")
    parser.addoption("--golden-token", action="store", default=None, help="Firebase ID token")


@pytest.fixture(scope="session")
def api_url(request):
    url = request.config.getoption("--golden-url")
    if not url:
        pytest.skip("--golden-url not provided")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def auth_token(request):
    token = request.config.getoption("--golden-token")
    if not token:
        pytest.skip("--golden-token not provided")
    return token


@pytest.fixture(scope="session")
def firestore_client():
    return firestore.Client()


@pytest.fixture(scope="session")
def golden_cases():
    path = Path(__file__).parent / "golden_set.json"
    data = json.loads(path.read_text())
    return data["cases"]


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
# Tests
# ---------------------------------------------------------------------------

class TestGoldenSet:
    """Run each golden set case as a separate test."""

    def test_golden_cases(self, api_url, auth_token, firestore_client, golden_cases):
        """Run all golden set cases and collect results."""
        results = []

        for case in golden_cases:
            case_id = case["id"]
            assertions = case["assertions"]
            failures = []

            print(f"\n{'='*60}")
            print(f"Running: {case_id} — {case.get('description', '')}")
            print(f"{'='*60}")

            # Submit investigation
            job_id = submit_investigation(api_url, auth_token, case["input"])
            print(f"  Job submitted: {job_id}")

            # Wait for completion
            job = poll_until_complete(api_url, auth_token, job_id)
            print(f"  Job completed")

            if assertions.get("should_complete"):
                if job["status"] != "complete":
                    failures.append("Job did not complete")

            # Read structured result from Firestore
            result = get_firestore_result(firestore_client, job_id)

            # Phone assertions
            if assertions.get("expect_phone_found"):
                failures.extend(assert_phones_found(result))

            # Email assertions
            if assertions.get("expect_email_found"):
                failures.extend(assert_emails_found(result))

            # Address assertions
            if assertions.get("expect_address_found"):
                failures.extend(assert_addresses_found(result))

            # Breach assertions
            if assertions.get("expect_breaches_found"):
                failures.extend(assert_breaches_found(result))

            # Social profile assertions
            if assertions.get("expect_social_profiles"):
                failures.extend(
                    assert_social_profiles(result, assertions["expect_social_profiles"])
                )

            # Company domain assertions
            if assertions.get("expect_company_domain_resolved"):
                failures.extend(assert_company_domain_resolved(result))

            # Report term assertions (search the full result JSON)
            if assertions.get("report_should_contain"):
                failures.extend(
                    assert_report_terms(result, assertions["report_should_contain"])
                )

            # Record result
            status = "PASS" if not failures else "FAIL"
            print(f"  Result: {status}")
            if failures:
                for f in failures:
                    print(f"    - {f}")

            results.append({
                "case_id": case_id,
                "job_id": job_id,
                "passed": len(failures) == 0,
                "failures": failures,
            })

        # Summary
        print(f"\n{'='*60}")
        print(f"Golden Set Summary: {sum(1 for r in results if r['passed'])}/{len(results)} passed")
        print(f"{'='*60}")

        failed = [r for r in results if not r["passed"]]
        if failed:
            msg = "\n".join(
                f"  {r['case_id']} (job {r['job_id']}): {'; '.join(r['failures'])}"
                for r in failed
            )
            pytest.fail(f"Golden set failures:\n{msg}")
