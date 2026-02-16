"""Tests for the report_generator_origination Cloud Function.

Covers:
- transform_firestore_to_report_format (pure data transform)
- on_job_updated (Firestore event handler: guards, happy path, Drive upload, error handling)
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock heavy dependencies BEFORE loading the module
# ---------------------------------------------------------------------------

# functions_framework — cloud_event decorator
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
sys.modules["generate_markdown_reports"] = _mock_md_gen

# ---------------------------------------------------------------------------
# Load report_generator_origination/main.py
# ---------------------------------------------------------------------------
from conftest import load_function_module

rgo_main = load_function_module("report_generator_origination", "report_generator_origination_main")

transform_firestore_to_report_format = rgo_main.transform_firestore_to_report_format
on_job_updated = rgo_main.on_job_updated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_cloud_event(document_path="projects/p/databases/d/documents/jobs/job456", data=None):
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

    rgo_main.firestore_events.DocumentEventData.return_value = mock_payload
    return mock_payload


def _mock_firestore_doc(data, exists=True):
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict.return_value = data
    return doc


def _sample_job_data():
    """Return a minimal valid job document for origination."""
    identity_data = {
        "identity": {
            "seed": {"full_name": "Jane Smith", "email": "jane@example.com"},
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
        "workflow_type": "origination",
        "input": {
            "full_name": "Jane Smith",
            "drive_folder_id": "folder456",
        },
        "result": json.dumps(identity_data),
    }


# ===========================================================================
# transform_firestore_to_report_format
# ===========================================================================
class TestTransformFirestoreToReportFormat:
    def test_full_data(self):
        firestore_data = {
            "identity": {
                "seed": {"full_name": "Jane Smith"},
                "scored": {"top_handles": []},
                "contactability": {"score": "Good"},
                "breaches": [],
                "queries": [],
                "grounding_metadata": {},
            }
        }
        result = transform_firestore_to_report_format(firestore_data)
        assert result["seed"]["full_name"] == "Jane Smith"
        assert result["contactability"]["score"] == "Good"

    def test_empty_data(self):
        result = transform_firestore_to_report_format({})
        assert result["seed"] == {}
        assert result["scored"] == {}
        assert result["breaches"] == []


# ===========================================================================
# on_job_updated
# ===========================================================================
class TestOnJobUpdated:
    @pytest.fixture(autouse=True)
    def _reset_payload_mock(self):
        """Reset DocumentEventData mock before each test to prevent state leakage."""
        rgo_main.firestore_events.DocumentEventData.reset_mock()
        rgo_main.firestore_events.DocumentEventData.return_value = MagicMock(
            _pb=MagicMock(ParseFromString=MagicMock(side_effect=Exception("no payload configured")))
        )

    def test_missing_document_attribute(self):
        event = MagicMock()
        event.get_attributes.return_value = {}
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

        with patch.object(rgo_main, "firestore_client", mock_client):
            on_job_updated(_make_cloud_event())

    def test_wrong_workflow_type_skipped(self):
        """Only processes workflow_type == 'origination'."""
        data = _sample_job_data()
        data["workflow_type"] = "skiptrace"

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        with patch.object(rgo_main, "firestore_client", mock_client):
            on_job_updated(_make_cloud_event())

        _mock_md_gen.generate_identity_report.assert_not_called()

    def test_wrong_status_skipped(self):
        data = _sample_job_data()
        data["status"] = "running"

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        with patch.object(rgo_main, "firestore_client", mock_client):
            on_job_updated(_make_cloud_event())

        _mock_md_gen.generate_identity_report.assert_not_called()

    def test_empty_document_data(self):
        mock_doc = _mock_firestore_doc(None, exists=True)
        mock_doc.to_dict.return_value = None
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        with patch.object(rgo_main, "firestore_client", mock_client):
            on_job_updated(_make_cloud_event())

    def test_invalid_result_json(self):
        data = _sample_job_data()
        data["result"] = "not valid json {"

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        with patch.object(rgo_main, "firestore_client", mock_client):
            on_job_updated(_make_cloud_event())

    def test_happy_path_no_drive(self):
        """Happy path without Drive upload."""
        data = _sample_job_data()
        data["input"]["drive_folder_id"] = ""

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            # Verify simplified=True is passed
            assert kwargs.get("simplified") is True
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Identity Report\nTest content")

        with patch.object(rgo_main, "firestore_client", mock_client), patch.object(rgo_main, "md_gen") as mock_md:
            mock_md.generate_identity_report = fake_generate
            on_job_updated(_make_cloud_event())

        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"
        assert update_data["reports_generated"] is True
        assert "identity" in update_data.get("markdown_reports", {})

    def test_happy_path_with_drive_upload(self):
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
            patch.object(rgo_main, "firestore_client", mock_client),
            patch.object(rgo_main, "md_gen") as mock_md,
            patch.object(rgo_main, "get_drive_service", return_value=mock_drive_service),
            patch.object(rgo_main, "find_or_create_folder", return_value="subfolder456"),
            patch.object(
                rgo_main,
                "upload_single_file",
                return_value=(
                    "Identity___Jane_Smith.md",
                    {"id": "file1", "webViewLink": "https://drive.google.com/file1"},
                ),
            ),
        ):
            mock_md.generate_identity_report = fake_generate
            on_job_updated(_make_cloud_event())

        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"
        assert "reports_folder_link" in update_data

    def test_drive_404_handled_gracefully(self):
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
            patch.object(rgo_main, "firestore_client", mock_client),
            patch.object(rgo_main, "md_gen") as mock_md,
            patch.object(rgo_main, "get_drive_service", side_effect=drive_error),
        ):
            mock_md.generate_identity_report = fake_generate
            on_job_updated(_make_cloud_event())

        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"

    def test_report_generation_failure_updates_status(self):
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        with patch.object(rgo_main, "firestore_client", mock_client), patch.object(rgo_main, "md_gen") as mock_md:
            mock_md.generate_identity_report.side_effect = RuntimeError("generation failed")
            on_job_updated(_make_cloud_event())

        last_set_call = mock_ref.set.call_args[0][0]
        assert last_set_call["status"] == "failed_report_generation"
        assert "generation failed" in last_set_call["error_message"]

    def test_result_as_dict(self):
        data = _sample_job_data()
        data["result"] = json.loads(data["result"])

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Report")

        with patch.object(rgo_main, "firestore_client", mock_client), patch.object(rgo_main, "md_gen") as mock_md:
            mock_md.generate_identity_report = fake_generate
            on_job_updated(_make_cloud_event())

        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"

    def test_borrower_name_fallback_to_input(self):
        """If seed has no full_name, falls back to input.full_name."""
        data = _sample_job_data()
        identity_data = json.loads(data["result"])
        identity_data["identity"]["seed"] = {}  # No full_name in seed
        data["result"] = json.dumps(identity_data)

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        generated_names = []

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            generated_names.append(borrower_name)
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Report")

        with patch.object(rgo_main, "firestore_client", mock_client), patch.object(rgo_main, "md_gen") as mock_md:
            mock_md.generate_identity_report = fake_generate
            on_job_updated(_make_cloud_event())

        assert generated_names[0] == "Jane Smith"  # From input, not seed

    # --- Early exit via CloudEvent payload tests ---

    def test_early_exit_wrong_workflow_from_payload(self):
        """Payload with wrong workflow_type exits before Firestore read."""
        _setup_payload_mock(workflow_type="skiptrace", status="post_processing")
        mock_client = MagicMock()

        with patch.object(rgo_main, "firestore_client", mock_client):
            on_job_updated(_make_cloud_event(data=b"fake-protobuf"))

        mock_client.collection.assert_not_called()

    def test_early_exit_wrong_status_from_payload(self):
        """Payload with wrong status exits before Firestore read."""
        _setup_payload_mock(workflow_type="origination", status="running")
        mock_client = MagicMock()

        with patch.object(rgo_main, "firestore_client", mock_client):
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

        with patch.object(rgo_main, "firestore_client", mock_client), patch.object(rgo_main, "md_gen") as mock_md:
            mock_md.generate_identity_report = fake_generate
            on_job_updated(_make_cloud_event(data=b"garbage"))

        # Should have proceeded to Firestore read and completed
        mock_client.collection.assert_called()
        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"

    def test_payload_matches_proceeds_to_firestore(self):
        """When payload fields match, function proceeds to Firestore read."""
        _setup_payload_mock(workflow_type="origination", status="post_processing")
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Report")

        with patch.object(rgo_main, "firestore_client", mock_client), patch.object(rgo_main, "md_gen") as mock_md:
            mock_md.generate_identity_report = fake_generate
            on_job_updated(_make_cloud_event(data=b"fake-protobuf"))

        mock_client.collection.assert_called()
        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"

    def test_drive_folder_transient_error_retried(self):
        """Drive 503 on find_or_create_folder is retried, then succeeds."""
        _setup_payload_mock(workflow_type="origination", status="post_processing")
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Report")

        call_count = [0]

        def folder_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("503 Service Unavailable")
            return "subfolder123"

        with (
            patch.object(rgo_main, "firestore_client", mock_client),
            patch.object(rgo_main, "md_gen") as mock_md,
            patch.object(rgo_main, "get_drive_service", return_value=MagicMock()),
            patch.object(rgo_main, "find_or_create_folder", side_effect=folder_side_effect),
            patch.object(
                rgo_main,
                "upload_single_file",
                return_value=(
                    "Identity___Jane_Smith.md",
                    {"id": "file1", "webViewLink": "https://drive.google.com/file1"},
                ),
            ),
            patch("retry_utils.time.sleep"),
        ):
            mock_md.generate_identity_report = fake_generate
            on_job_updated(_make_cloud_event())

        assert call_count[0] == 2
        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"


# ===========================================================================
# Drive helper functions & additional uncovered lines
# ===========================================================================


class TestDriveHelpers:
    """Tests for get_drive_service, find_or_create_folder, upload_file_to_drive, upload_single_file."""

    # --- get_drive_service (lines 83-84) ---

    def test_get_drive_service_success(self):
        """Successful build of Drive service returns service object."""
        mock_creds = MagicMock()
        mock_service = MagicMock()

        with (
            patch.object(rgo_main, "default", return_value=(mock_creds, "project-id")),
            patch.object(rgo_main, "build", return_value=mock_service) as mock_build,
        ):
            result = rgo_main.get_drive_service()

        assert result is mock_service
        mock_build.assert_called_once_with("drive", "v3", credentials=mock_creds)

    # --- find_or_create_folder (lines 96-117) ---

    def test_find_or_create_folder_existing(self):
        """When folder already exists, return its ID without creating."""
        mock_service = MagicMock()
        mock_service.files.return_value.list.return_value.execute.return_value = {
            "files": [{"id": "existing-folder-id", "name": "MyFolder"}]
        }

        result = rgo_main.find_or_create_folder(mock_service, "parent-id", "MyFolder")

        assert result == "existing-folder-id"
        # Should NOT call create
        mock_service.files.return_value.create.assert_not_called()

    def test_find_or_create_folder_create_new(self):
        """When folder does not exist, create it and return the new ID."""
        mock_service = MagicMock()
        # list returns empty results
        mock_service.files.return_value.list.return_value.execute.return_value = {"files": []}
        # create returns new folder
        mock_service.files.return_value.create.return_value.execute.return_value = {"id": "new-folder-id"}

        result = rgo_main.find_or_create_folder(mock_service, "parent-id", "NewFolder")

        assert result == "new-folder-id"
        mock_service.files.return_value.create.assert_called_once()
        call_kwargs = mock_service.files.return_value.create.call_args[1]
        assert call_kwargs["body"]["name"] == "NewFolder"
        assert call_kwargs["body"]["parents"] == ["parent-id"]
        assert call_kwargs["supportsAllDrives"] is True

    def test_find_or_create_folder_parent_404(self):
        """HttpError 404 on list propagates to caller."""
        mock_service = MagicMock()
        mock_resp = MagicMock(status=404)
        mock_service.files.return_value.list.return_value.execute.side_effect = _HttpError(
            resp=mock_resp, content=b"Not Found"
        )

        with pytest.raises(_HttpError):
            rgo_main.find_or_create_folder(mock_service, "bad-parent-id", "Folder")

    # --- upload_file_to_drive (lines 127-142) ---

    def test_upload_file_to_drive_success(self):
        """Successful file upload returns file metadata."""
        mock_service = MagicMock()
        expected_meta = {"id": "file-123", "name": "report.md", "webViewLink": "https://drive.google.com/file-123"}
        mock_service.files.return_value.create.return_value.execute.return_value = expected_meta

        mock_media = MagicMock()
        with patch.object(rgo_main, "MediaFileUpload", return_value=mock_media) as mock_media_cls:
            result = rgo_main.upload_file_to_drive(mock_service, Path("/tmp/report.md"), "report.md", "parent-id")

        assert result == expected_meta
        mock_media_cls.assert_called_once_with(str(Path("/tmp/report.md")), mimetype="text/markdown", resumable=True)
        create_kwargs = mock_service.files.return_value.create.call_args[1]
        assert create_kwargs["body"]["name"] == "report.md"
        assert create_kwargs["body"]["parents"] == ["parent-id"]
        assert create_kwargs["media_body"] is mock_media
        assert create_kwargs["supportsAllDrives"] is True

    # --- upload_single_file (lines 152-158) ---

    def test_upload_single_file_success(self):
        """upload_single_file creates its own Drive service and returns (name, meta) tuple."""
        mock_service = MagicMock()
        expected_meta = {"id": "f1", "webViewLink": "https://drive.google.com/f1"}

        with (
            patch.object(rgo_main, "get_drive_service", return_value=mock_service) as mock_get_svc,
            patch.object(rgo_main, "upload_file_to_drive", return_value=expected_meta),
            patch("retry_utils.time.sleep"),
        ):
            name, meta = rgo_main.upload_single_file(Path("/tmp/report.md"), "report.md", "folder-id")

        assert name == "report.md"
        assert meta == expected_meta
        mock_get_svc.assert_called_once()


# ===========================================================================
# Additional on_job_updated uncovered lines
# ===========================================================================


class TestOnJobUpdatedAdditionalCoverage:
    """Tests covering remaining uncovered lines in on_job_updated."""

    @pytest.fixture(autouse=True)
    def _reset_payload_mock(self):
        """Reset DocumentEventData mock before each test."""
        rgo_main.firestore_events.DocumentEventData.reset_mock()
        rgo_main.firestore_events.DocumentEventData.return_value = MagicMock(
            _pb=MagicMock(ParseFromString=MagicMock(side_effect=Exception("no payload configured")))
        )

    # --- Line 191: document path starting with "jobs/" ---

    def test_document_path_jobs_prefix(self):
        """Document path 'jobs/jobXYZ' extracts job_id via startswith branch."""
        data = _sample_job_data()
        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Report")

        with patch.object(rgo_main, "firestore_client", mock_client), patch.object(rgo_main, "md_gen") as mock_md:
            mock_md.generate_identity_report = fake_generate
            on_job_updated(_make_cloud_event("jobs/jobXYZ"))

        # Should have looked up document with job_id "jobXYZ"
        mock_client.collection.return_value.document.assert_called_with("jobXYZ")
        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"

    # --- Line 301: unexpected file count warning ---

    def test_unexpected_file_count_warning(self):
        """When generate_identity_report creates no files, a warning is printed but processing continues."""
        data = _sample_job_data()
        data["input"]["drive_folder_id"] = ""  # Skip Drive upload

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate_no_files(report_data, borrower_name, output_dir, **kwargs):
            # Intentionally create NO files to trigger the warning
            pass

        with patch.object(rgo_main, "firestore_client", mock_client), patch.object(rgo_main, "md_gen") as mock_md:
            mock_md.generate_identity_report = fake_generate_no_files
            on_job_updated(_make_cloud_event())

        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"

    # --- Line 340: no files to upload ---

    def test_drive_upload_no_files_to_upload(self):
        """When report files don't exist on disk, 'no files found to upload' warning path is hit."""
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate_no_files(report_data, borrower_name, output_dir, **kwargs):
            # Create no files — the expected Identity file won't exist
            pass

        mock_drive_service = MagicMock()

        with (
            patch.object(rgo_main, "firestore_client", mock_client),
            patch.object(rgo_main, "md_gen") as mock_md,
            patch.object(rgo_main, "get_drive_service", return_value=mock_drive_service),
            patch.object(rgo_main, "find_or_create_folder", return_value="subfolder789"),
            patch("retry_utils.time.sleep"),
        ):
            mock_md.generate_identity_report = fake_generate_no_files
            on_job_updated(_make_cloud_event())

        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"
        # No upload was attempted since no files existed
        assert update_data.get("report_urls", {}) == {}

    # --- Lines 358-361: upload future raises exception ---

    def test_drive_upload_future_exception(self):
        """When upload_single_file raises inside a thread, upload_errors is populated.

        The re-raised Exception("Upload errors: ...") on line 369 is caught by the
        inner except Exception on line 382, which handles it gracefully (status=complete).
        """
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Report")

        mock_drive_service = MagicMock()

        with (
            patch.object(rgo_main, "firestore_client", mock_client),
            patch.object(rgo_main, "md_gen") as mock_md,
            patch.object(rgo_main, "get_drive_service", return_value=mock_drive_service),
            patch.object(rgo_main, "find_or_create_folder", return_value="subfolder789"),
            patch.object(rgo_main, "upload_single_file", side_effect=RuntimeError("upload boom")),
            patch("retry_utils.time.sleep"),
        ):
            mock_md.generate_identity_report = fake_generate
            on_job_updated(_make_cloud_event())

        # Upload error is caught by inner except Exception (line 382), handled gracefully
        last_set = mock_ref.set.call_args[0][0]
        assert last_set["status"] == "complete"
        # Drive upload failed, so report_urls should be empty
        assert last_set.get("report_urls", {}) == {}
        # No reports_folder_link since Drive upload failed
        assert "reports_folder_link" not in last_set

    # --- Line 366: missing file warning ---

    def test_drive_upload_missing_file_warning(self):
        """When expected report file name doesn't exist but upload completes, missing file warning fires."""
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate_wrong_name(report_data, borrower_name, output_dir, **kwargs):
            # Create a file with a NON-matching name, so the expected file isn't found
            wrong_file = output_dir / "Wrong___Name.md"
            wrong_file.write_text("# Wrong Report")

        mock_drive_service = MagicMock()

        with (
            patch.object(rgo_main, "firestore_client", mock_client),
            patch.object(rgo_main, "md_gen") as mock_md,
            patch.object(rgo_main, "get_drive_service", return_value=mock_drive_service),
            patch.object(rgo_main, "find_or_create_folder", return_value="subfolder789"),
            patch("retry_utils.time.sleep"),
        ):
            mock_md.generate_identity_report = fake_generate_wrong_name
            on_job_updated(_make_cloud_event())

        # No files to upload, but should still complete (line 340 + 366)
        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"

    # --- Line 379: non-404 HttpError ---

    def test_drive_non_404_http_error(self):
        """Non-404 HttpError (e.g. 403) is handled gracefully with different error message."""
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Report")

        mock_resp = MagicMock(status=403)
        drive_error = _HttpError(resp=mock_resp, content=b"Forbidden")

        with (
            patch.object(rgo_main, "firestore_client", mock_client),
            patch.object(rgo_main, "md_gen") as mock_md,
            patch.object(rgo_main, "get_drive_service", side_effect=drive_error),
        ):
            mock_md.generate_identity_report = fake_generate
            on_job_updated(_make_cloud_event())

        update_data = mock_ref.set.call_args[0][0]
        # Reports still complete because Drive errors are handled gracefully
        assert update_data["status"] == "complete"

    # --- Lines 400-402: markdown file not found + read exception ---

    def test_identity_markdown_file_not_found(self):
        """When identity markdown file doesn't exist, warning is printed but completes."""
        data = _sample_job_data()
        data["input"]["drive_folder_id"] = ""

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate_no_identity(report_data, borrower_name, output_dir, **kwargs):
            # Create a file with wrong name so identity file doesn't exist
            other = output_dir / "Other___File.md"
            other.write_text("# Other")

        with patch.object(rgo_main, "firestore_client", mock_client), patch.object(rgo_main, "md_gen") as mock_md:
            mock_md.generate_identity_report = fake_generate_no_identity
            on_job_updated(_make_cloud_event())

        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"
        # No markdown_reports since the identity file wasn't found
        assert "identity" not in update_data.get("markdown_reports", {})

    def test_markdown_read_exception(self):
        """Exception while reading markdown file is caught gracefully."""
        data = _sample_job_data()
        data["input"]["drive_folder_id"] = ""

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        def fake_generate(report_data, borrower_name, output_dir, **kwargs):
            md_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"
            md_file.write_text("# Report")

        with (
            patch.object(rgo_main, "firestore_client", mock_client),
            patch.object(rgo_main, "md_gen") as mock_md,
            patch("builtins.open", side_effect=PermissionError("cannot read")),
        ):
            mock_md.generate_identity_report = fake_generate
            on_job_updated(_make_cloud_event())

        update_data = mock_ref.set.call_args[0][0]
        assert update_data["status"] == "complete"

    # --- Lines 446-447: Firestore update error in outer except ---

    def test_firestore_update_error_in_outer_except(self):
        """When both report generation and Firestore error-update fail, no unhandled exception."""
        data = _sample_job_data()

        mock_doc = _mock_firestore_doc(data)
        mock_ref = MagicMock()
        mock_ref.get.return_value = mock_doc
        # The error-status update itself fails
        mock_ref.set.side_effect = RuntimeError("Firestore is down")

        mock_client = MagicMock()
        mock_client.collection.return_value.document.return_value = mock_ref

        with patch.object(rgo_main, "firestore_client", mock_client), patch.object(rgo_main, "md_gen") as mock_md:
            mock_md.generate_identity_report.side_effect = RuntimeError("generation exploded")
            # Should not raise — the outer except catches Firestore update error too
            on_job_updated(_make_cloud_event())
