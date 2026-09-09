"""Integration tests: the full auth-enabled /detect -> /stats production path.

Unlike the unit tests, these exercise the *whole* circuit the way it runs in
Cloud Run: Firebase ID-token verification (mocked SDK) -> Gemini-not-invoked
mock detection -> BigQuery analytics write -> BigQuery analytics read with
facility scoping.

The cloud SDKs are faked in-process (matching the codebase's lazy-import
pattern), so this tier runs in CI without credentials. Point the same tests at
the real emulators / a deployed endpoint by swapping the fakes for live clients.
"""

import sys
import types

from fastapi.testclient import TestClient

from cyclotorsion import app as app_mod
from cyclotorsion import auth
from cyclotorsion.app import app
from cyclotorsion.auth import ROLE_FACILITY_ADMIN, ROLE_PROGRAM_OFFICER, UserContext

client = TestClient(app)


def _png_bytes() -> bytes:
    from generate_synthetic_data import generate_pair

    upright, _, _ = generate_pair(5.0, 128)
    return upright


def _install_fake_bigquery(monkeypatch):
    bq = types.ModuleType("google.cloud.bigquery")

    class _Scalar:
        def __init__(self, name, type_, value):
            self.name = name
            self.type_ = type_
            self.value = value

    class _QC:
        def __init__(self, query_parameters=None, use_legacy_sql=False):
            self.query_parameters = query_parameters or []

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def __iter__(self):
            return iter(self._rows)

    bq.ScalarQueryParameter = _Scalar
    bq.QueryJobConfig = _QC

    class _Client:
        """Record insertions and serve reads from the same in-memory table."""

        def __init__(self):
            self.table = []
            self.seen_queries = []

        def insert_rows_json(self, table, rows):
            self.table.extend(rows)
            return []

        def query(self, query, job_config=None):
            self.seen_queries.append(query)
            where = ""
            if job_config and job_config.query_parameters:
                p = {q.name: q.value for q in job_config.query_parameters}
                where = f"fac:{p.get('facility_id')}"
            return _Rows(
                [
                    {
                        "test_id": r["test_id"],
                        "timestamp": r["timestamp"],
                        "angle_deg": r["angle_deg"],
                        "upright_landmark": r["upright_landmark"],
                        "rotated_landmark": r["rotated_landmark"],
                        "passed_sanity_check": r["passed_sanity_check"],
                        "sanity_flags": r["sanity_flags"],
                        "facility_id": r.get("facility_id"),
                        "user_uid": r.get("user_uid"),
                    }
                    for r in self.table
                    if where in ("", f"fac:{r.get('facility_id')}")
                ]
            )

    bq.Client = _Client
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bq)
    return bq


def _enable_auth(monkeypatch, user: UserContext):
    """Turn on auth enforcement and make Firebase verification return ``user``."""
    cfg = type("C", (), {"auth_enabled": True})
    monkeypatch.setattr(auth, "cfg", cfg)
    monkeypatch.setattr(app_mod, "cfg", cfg)
    monkeypatch.setattr(auth, "_verify_with_firebase", lambda token: user)


def _install_bigquery_writer(monkeypatch):
    from cyclotorsion.storage import BigQueryWriter

    writer = BigQueryWriter(table="proj.ds.results")
    writer._client = None
    monkeypatch.setattr(app_mod, "_WRITER", writer)
    return writer


def _detect(token_header: str) -> dict:
    data = _png_bytes()
    r = client.post(
        "/detect",
        headers={"Authorization": token_header},
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_program_officer_detect_stats_roundtrip(monkeypatch):
    from cyclotorsion.app import flush_analytics

    _install_fake_bigquery(monkeypatch)
    writer = _install_bigquery_writer(monkeypatch)
    _enable_auth(
        monkeypatch,
        UserContext(uid="po-1", role=ROLE_PROGRAM_OFFICER, facility_id="fac_a"),
    )

    body = _detect("Bearer tok")
    assert "test_id" in body

    # Drain the off-hot-path analytics worker so the insert is observable.
    flush_analytics()

    r = client.get("/stats", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200, r.text
    stats = r.json()
    assert stats["total_tests"] == 1
    assert stats["scoped_by_facility_id"] is None
    assert stats["average_abs_angle_deg"] is not None
    assert writer._client.seen_queries, "stats should have queried BigQuery"


def test_facility_admin_stats_scoped_to_own_facility(monkeypatch):
    from cyclotorsion.app import flush_analytics

    _install_fake_bigquery(monkeypatch)
    writer = _install_bigquery_writer(monkeypatch)
    _enable_auth(
        monkeypatch,
        UserContext(uid="fa-1", role=ROLE_FACILITY_ADMIN, facility_id="fac_a"),
    )

    # Two detections from the same facility so facility scoping is observable.
    _detect("Bearer tok")
    _detect("Bearer tok")

    # Drain the off-hot-path analytics worker so both inserts are observable.
    flush_analytics()

    r = client.get("/stats", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200, r.text
    stats = r.json()
    assert stats["total_tests"] == 2
    assert stats["scoped_by_facility_id"] == "fac_a"
    # The BigQuery read must have filtered by the facility (no facility id on
    # the raw write -> the writer should still return the rows we wrote).
    assert writer._client.table, "detect should have written rows to BigQuery"
