"""Nightly BigQuery -> Cloud Storage backup export (TDD Section 4.4).

Implements the TDD's low-cost disaster-recovery mitigation: export the
``results`` analytics table to Cloud Storage once a day (scheduled by Cloud
Scheduler — see infra/) so aggregate result metadata survives accidental
deletion or regional issues. Target RPO: 24 hours; RTO best-effort (the table
is analytics, not the clinical system of record, per PRD's non-diagnostic
positioning).

The export is a standard BigQuery ``extract_table`` (table -> GCS files). New
files are written under a dated prefix so every nightly run leaves a full,
self-contained snapshot; a retention schedule (see IaC) prunes old copies.

Design notes:
  - **Not imported by the API** — it is an operational script invoked by Cloud
    Scheduler, so requiring GCP credentials here never affects the serving path.
  - Config via env (mirrors cyclotorsion.config): CC_PROJECT_ID, the BigQuery
    table, and the destination GCS bucket. The optional ``--dry-run`` prints
    the destination URIs and exits without contacting GCP, so the script is
    testable/runnable without credentials.

Usage:
    set CC_PROJECT_ID=cyclotorsion-check
    set CC_BACKUP_TABLE=cyclotorsion_check.results
    set CC_BACKUP_BUCKET=cyclotorsion-backups
    python scripts/backup_export.py            # real export
    python scripts/backup_export.py --dry-run  # print what would happen
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
for _p in (ROOT, BACKEND):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Use the application's JSON logger so Cloud Logging picks up the run trail.
import logging  # noqa: E402

from cyclotorsion.logging_config import configure_logging  # noqa: E402

configure_logging()
logger = logging.getLogger("cyclotorsion.backup")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def destination_uris(bucket: str, table: str, project: str, run_id: str) -> list[str]:
    """Compute the dated wildcard URIs this run writes to."""
    table_short = table.replace(".", "_")
    return [f"gs://{bucket}/{project}/{table_short}/{run_id}-*.jsonl"]


def run_export(
    project: str,
    table: str,
    bucket: str,
    *,
    dry_run: bool = False,
    location: str = "us-central1",
) -> str:
    """Start an extract job; returns the job id ("" when dry-run).

    Raises on failure so Cloud Scheduler / alerting sees a non-zero exit.
    """
    run_id = _run_id()
    uris = destination_uris(bucket, table, project, run_id)

    if dry_run:
        logger.info(
            "backup.dry_run",
            extra={"event": "backup.dry_run", "run_id": run_id, "uris": uris},
        )
        print("\n".join(uris))
        return ""

    from google.cloud import bigquery

    client = bigquery.Client(project=project, location=location)
    job_config = bigquery.ExtractJobConfig(
        destination_format="NEWLINE_DELIMITED_JSON",
        compression="GZIP",
        field_delimiter=",",
        print_header=True,
    )
    extract = client.extract_table(
        table,
        uris,
        job_config=job_config,
    )
    extract.result()  # blocks until the job finishes
    logger.info(
        "backup.export_complete",
        extra={
            "event": "backup.export_complete",
            "run_id": run_id,
            "destination_uris": uris,
        },
    )
    return extract.job_id


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="print URIs, don't export")
    ap.add_argument("--location", default="us-central1")
    ap.add_argument("--bucket", default="")
    ap.add_argument("--table", default="")
    ap.add_argument("--project", default="")
    args = ap.parse_args(argv)

    project = args.project or _env("CC_PROJECT_ID")
    table = args.table or _env("CC_BACKUP_TABLE", "cyclotorsion_check.results")
    bucket = args.bucket or _env("CC_BACKUP_BUCKET")

    errors: list[str] = []
    if not project:
        errors.append("project (--project or CC_PROJECT_ID)")
    if not bucket:
        errors.append("bucket (--bucket or CC_BACKUP_BUCKET)")

    if errors:
        print("Missing required configuration: " + ", ".join(errors))
        return 2

    try:
        run_export(project, table, bucket, dry_run=args.dry_run, location=args.location)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "backup.export_failed",
            extra={"event": "backup.export_failed", "error": str(exc)},
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
