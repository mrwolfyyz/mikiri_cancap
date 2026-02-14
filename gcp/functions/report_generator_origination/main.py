"""
Origination Report Generator Cloud Function

Triggered by Firestore document updates when status == "post_processing" and workflow_type == "origination".
Generates only simplified Identity report (excluding Contactability and Public Sector Employment sections) and uploads to Google Drive.
"""

import json
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from cloudevents.http import CloudEvent
from firebase_admin import firestore, initialize_app
from functions_framework import cloud_event
from google.auth import default
from google.events.cloud import firestore as firestore_events
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# Initialize Firebase Admin SDK
initialize_app()
firestore_client = firestore.client()

# Import report generation functions
import generate_markdown_reports as md_gen

# -------------------------
# Configuration
# -------------------------

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


# -------------------------
# Data Transformation
# -------------------------


def transform_firestore_to_report_format(firestore_data: dict[str, Any]) -> dict[str, Any]:
    """
    Transform Firestore result structure to match generate_markdown_reports.py expectations.

    Firestore structure (from aggregator):
    - result.identity (contains: seed, scored, contactability, breaches, queries)
    - result.enrichment (contains: domains, addresses, contacts)

    Expected structure:
    - data['seed']
    - data['scored']
    - data['contactability']
    - data['breaches']
    - data['queries']
    """
    identity = firestore_data.get("identity", {})

    transformed = {
        "seed": identity.get("seed", {}),
        "scored": identity.get("scored", {}),
        "contactability": identity.get("contactability", {}),
        "breaches": identity.get("breaches", []),
        "queries": identity.get("queries", []),
        "grounding_metadata": identity.get("grounding_metadata", {}),
    }

    return transformed


# -------------------------
# Google Drive Integration
# -------------------------


def get_drive_service():
    """Get authenticated Google Drive API service."""
    try:
        # Get credentials with Drive scope (supports Shared Drives)
        credentials, _ = default(scopes=DRIVE_SCOPES)
        service = build("drive", "v3", credentials=credentials)
        return service
    except Exception as e:
        print(f"[Drive] Error getting service: {e}")
        raise


def find_or_create_folder(drive_service, parent_folder_id: str, folder_name: str) -> str:
    """
    Find or create a folder in Google Drive.
    Returns the folder ID.
    """
    # Search for existing folder (supports both regular Drive and Shared Drives)
    query = f"name='{folder_name}' and '{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = (
        drive_service.files()
        .list(q=query, spaces="drive", fields="files(id, name)", supportsAllDrives=True, includeItemsFromAllDrives=True)
        .execute()
    )

    items = results.get("files", [])
    if items:
        print(f"[Drive] Found existing folder: {folder_name} (ID: {items[0]['id']})")
        return items[0]["id"]

    # Create new folder (supports both regular Drive and Shared Drives)
    folder_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id],
    }
    folder = drive_service.files().create(body=folder_metadata, fields="id", supportsAllDrives=True).execute()

    print(f"[Drive] Created folder: {folder_name} (ID: {folder['id']})")
    return folder["id"]


def upload_file_to_drive(
    drive_service, file_path: Path, file_name: str, parent_folder_id: str, mime_type: str = "text/markdown"
) -> dict[str, Any]:
    """
    Upload a file to Google Drive.
    Returns file metadata including webViewLink.
    """
    file_metadata = {"name": file_name, "parents": [parent_folder_id]}

    # Convert Path to string and create MediaFileUpload
    file_path_str = str(file_path)
    media = MediaFileUpload(file_path_str, mimetype=mime_type, resumable=True)

    media_body = (
        drive_service.files()
        .create(
            body=file_metadata, media_body=media, fields="id, name, webViewLink, webContentLink", supportsAllDrives=True
        )
        .execute()
    )

    print(f"[Drive] Uploaded: {file_name} (ID: {media_body['id']})")
    return media_body


def upload_single_file(file_path: Path, file_name: str, parent_folder_id: str) -> tuple[str, dict[str, Any]]:
    """
    Wrapper for upload_file_to_drive for use with ThreadPoolExecutor.
    Creates its own Drive service instance to avoid thread-safety issues.
    Returns (file_name, result_dict) tuple.
    """
    # Create a new Drive service instance for this thread to avoid thread-safety issues
    drive_service = get_drive_service()
    file_meta = upload_file_to_drive(drive_service, file_path, file_name, parent_folder_id)
    return (file_name, file_meta)


# -------------------------
# Main Function
# -------------------------


@cloud_event
def on_job_updated(event: CloudEvent) -> None:
    """
    Firestore trigger entry point.
    Triggered when a job document is updated.
    Only processes jobs with workflow_type == "origination".
    """
    print("[OriginationReportGenerator] Function triggered!")
    job_id = None

    try:
        # Parse Firestore event from CloudEvent
        attributes = event.get_attributes() if hasattr(event, "get_attributes") else {}
        print(f"[OriginationReportGenerator] Event attributes: {attributes}")

        # Extract job_id from document attribute
        document_path = attributes.get("document", "")
        if not document_path:
            print("[OriginationReportGenerator] ERROR: 'document' attribute not found in event")
            return

        # Extract job_id from document path
        if "/jobs/" in document_path:
            job_id = document_path.split("/jobs/")[-1]
        elif document_path.startswith("jobs/"):
            job_id = document_path.replace("jobs/", "")
        else:
            print(f"[OriginationReportGenerator] ERROR: Could not extract job_id from document path: {document_path}")
            return

        print(f"[OriginationReportGenerator] Processing job: {job_id}")

        # Early exit: check fields from CloudEvent payload to avoid Firestore read
        try:
            payload = firestore_events.DocumentEventData()
            payload._pb.ParseFromString(event.data)
            fields = payload.value.fields

            payload_workflow = fields["workflow_type"].string_value if "workflow_type" in fields else None
            payload_status = fields["status"].string_value if "status" in fields else None

            if payload_workflow and payload_workflow != "origination":
                print(
                    f"[OriginationReportGenerator] Early exit: workflow_type is '{payload_workflow}' (from event payload)"
                )
                return

            if payload_status and payload_status != "post_processing":
                print(f"[OriginationReportGenerator] Early exit: status is '{payload_status}' (from event payload)")
                return
        except Exception:  # nosec B110 — best-effort payload parsing; fall through to Firestore read
            pass

        # Fetch document directly from Firestore
        doc_ref = firestore_client.collection("jobs").document(job_id)
        doc = doc_ref.get()
        if not doc.exists:
            print("[OriginationReportGenerator] ERROR: Document not found in Firestore")
            return

        doc_data = doc.to_dict()
        if not doc_data:
            print("[OriginationReportGenerator] ERROR: Document data is empty")
            return

        # Check workflow_type - only process origination workflows
        workflow_type = doc_data.get("workflow_type", "")
        if workflow_type != "origination":
            print(f"[OriginationReportGenerator] Skipping: workflow_type is '{workflow_type}', expected 'origination'")
            return

        # Get Drive folder ID from job input
        input_data = doc_data.get("input", {})
        drive_folder_id = input_data.get("drive_folder_id", "")
        company_domain = input_data.get("company_domain")

        # Check guards
        status = doc_data.get("status", "")
        if status != "post_processing":
            print(f"[OriginationReportGenerator] Skipping: status is '{status}', expected 'post_processing'")
            return

        # Get result data
        result_str = doc_data.get("result", "{}")
        if isinstance(result_str, str):
            try:
                firestore_result = json.loads(result_str)
            except json.JSONDecodeError as e:
                print(f"[OriginationReportGenerator] ERROR: Failed to parse result JSON: {e}")
                return
        else:
            firestore_result = result_str

        # Transform data structure
        report_data = transform_firestore_to_report_format(firestore_result)

        # Extract enrichment data from Firestore result
        enrichment_data = {}
        if isinstance(firestore_result, dict):
            enrichment = firestore_result.get("enrichment", {})
            enrichment_data = {
                "domains": enrichment.get("domains", {}),
                "addresses": enrichment.get("addresses", {}),
                "contacts": enrichment.get("contacts", {"phones": [], "emails": [], "addresses": []}),
            }

        # Extract borrower name from transformed data
        borrower_name = report_data.get("seed", {}).get("full_name", "UnknownBorrower")
        if not borrower_name or borrower_name == "UnknownBorrower":
            # Fallback to input data if not in seed
            borrower_name = input_data.get("full_name", "UnknownBorrower")

        print(f"[OriginationReportGenerator] Generating loan origination report for: {borrower_name}")

        # Generate reports to temporary directory
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            print(f"[OriginationReportGenerator] Generating reports to: {output_dir}")

            # Generate only simplified Identity report (excluding Contactability and Public Sector Employment sections)
            print("[OriginationReportGenerator] Calling generate_identity_report() with simplified=True")
            md_gen.generate_identity_report(
                report_data,
                borrower_name,
                output_dir,
                company_domain=company_domain,
                enrichment_data=enrichment_data,
                simplified=True,
            )

            # Verify only Identity report was generated
            output_files = list(output_dir.glob("*.md"))
            print(f"[OriginationReportGenerator] Files generated in output directory: {[f.name for f in output_files]}")
            if len(output_files) != 1 or not output_files[0].name.startswith("Identity___"):
                print(
                    f"[OriginationReportGenerator] WARNING: Expected only Identity report, but found {len(output_files)} files"
                )

            print("[OriginationReportGenerator] ✓ Identity report generated")

            # Upload to Google Drive
            reports_folder_link = None
            if not drive_folder_id:
                print("[OriginationReportGenerator] WARNING: No drive_folder_id provided, skipping Drive upload")
                drive_urls = {}
            else:
                try:
                    print(f"[OriginationReportGenerator] Uploading to Google Drive folder: {drive_folder_id}")
                    drive_service = get_drive_service()

                    # Create borrower folder with job_id
                    job_folder_name = f"{borrower_name}_{job_id}"
                    job_folder_id = find_or_create_folder(drive_service, drive_folder_id, job_folder_name)

                    # Upload only Identity report
                    report_urls = {}
                    report_files = [
                        f"Identity___{borrower_name.replace(' ', '_')}.md",
                    ]

                    # Filter to only files that exist
                    files_to_upload = [
                        (file_name, output_dir / file_name)
                        for file_name in report_files
                        if (output_dir / file_name).exists()
                    ]

                    upload_errors = []
                    if not files_to_upload:
                        print("[OriginationReportGenerator] WARNING: No files found to upload")
                    else:
                        print(f"[OriginationReportGenerator] Uploading {len(files_to_upload)} files in parallel...")

                        with ThreadPoolExecutor(max_workers=2) as executor:
                            future_to_file = {}
                            for file_name, file_path in files_to_upload:
                                future = executor.submit(upload_single_file, file_path, file_name, job_folder_id)
                                future_to_file[future] = file_name

                            # Collect results as they complete
                            for future in as_completed(future_to_file):
                                file_name = future_to_file[future]
                                try:
                                    print(f"[OriginationReportGenerator] Uploading {file_name}...")
                                    _, file_meta = future.result()
                                    report_urls[file_name] = file_meta.get("webViewLink", "")
                                    print(f"[OriginationReportGenerator] ✓ Uploaded {file_name}")
                                except Exception as e:
                                    error_msg = f"Failed to upload {file_name}: {str(e)}"
                                    print(f"[OriginationReportGenerator] ERROR: {error_msg}")
                                    upload_errors.append(error_msg)

                        # Log warning for missing files
                        missing_files = set(report_files) - {f[0] for f in files_to_upload}
                        for file_name in missing_files:
                            print(f"[OriginationReportGenerator] WARNING: File not found: {output_dir / file_name}")

                    if upload_errors:
                        raise Exception(f"Upload errors: {'; '.join(upload_errors)}")

                    drive_urls = report_urls
                    reports_folder_link = f"https://drive.google.com/drive/folders/{job_folder_id}"
                    print(f"[OriginationReportGenerator] ✓ All reports uploaded to Drive ({len(report_urls)} files)")
                except HttpError as e:
                    # Handle Drive API errors gracefully - reports are already generated
                    if e.resp.status == 404:
                        error_msg = f"Drive folder not found or inaccessible: {drive_folder_id}. Reports were generated successfully but could not be uploaded to Drive."
                    else:
                        error_msg = f"Drive upload failed: {str(e)}. Reports were generated successfully but could not be uploaded to Drive."
                    print(f"[OriginationReportGenerator] WARNING: {error_msg}")
                    drive_urls = {}  # Empty URLs if upload failed
                except Exception as e:
                    # Handle any other Drive-related errors gracefully
                    error_msg = f"Drive upload failed: {str(e)}. Reports were generated successfully but could not be uploaded to Drive."
                    print(f"[OriginationReportGenerator] WARNING: {error_msg}")
                    drive_urls = {}  # Empty URLs if upload failed

            # Read markdown files and store in Firestore for chat feature
            markdown_reports = {}
            identity_file = output_dir / f"Identity___{borrower_name.replace(' ', '_')}.md"

            try:
                if identity_file.exists():
                    with open(identity_file, encoding="utf-8") as f:
                        markdown_reports["identity"] = f.read()
                    print(
                        f"[OriginationReportGenerator] ✓ Read Identity markdown ({len(markdown_reports['identity'])} chars)"
                    )
                else:
                    print(f"[OriginationReportGenerator] WARNING: Identity markdown file not found: {identity_file}")
            except Exception as e:
                print(f"[OriginationReportGenerator] WARNING: Failed to read markdown files for chat storage: {e}")
                # Continue anyway - markdown storage is optional for chat feature

            # Update Firestore
            job_ref = firestore_client.collection("jobs").document(job_id)

            update_data = {
                "status": "complete",
                "completed_at": datetime.utcnow(),
                "expire_at": datetime.utcnow() + timedelta(days=7),  # TTL field for 7-day retention
                "reports_generated": True,
                "report_urls": drive_urls,
            }
            if reports_folder_link:
                update_data["reports_folder_link"] = reports_folder_link

            # Store markdown reports for chat feature (if available)
            if markdown_reports:
                update_data["markdown_reports"] = markdown_reports
                print("[OriginationReportGenerator] ✓ Stored markdown reports in Firestore for chat feature")

            job_ref.set(update_data, merge=True)

            print("[OriginationReportGenerator] ✓ Updated Firestore: status=complete, reports_generated=true")
            print(f"[OriginationReportGenerator] ✓ Job {job_id} completed successfully")

    except Exception as e:
        print(f"[OriginationReportGenerator] ERROR: {e}")
        import traceback

        traceback.print_exc()

        # Try to update Firestore with error status
        try:
            if job_id:
                job_ref = firestore_client.collection("jobs").document(job_id)
                job_ref.set(
                    {
                        "status": "failed_report_generation",
                        "error_message": str(e),
                        "expire_at": datetime.utcnow() + timedelta(days=7),  # TTL field for 7-day retention
                    },
                    merge=True,
                )
        except Exception as update_error:
            print(f"[OriginationReportGenerator] ERROR: Failed to update Firestore with error: {update_error}")
