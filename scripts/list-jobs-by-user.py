#!/usr/bin/env python3
"""
List jobs in Firestore by user_id (Firebase UID).

Use when you need to view or inspect jobs created by another user.
Requires Application Default Credentials with Firestore read access
(e.g. gcloud auth application-default login, or GOOGLE_APPLICATION_CREDENTIALS).

Usage:
  # List all jobs for a user (Firebase UID)
  python scripts/list-jobs-by-user.py --user-id ght4fuuPHtgpGvAc0bLuHPTY7ZO2

  # List with GCP project
  python scripts/list-jobs-by-user.py --user-id <uid> --project my-gcp-project

  # List jobs from the last 36 hours
  python scripts/list-jobs-by-user.py --since 36h

  # List jobs from the last 2 days
  python scripts/list-jobs-by-user.py --since 2d

  # Fetch one job and print details (optionally write markdown to files)
  python scripts/list-jobs-by-user.py --job-id ee50469c4bc6 --project my-gcp-project
  python scripts/list-jobs-by-user.py --job-id ee50469c4bc6 --write-markdown ./reports
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    from google.cloud import firestore
except ImportError:
    print("Missing dependency: pip install google-cloud-firestore", file=sys.stderr)
    sys.exit(1)


def parse_duration(s: str) -> timedelta:
    """Parse a human duration string like '36h', '2d', '90m' into a timedelta."""
    m = re.fullmatch(r"(\d+)\s*([mhd])", s.strip().lower())
    if not m:
        raise argparse.ArgumentTypeError(f"Invalid duration: '{s}'. Use e.g. 90m, 36h, 2d")
    val, unit = int(m.group(1)), m.group(2)
    if unit == "m":
        return timedelta(minutes=val)
    if unit == "h":
        return timedelta(hours=val)
    return timedelta(days=val)


def get_firestore_client(project: Optional[str]):
    if project:
        return firestore.Client(project=project)
    return firestore.Client()


def serialize_value(v):
    """Convert Firestore types to JSON-serializable (e.g. datetime -> ISO string)."""
    if hasattr(v, "isoformat"):
        return v.isoformat() + "Z" if hasattr(v, "tzinfo") and v.tzinfo is None else v.isoformat()
    if isinstance(v, dict):
        return {k: serialize_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [serialize_value(x) for x in v]
    return v


def list_jobs_by_user(db, user_id: str) -> list[dict]:
    """Query jobs collection where user_id == user_id. Sorted by created_at desc in memory."""
    ref = db.collection("jobs")
    query = ref.where("user_id", "==", user_id)
    docs = query.stream()
    out = []
    for doc in docs:
        d = doc.to_dict()
        d["job_id"] = doc.id
        out.append(d)
    # Sort by created_at descending (most recent first); put jobs without created_at last
    def _sort_key(j):
        ca = j.get("created_at")
        ts = -ca.timestamp() if (ca and hasattr(ca, "timestamp")) else 0
        return (1 if ca is None else 0, ts)

    out.sort(key=_sort_key)
    return out


def list_jobs_since(db, since: datetime, until: Optional[datetime] = None) -> list[dict]:
    """Query jobs where created_at >= since and (optionally) created_at < until. Sorted by created_at desc."""
    ref = db.collection("jobs")
    query = ref.where("created_at", ">=", since)
    if until is not None:
        query = query.where("created_at", "<", until)
    docs = query.stream()
    out = []
    for doc in docs:
        d = doc.to_dict()
        d["job_id"] = doc.id
        out.append(d)

    def _sort_key(j):
        ca = j.get("created_at")
        ts = -ca.timestamp() if (ca and hasattr(ca, "timestamp")) else 0
        return (1 if ca is None else 0, ts)

    out.sort(key=_sort_key)
    return out


def get_job(db, job_id: str) -> Optional[dict]:
    doc = db.collection("jobs").document(job_id).get()
    if not doc.exists:
        return None
    d = doc.to_dict()
    d["job_id"] = doc.id
    return d


def main():
    ap = argparse.ArgumentParser(
        description="List Firestore jobs by user_id or fetch a single job.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--user-id", metavar="UID", help="Firebase UID to list jobs for")
    ap.add_argument("--job-id", metavar="ID", help="Fetch a single job by ID (ignores --user-id)")
    ap.add_argument("--today", action="store_true", help="List all jobs created today (UTC)")
    ap.add_argument("--since", metavar="DURATION", help="List jobs from the last DURATION (e.g. 36h, 2d, 90m)")
    ap.add_argument("--project", "-p", default=os.environ.get("GCP_PROJECT"), help="GCP project (default: GCP_PROJECT or ADC project)")
    ap.add_argument("--write-markdown", metavar="DIR", help="For --job-id: write markdown_reports to this directory")
    ap.add_argument("--json", action="store_true", help="Output raw JSON")
    args = ap.parse_args()

    if not args.user_id and not args.job_id and not args.today and not args.since:
        ap.error("Provide --user-id, --job-id, --today, or --since.")

    db = get_firestore_client(args.project)

    if args.today:
        now = datetime.utcnow()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        jobs = list_jobs_since(db, start, end)
        if args.json:
            print(json.dumps([serialize_value(j) for j in jobs], indent=2))
        else:
            if not jobs:
                print("No jobs created today (UTC).")
            else:
                print(f"Jobs created today ({start.isoformat()}Z – {end.isoformat()}Z) ({len(jobs)} total):\n")
                for j in jobs:
                    created = serialize_value(j.get("created_at"))
                    name = j.get("full_name") or (j.get("input") or {}).get("full_name") or "—"
                    status = j.get("status", "—")
                    uid = j.get("user_id") or "—"
                    print(f"  {j['job_id']}  created={created}  status={status}  full_name={name}  user_id={uid}")
        return

    if args.since:
        delta = parse_duration(args.since)
        now = datetime.utcnow()
        start = now - delta
        jobs = list_jobs_since(db, start)
        if args.json:
            print(json.dumps([serialize_value(j) for j in jobs], indent=2))
        else:
            if not jobs:
                print(f"No jobs in the last {args.since}.")
            else:
                print(f"Jobs since {start.isoformat()}Z ({len(jobs)} total):\n")
                for j in jobs:
                    created = serialize_value(j.get("created_at"))
                    name = j.get("full_name") or (j.get("input") or {}).get("full_name") or "—"
                    status = j.get("status", "—")
                    uid = j.get("user_id") or "—"
                    print(f"  {j['job_id']}  created={created}  status={status}  full_name={name}  user_id={uid}")
        return

    if args.job_id:
        job = get_job(db, args.job_id)
        if not job:
            print(f"Job not found: {args.job_id}", file=sys.stderr)
            sys.exit(1)
        if args.json:
            print(json.dumps(serialize_value(job), indent=2))
        else:
            # Summary
            summary = {
                "job_id": job.get("job_id"),
                "user_id": job.get("user_id"),
                "status": job.get("status"),
                "full_name": job.get("full_name") or (job.get("input") or {}).get("full_name"),
                "email": job.get("email") or (job.get("input") or {}).get("email"),
                "created_at": serialize_value(job.get("created_at")),
                "started_at": serialize_value(job.get("started_at")),
                "completed_at": serialize_value(job.get("completed_at")),
                "reports_generated": job.get("reports_generated"),
                "has_markdown_reports": bool(job.get("markdown_reports")),
                "report_urls": list((job.get("report_urls") or {}).keys()),
            }
            print("Job summary:")
            for k, v in summary.items():
                print(f"  {k}: {v}")
            if job.get("markdown_reports") and args.write_markdown:
                out_dir = Path(args.write_markdown)
                out_dir.mkdir(parents=True, exist_ok=True)
                for name, content in job["markdown_reports"].items():
                    path = out_dir / f"{args.job_id}_{name}.md"
                    path.write_text(content, encoding="utf-8")
                    print(f"  Wrote {path}")
        if args.write_markdown and not (job.get("markdown_reports")):
            print("No markdown_reports on this job.", file=sys.stderr)
        return

    jobs = list_jobs_by_user(db, args.user_id)
    if args.json:
        print(json.dumps([serialize_value(j) for j in jobs], indent=2))
        return

    if not jobs:
        print(f"No jobs found for user_id: {args.user_id}")
        return

    print(f"Jobs for user_id {args.user_id} ({len(jobs)} total):\n")
    for j in jobs:
        created = serialize_value(j.get("created_at"))
        name = j.get("full_name") or (j.get("input") or {}).get("full_name") or "—"
        status = j.get("status", "—")
        print(f"  {j['job_id']}  created={created}  status={status}  full_name={name}")


if __name__ == "__main__":
    main()
