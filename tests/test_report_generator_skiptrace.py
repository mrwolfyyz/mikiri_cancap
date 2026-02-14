"""Tests for the report_generator_skiptrace Cloud Function.

Covers:
- transform_firestore_to_report_format (pure data transform)
- on_job_updated (Firestore event handler: guards, happy path, Drive upload, error handling)
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock heavy dependencies BEFORE loading the module
# ---------------------------------------------------------------------------

# functions_framework — cloud_event decorator (not http)
# Must force-set cloud_event attribute even if functions_framework was already
# mocked by another test file (setdefault won't overwrite).
_ff = sys.modules.get("functions_framework")
if _ff is None:
    _ff = MagicMock()
    sys.modules["functions_framework"] = _ff
_ff.cloud_event = lambda f: f  # no-op decorator for @cloud_event
_ff.http = lambda f: f  # no-op decorator for @functions_framework.http (prevents poisoning other test files)

# cloudevents
_mock_cloudevents = MagicMock()
_mock_cloudevents_http = MagicMock()
sys.modules.setdefault("cloudevents", _mock_cloudevents)
sys.modules.setdefault("cloudevents.http", _mock_cloudevents_http)

# firebase_admin — module-level initialize_app() and firestore.client()
_mock_fb_admin = MagicMock()
_mock_fb_firestore = MagicMock()
_mock_fb_admin.firestore = _mock_fb_firestore
_mock_fb_admin.initialize_app = MagicMock()
sys.modules.setdefault("firebase_admin", _mock_fb_admin)
sys.modules.setdefault("firebase_admin.firestore", _mock_fb_firestore)

# google.auth
_mock_google_auth = MagicMock()
sys.modules.setdefault("google.auth", _mock_google_auth)


# googleapiclient — HttpError must be a real Exception subclass
class _HttpError(Exception):
    """Mock HttpError that supports resp.status access."""

    def __init__(self, resp=None, content=b"", uri=""):
        self.resp = resp or MagicMock(status=500)
        self.content = content
        self.uri = uri
        super().__init__(str(content))


_mock_googleapiclient = MagicMock()
_mock_googleapiclient_discovery = MagicMock()
_mock_googleapiclient_http = MagicMock()
_mock_googleapiclient_errors = MagicMock()
_mock_googleapiclient_errors.HttpError = _HttpError
sys.modules.setdefault("googleapiclient", _mock_googleapiclient)
sys.modules.setdefault("googleapiclient.discovery", _mock_googleapiclient_discovery)
sys.modules.setdefault("googleapiclient.http", _mock_googleapiclient_http)
sys.modules.setdefault("googleapiclient.errors", _mock_googleapiclient_errors)

# Mock google.events.cloud.firestore (for CloudEvent payload parsing)
_mock_firestore_events = MagicMock()
sys.modules.setdefault("google.events", MagicMock())
sys.modules.setdefault("google.events.cloud", MagicMock())
sys.modules.setdefault("google.events.cloud.firestore", _mock_firestore_events)

# Mock the local markdown generator module
_mock_md_gen = MagicMock()
sys.modules["generate_markdown_reports_skiptrace"] = _mock_md_gen

# ---------------------------------------------------------------------------
# Load report_generator_skiptrace/main.py
# ---------------------------------------------------------------------------
from conftest import load_function_module

rgs_main = load_function_module("report_generator_skiptrace", "report_generator_skiptrace_main")

transform_firestore_to_report_format = rgs_main.transform_firestore_to_report_format
on_job_updated = rgs_main.on_job_updated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_cloud_event(document_path="projects/p/databases/d/documents/jobs/job123", data=None):
    """Build a mock CloudEvent with Firestore document path."""
    event = MagicMock()
    event.get_attributes.return_value = {"document": document_path}
    event.data = data
    return event


def _setup_payload_mock(status=None, workflow_type=None, parse_error=False):
    """Configure the firestore_events mock for CloudEvent payload parsing."""
    mock_payload = MagicMock()
    if parse_error:
        mock_payload._pb.ParseFromString.side_effect = Exception("bad protobuf")
    else:
        fields = {}
        if status is not None:
            status_field = MagicMock()
            status_field.string_value = status
            fields["status"] = status_field
        if workflow_type is not None:
            wf_field = MagicMock()
            wf_field.string_value = workflow_type
            fields["workflow_type"] = wf_field
        mock_payload.value.fields = fields

    rgs_main.firestore_events.DocumentEventData.return_value = mock_payload
    return mock_payload


def _mock_firestore_doc(data, exists=True):
    """Build a mock Firestore document snapshot."""
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict.return_value = data
    return doc


def _sample_job_data():
    """Return a minimal valid job document for skip trace."""
    identity_data = {
        "identity": {
            "seed": {"full_name": "John Doe", "email": "john@example.com"},
            "scored": {"top_handles": []},
            "contactability": {"score": "Good"},
            "breaches": [],
            "queries": [],
            "grounding_metadata": {},
        },
        "enrichment": {
            "domains": {},
            "addresses": {},
            "contacts": {"phones": [], "emails": [], "addresses": []},
        },
    }
    return {
        "status": "post_processing",
        "workflow_type": "skiptrace",
        "input": {
            "full_name": "John Doe",
            "drive_folder_id": "folder123",
        },
        "result": json.dumps(identity_data),
    }


# ===========================================================================
# transform_firestore_to_report_format
# ===========================================================================
class TestTransformFirestoreToReportFormat:
    """Tests for the data transformation from Firestore to report format."""

    def test_full_data(self):
        firestore_data = {
            "identity": {
                "seed": {"full_name": "John Doe"},
                "scored": {"top_handles": [{"handle": "jdoe"}]},
                "contactability": {"score": "Good"},
                "breaches": [{"name": "Adobe"}],
                "queries": [{"id": "precision"}],
                "grounding_metadata": {"sources": []},
            }
        }
        result = transform_firestore_to_report_format(firestore_data)
        assert result["seed"]["full_name"] == "John Doe"
        assert result["scored"]["top_handles"][0]["handle"] == "jdoe"
        assert result["contactability"]["score"] == "Good"
        assert len(result["breaches"]) == 1
        assert len(result["queries"]) == 1

    def test_empty_data(self):
        result = transform_firestore_to_report_format({})
        assert result["seed"] == {}
        assert result["scored"] == {}
        assert result["contactability"] == {}
        assert result["breaches"] == []
        assert result["queries"] == []

    def test_partial_data(self):
        result = transform_firestore_to_report_format({"identity": {"seed": {"name": "Jane"}}})
        assert result["seed"]["name"] == "Jane"
        assert result["scored"] == {}


# ===========================================================================
# on_job_updated
# ===========================================================================
class TestOnJobUpdated:
    """Tests for the Firestore event handler."""

    @pytest.fixture(autouse=True)
    def _reset_payload_mock(self):
        """Reset DocumentEventData mock before each test to prevent state leakage."""
        rgs_main.firestore_events.DocumentEventData.reset_mock()
        rgs_main.firestore_events.DocumentEventData.return_value = MagicMock(
            _pb=MagicMock(ParseFromString=MagicMock(side_effect=Exception("no payload configured")))
        )

    def test_missing_document_attribute(self):
        """Event without 'document' attribute should return early."""
        event = MagicMock()
        event.get_attributes.return_value = {}
        # Should not raise
        on_job_updated(event)

    def test_unparseable_document_path(self):
        event = _make_cloud_event("some/random/path")
        on_job_updated(event)

    def test_document_not_found(self):
        mock_doc = _mock_firestore_doc(None, exists=False)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        with patch.object(rgs_main, "firestore_client", mock_client):
            on_job_updated(_make_cloud_event())

    def test_wrong_workflow_type_skipped(self):
        data = _sample_job_data()
        data["workflow_type"] = "origination"

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        with patch.object(rgs_main, "firestore_client", mock_client):
            on_job_updated(_make_cloud_event())

        # Should not proceed to report generation
        _mock_md_gen.generate_identity_report_skiptrace.assert_not_called()

    def test_wrong_status_skipped(self):
        data = _sample_job_data()
        data["status"] = "running"

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        with patch.object(rgs_main, "firestore_client", mock_client):
            on_job_updated(_make_cloud_event())

        _mock_md_gen.generate_identity_report_skiptrace.assert_not_called()

    def test_empty_document_data(self):
        mock_doc = _mock_firestore_doc(None, exists=True)
        mock_doc.to_dict.return_value = None
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        with patch.object(rgs_main, "firestore_client", mock_client):
            on_job_updated(_make_cloud_event())

    def test_invalid_result_json(self):
        data = _sample_job_data()
        data["result"] = "not valid json {"

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        with patch.object(rgs_main, "firestore_client", mock_client):
            on_job_updated(_make_cloud_event())

    def test_happy_path_no_drive(self):
        """Happy path without Drive upload (no drive_folder_id)."""
        data = _sample_job_data()
        data["input"]["drive_folder_id"] = ""

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            # Create a fake markdown file
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Identity Report\nTest content")

        with patch.object(rgs_main, "firestore_client", mock_client), patch.object(rgs_main, "md_gen") as mock_md:
            mock_md.generate_identity_report_skiptrace = fake_generate
            on_job_updated(_make_cloud_event())

        # Verify Firestore was updated to 'complete'
        mock_ref.set.assert_called_once()
        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"
        assert update_data["reports_generated"] is True
        assert "identity" in update_data.get("markdown_reports", {})

    def test_happy_path_with_drive_upload(self):
        """Happy path with Drive upload."""
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Identity Report\nTest content")

        mock_drive_service = MagicMock()

        with (
            patch.object(rgs_main, "firestore_client", mock_client),
            patch.object(rgs_main, "md_gen") as mock_md,
            patch.object(rgs_main, "get_drive_service", return_value=mock_drive_service),
            patch.object(rgs_main, "find_or_create_folder", return_value="subfolder123"),
            patch.object(
                rgs_main,
                "upload_single_file",
                return_value=(
                    "Identity___John_Doe.md",
                    {"id": "file1", "webViewLink": "https://drive.google.com/file1"},
                ),
            ),
        ):
            mock_md.generate_identity_report_skiptrace = fake_generate
            on_job_updated(_make_cloud_event())

        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"
        assert "reports_folder_link" in update_data

    def test_drive_404_handled_gracefully(self):
        """Drive folder not found → reports generated, upload skipped."""
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Report")

        mock_resp = MagicMock(status=404)
        drive_error = _HttpError(resp=mock_resp, content=b"Not Found")

        with (
            patch.object(rgs_main, "firestore_client", mock_client),
            patch.object(rgs_main, "md_gen") as mock_md,
            patch.object(rgs_main, "get_drive_service", side_effect=drive_error),
        ):
            mock_md.generate_identity_report_skiptrace = fake_generate
            on_job_updated(_make_cloud_event())

        # Should still complete (graceful degradation)
        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"

    def test_report_generation_failure_updates_status(self):
        """Exception during report generation → status set to failed."""
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        with patch.object(rgs_main, "firestore_client", mock_client), patch.object(rgs_main, "md_gen") as mock_md:
            mock_md.generate_identity_report_skiptrace.side_effect = RuntimeError("generation failed")
            on_job_updated(_make_cloud_event())

        # Should update to failed status
        last_set_call = mock_ref.set.call_args[0][0]
        assert last_set_call["status"] == "failed_report_generation"
        assert "generation failed" in last_set_call["error_message"]

    def test_result_as_dict_not_string(self):
        """Result can be a dict (not JSON string) in Firestore."""
        data = _sample_job_data()
        data["result"] = json.loads(data["result"])  # Convert to dict

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Report")

        with patch.object(rgs_main, "firestore_client", mock_client), patch.object(rgs_main, "md_gen") as mock_md:
            mock_md.generate_identity_report_skiptrace = fake_generate
            on_job_updated(_make_cloud_event())

        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"

    def test_job_id_from_jobs_prefix(self):
        """Extract job_id from 'jobs/abc' format."""
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Report")

        with patch.object(rgs_main, "firestore_client", mock_client), patch.object(rgs_main, "md_gen") as mock_md:
            mock_md.generate_identity_report_skiptrace = fake_generate
            on_job_updated(_make_cloud_event("jobs/abc123"))

        # Verify document lookup used "abc123"
        mock_client.collection.return_value.document.assert_called_with("abc123")

    # --- Early exit via CloudEvent payload tests ---

    def test_early_exit_wrong_workflow_from_payload(self):
        """Payload with wrong workflow_type exits before Firestore read."""
        _setup_payload_mock(workflow_type="origination", status="post_processing")
        mock_client = MagicMock()

        with patch.object(rgs_main, "firestore_client", mock_client):
            on_job_updated(_make_cloud_event(data=b"fake-protobuf"))

        mock_client.collection.assert_not_called()

    def test_early_exit_wrong_status_from_payload(self):
        """Payload with wrong status exits before Firestore read."""
        _setup_payload_mock(workflow_type="skiptrace", status="running")
        mock_client = MagicMock()

        with patch.object(rgs_main, "firestore_client", mock_client):
            on_job_updated(_make_cloud_event(data=b"fake-protobuf"))

        mock_client.collection.assert_not_called()

    def test_payload_parse_failure_falls_through(self):
        """When payload parsing fails, function falls through to Firestore read."""
        _setup_payload_mock(parse_error=True)
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Report")

        with patch.object(rgs_main, "firestore_client", mock_client), patch.object(rgs_main, "md_gen") as mock_md:
            mock_md.generate_identity_report_skiptrace = fake_generate
            on_job_updated(_make_cloud_event(data=b"garbage"))

        # Should have proceeded to Firestore read and completed
        mock_client.collection.assert_called()
        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"

    def test_payload_matches_proceeds_to_firestore(self):
        """When payload fields match, function proceeds to Firestore read."""
        _setup_payload_mock(workflow_type="skiptrace", status="post_processing")
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Report")

        with patch.object(rgs_main, "firestore_client", mock_client), patch.object(rgs_main, "md_gen") as mock_md:
            mock_md.generate_identity_report_skiptrace = fake_generate
            on_job_updated(_make_cloud_event(data=b"fake-protobuf"))

        mock_client.collection.assert_called()
        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"
