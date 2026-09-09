"""Tests for the nightly BigQuery -> Cloud Storage backup export (TDD 4.4)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
for _p in (str(ROOT), str(ROOT / "backend"), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.backup_export import destination_uris, run_export  # noqa: E402


def test_destination_uris_are_dated_and_wildcarded():
    uris = destination_uris(
        "bk-bucket", "cyclotorsion_check.results", "proj", "20260101-000000"
    )
    assert len(uris) == 1
    assert uris[0] == (
        "gs://bk-bucket/proj/cyclotorsion_check_results/20260101-000000-*.jsonl"
    )


def test_dry_run_returns_empty_without_errors(capsys):
    run_id = run_export(
        "proj",
        "cyclotorsion_check.results",
        "bk-bucket",
        dry_run=True,
        location="us-central1",
    )
    assert run_id == ""
    out = capsys.readouterr().out
    assert "gs://bk-bucket/proj/cyclotorsion_check_results/" in out
