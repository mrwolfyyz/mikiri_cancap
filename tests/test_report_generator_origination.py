"""Tests for the report_generator_origination Cloud Function.

Covers:
- transform_firestore_to_report_format (pure data transform)
- on_job_updated (Firestore event handler: guards, happy path, Drive upload, error handling)
"""

import json
import sys
from unittest.mock import MagicMock, patch

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
def _make_cloud_event(document_path="projects/p/databases/d/documents/jobs/job456"):
    event = MagicMock()
    event.get_attributes.return_value = {"document": document_path}
    return event


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
