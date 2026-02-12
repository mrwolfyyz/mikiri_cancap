"""Tests for the aggregator Cloud Function (gcp/functions/aggregator/main.py).

The aggregator has zero external dependencies — all functions are pure data
transformations.  We load the module via the conftest helper to avoid
collisions with other main.py files on sys.path.
"""

import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Mock functions_framework before loading the module (it's just a decorator)
# ---------------------------------------------------------------------------
_mock_ff = MagicMock()
_mock_ff.http = lambda f: f  # no-op decorator
sys.modules.setdefault("functions_framework", _mock_ff)

# ---------------------------------------------------------------------------
# Load aggregator/main.py via conftest helper
# ---------------------------------------------------------------------------
from conftest import load_function_module

aggregator_main = load_function_module("aggregator", "aggregator_main")

sanitize_for_json = aggregator_main.sanitize_for_json
compute_result_summary = aggregator_main.compute_result_summary
aggregate = aggregator_main.aggregate
main_handler = aggregator_main.main


# ---------------------------------------------------------------------------
# Helper: build a Flask-like mock request with a JSON body
# ---------------------------------------------------------------------------
def _make_request(body=None, *, bad_json=False):
    req = MagicMock()
    if bad_json:
        req.get_json.side_effect = Exception("bad JSON")
    else:
        req.get_json.return_value = body
    return req


# ===========================================================================
# sanitize_for_json
# ===========================================================================
class TestSanitizeForJson:
    """Tests for recursive JSON sanitization."""

    def test_none_passthrough(self):
        assert sanitize_for_json(None) is None

    def test_bytes_decoded(self):
        assert sanitize_for_json(b"hello") == "hello"

    def test_bytearray_decoded(self):
        assert sanitize_for_json(bytearray(b"world")) == "world"

    def test_dict_recursion(self):
        result = sanitize_for_json({"a": b"bytes", "b": {"c": 1}})
        assert result == {"a": "bytes", "b": {"c": 1}}

    def test_list_recursion(self):
        result = sanitize_for_json([b"a", [b"b"]])
        assert result == ["a", ["b"]]

    def test_tuple_converted_to_list(self):
        result = sanitize_for_json((1, 2, 3))
        assert result == [1, 2, 3]

    def test_primitives_passthrough(self):
        assert sanitize_for_json("str") == "str"
        assert sanitize_for_json(42) == 42
        assert sanitize_for_json(3.14) == 3.14
        assert sanitize_for_json(True) is True
        assert sanitize_for_json(False) is False

    def test_object_with_dunder_dict(self):
        class Dummy:
            def __init__(self):
                self.x = 1
                self.y = "two"

        result = sanitize_for_json(Dummy())
        assert result == {"x": 1, "y": "two"}

    def test_object_with_dunder_dict_takes_priority_over_decode(self):
        """Objects with __dict__ are serialized via __dict__, not decode."""

        class HasBoth:
            def __init__(self):
                self.val = 42

            def decode(self, *a, **kw):
                return "decoded"

        result = sanitize_for_json(HasBoth())
        # __dict__ branch wins over decode branch
        assert result == {"val": 42}

    def test_fallback_to_str(self):
        """Unknown types fall back to str()."""
        result = sanitize_for_json(frozenset([1, 2]))
        assert isinstance(result, str)

    def test_nested_bytes_in_complex_structure(self):
        data = {"items": [{"val": b"x"}, {"val": b"y"}]}
        result = sanitize_for_json(data)
        assert result == {"items": [{"val": "x"}, {"val": "y"}]}


# ===========================================================================
# compute_result_summary
# ===========================================================================
class TestComputeResultSummary:
    """Tests for high-level summary computation."""

    def test_no_errors_clear_status(self):
        result = compute_result_summary(identity={}, errors={})
        assert result["overall_status"] == "clear"
        assert result["partial_failure"] is False
        assert "complete" in result["headline"].lower()

    def test_with_errors_partial_failure(self):
        result = compute_result_summary(
            identity={},
            errors={"domain_enrichment": "timeout"},
        )
        assert result["overall_status"] == "partial_failure"
        assert result["partial_failure"] is True
        assert "partial" in result["headline"].lower()

    def test_high_confidence_identity_bullet(self):
        result = compute_result_summary(
            identity={"location": {"confidence": "high"}},
            errors={},
        )
        assert any("High-confidence" in b for b in result["bullets"])

    def test_non_high_confidence_no_bullet(self):
        result = compute_result_summary(
            identity={"location": {"confidence": "medium"}},
            errors={},
        )
        assert not any("High-confidence" in b for b in result["bullets"])

    def test_error_bullets_include_source(self):
        result = compute_result_summary(
            identity={},
            errors={"domain_enrichment": "timeout", "address_geocoding": "500 error"},
        )
        sources = [b for b in result["bullets"] if "Error" in b]
        assert len(sources) == 2
        assert any("domain_enrichment" in b for b in sources)
        assert any("address_geocoding" in b for b in sources)

    def test_bullets_capped_at_five(self):
        errors = {f"source_{i}": f"error_{i}" for i in range(10)}
        result = compute_result_summary(identity={}, errors=errors)
        assert len(result["bullets"]) <= 5

    def test_empty_identity(self):
        result = compute_result_summary(identity={}, errors={})
        assert result["overall_status"] == "clear"
        assert result["bullets"] == []

    def test_non_dict_location_no_crash(self):
        """If identity.location is not a dict, don't crash."""
        result = compute_result_summary(
            identity={"location": "Toronto"},
            errors={},
        )
        assert result["overall_status"] == "clear"


# ===========================================================================
# aggregate
# ===========================================================================
class TestAggregate:
    """Tests for the core aggregation function."""

    def test_all_inputs_present(self):
        result = aggregate(
            identity={"golden_name": "John Smith"},
            domain_enrichment={"domains": {"example.com": {}}},
            address_geocoding={"addresses": {"123 Main St": {}}},
            contact_extraction={"contacts": {"phones": [{"n": "555"}], "emails": [], "addresses": []}},
        )
        assert result["identity"]["golden_name"] == "John Smith"
        assert result["enrichment"]["domains"] == {"example.com": {}}
        assert result["enrichment"]["addresses"] == {"123 Main St": {}}
        assert len(result["enrichment"]["contacts"]["phones"]) == 1
        assert result["partial_failure"] is False
        assert result["errors"] == {}

    def test_null_domain_enrichment(self):
        result = aggregate(identity={"x": 1}, domain_enrichment=None)
        assert result["enrichment"]["domains"] == {}

    def test_null_address_geocoding(self):
        result = aggregate(identity={"x": 1}, address_geocoding=None)
        assert result["enrichment"]["addresses"] == {}

    def test_null_contact_extraction(self):
        result = aggregate(identity={"x": 1}, contact_extraction=None)
        assert result["enrichment"]["contacts"] == {
            "phones": [],
            "emails": [],
            "addresses": [],
        }

    def test_errors_filtered(self):
        """Errors with None or empty-string values are filtered out."""
        result = aggregate(
            identity={"x": 1},
            errors={
                "domain_enrichment": "timeout",
                "address_geocoding": None,
                "contact_extraction": "",
            },
        )
        assert result["errors"] == {"domain_enrichment": "timeout"}
        assert result["partial_failure"] is True

    def test_no_errors(self):
        result = aggregate(identity={"x": 1}, errors={})
        assert result["errors"] == {}
        assert result["partial_failure"] is False

    def test_result_summary_present(self):
        result = aggregate(identity={"x": 1})
        assert "result_summary" in result
        assert "overall_status" in result["result_summary"]

    def test_all_null_phase2(self):
        """All phase2 results null — should produce valid structure."""
        result = aggregate(
            identity={"golden_name": "Jane Doe"},
            domain_enrichment=None,
            address_geocoding=None,
            contact_extraction=None,
            errors={"domain_enrichment": "fail", "address_geocoding": "fail"},
        )
        assert result["enrichment"]["domains"] == {}
        assert result["enrichment"]["addresses"] == {}
        assert result["enrichment"]["contacts"]["phones"] == []
        assert result["partial_failure"] is True


# ===========================================================================
# main HTTP handler
# ===========================================================================
class TestMainHandler:
    """Tests for the HTTP entry point."""

    def test_valid_request(self):
        body = {
            "job_id": "abc123",
            "identity": {"golden_name": "John Smith"},
            "domain_enrichment": {"domains": {}},
            "address_geocoding": None,
            "errors": {},
        }
        result, status, _ = main_handler(_make_request(body))
        assert status == 200
        assert "identity" in result
        assert "enrichment" in result

    def test_invalid_json(self):
        result, status = main_handler(_make_request(bad_json=True))
        assert status == 400
        assert "error" in result

    def test_missing_identity(self):
        result, status = main_handler(_make_request({"job_id": "x"}))
        assert status == 400
        assert "identity" in result["error"].lower()

    def test_empty_identity_rejected(self):
        result, status = main_handler(_make_request({"identity": {}}))
        assert status == 400

    def test_minimal_valid_request(self):
        body = {"identity": {"name": "test"}}
        result, status, _ = main_handler(_make_request(body))
        assert status == 200

    def test_exception_returns_500(self):
        """If aggregate somehow raises, the handler returns 500."""
        req = _make_request({"identity": {"name": "test"}})
        # Temporarily break aggregate to force an exception
        original = aggregator_main.aggregate
        aggregator_main.aggregate = MagicMock(side_effect=RuntimeError("boom"))
        try:
            result, status = main_handler(req)
            assert status == 500
            assert "boom" in result["error"]
            assert result["error_type"] == "RuntimeError"
        finally:
            aggregator_main.aggregate = original
