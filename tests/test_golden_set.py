"""
Golden set integration tests.

Runs real investigations against a live deployment and validates results
against expected outcomes. These tests are slow (30-120s each) and require
a running deployment.

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
def golden_cases():
    path = Path(__file__).parent / "golden_set.json"
    data = json.loads(path.read_text())
    return data["cases"]


# ---------------------------------------------------------------------------
# Helpers
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


def get_markdown_report(api_url: str, token: str, job_id: str) -> dict:
    """Fetch markdown reports for a completed job."""
    resp = requests.get(
        f"{api_url}/get_markdown/{job_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGoldenSet:
    """Run each golden set case as an integration test."""

    def test_golden_cases(self, api_url, auth_token, golden_cases):
        """Run all golden set cases and report results."""
        results = []

        for case in golden_cases:
            case_id = case["id"]
            assertions = case["assertions"]

            # Submit investigation
            job_id = submit_investigation(api_url, auth_token, case["input"])

            # Wait for completion
            job = poll_until_complete(api_url, auth_token, job_id)
            assert job["status"] == "complete", f"[{case_id}] Job did not complete"

            # Fetch markdown report
            report = get_markdown_report(api_url, auth_token, job_id)
            report_text = " ".join(report.values()) if isinstance(report, dict) else str(report)

            # Check assertions
            passed = True
            failures = []

            if assertions.get("should_complete"):
                assert job["status"] == "complete"

            if assertions.get("report_should_contain"):
                for term in assertions["report_should_contain"]:
                    if term.lower() not in report_text.lower():
                        failures.append(f"Report missing expected term: '{term}'")
                        passed = False

            results.append({
                "case_id": case_id,
                "job_id": job_id,
                "passed": passed,
                "failures": failures,
            })

        # Summary
        failed = [r for r in results if not r["passed"]]
        if failed:
            msg = "\n".join(
                f"  {r['case_id']}: {'; '.join(r['failures'])}"
                for r in failed
            )
            pytest.fail(f"Golden set failures:\n{msg}")
