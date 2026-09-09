"""Tests for the persistence writers (in-memory + BigQuery backends)."""

import sys
import types

from cyclotorsion.storage import (
    BigQueryWriter,
    CaseRow,
    InMemoryWriter,
    ResultRow,
    writer_from_config,
)


def _row(**overrides):
    data = dict(
        test_id="t",
        timestamp="2026-01-01T00:00:00+00:00",
        angle_deg=1.5,
        upright_landmark="up",
        rotated_landmark="rot",
        passed_sanity_check=True,
        sanity_flags="",
    )
    data.update(overrides)
    return ResultRow(**data)


class _FakeBigQueryClient:
    def __init__(self):
        self.inserted = []
        self._rows = []
        self.queries = []
        self.job_configs = []
        self._fail_query = None

    def insert_rows_json(self, table, rows):
        self.inserted.append((table, rows))
        if self._fail_query is not None:
            raise self._fail_query

    def query(self, query, job_config=None):
        self.queries.append(query)
        self.job_configs.append(job_config)
        if self._fail_query is not None:
            raise self._fail_query
        return self._rows

    def set_rows(self, rows):
        self._rows = rows

    def set_query_failure(self, exc):
        self._fail_query = exc


def _install_fake_bigquery(monkeypatch, client):
    bigquery = types.ModuleType("google.cloud.bigquery")

    class _ScalarQueryParameter:
        def __init__(self, name, type_, value):
            self.name = name
            self.type_ = type_
            self.value = value

    class _QueryJobConfig:
        def __init__(self, query_parameters=None, use_legacy_sql=False):
            self.query_parameters = query_parameters or []
            self.use_legacy_sql = use_legacy_sql

    bigquery.ScalarQueryParameter = _ScalarQueryParameter
    bigquery.QueryJobConfig = _QueryJobConfig

    def _client_factory():
        return client

    bigquery.Client = _client_factory
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bigquery)
    return bigquery


def test_in_memory_writer_appends():
    w = InMemoryWriter()
    r = _row(angle_deg=3.0)
    assert w.write_row(r) is None
    assert w.rows == [r]


def test_bigquery_writer_success(monkeypatch):
    client = _FakeBigQueryClient()
    _install_fake_bigquery(monkeypatch, client)
    w = BigQueryWriter(table="proj.dataset.results")
    err = w.write_row(_row())
    assert err is None
    assert len(client.inserted) == 1
    table, rows = client.inserted[0]
    assert table == "proj.dataset.results"
    assert rows[0]["test_id"] == "t"


def test_bigquery_writer_returns_exception_on_failure(monkeypatch):
    client = _FakeBigQueryClient()
    client.set_query_failure(RuntimeError("network down"))
    _install_fake_bigquery(monkeypatch, client)
    w = BigQueryWriter()
    err = w.write_row(_row())
    assert isinstance(err, RuntimeError)


def test_bigquery_writer_default_table():
    w = BigQueryWriter()
    assert w._table == "cyclotorsion_check.results"


def _bq_row_dict(facility_id=None, angle=1.5, passed=True, case_ref=None):
    return {
        "test_id": "t",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "angle_deg": angle,
        "upright_landmark": "up",
        "rotated_landmark": "rot",
        "passed_sanity_check": passed,
        "sanity_flags": "",
        "facility_id": facility_id,
        "user_uid": None,
        "case_ref": case_ref,
    }


def test_bigquery_writer_read_rows_all(monkeypatch):
    client = _FakeBigQueryClient()
    client.set_rows(
        [
            _bq_row_dict(facility_id="f1", angle=2.0),
            _bq_row_dict(facility_id="f2", angle=4.0),
        ]
    )
    _install_fake_bigquery(monkeypatch, client)
    w = BigQueryWriter(table="proj.dataset.results")
    rows, err = w.read_rows()
    assert err is None
    assert len(rows) == 2
    assert rows[0].facility_id == "f1"
    assert rows[0].angle_deg == 2.0
    assert rows[0].passed_sanity_check is True
    assert "FROM `proj.dataset.results`" in client.queries[0]
    assert "WHERE" not in client.queries[0]


def test_bigquery_writer_read_rows_facility_filter(monkeypatch):
    client = _FakeBigQueryClient()
    client.set_rows([_bq_row_dict(facility_id="f1")])
    _install_fake_bigquery(monkeypatch, client)
    w = BigQueryWriter(table="proj.dataset.results")
    rows, err = w.read_rows(facility_id="f1")
    assert err is None
    assert len(rows) == 1
    assert "WHERE facility_id = @facility_id" in client.queries[0]
    params = client.job_configs[0].query_parameters
    assert len(params) == 1
    assert params[0].name == "facility_id"
    assert params[0].value == "f1"


def test_bigquery_writer_read_rows_failure_returns_empty_and_error(monkeypatch):
    class _BoomQuery:
        def insert_rows_json(self, table, rows):
            pass

        def query(self, query, job_config=None):
            raise RuntimeError("query failed")

    _install_fake_bigquery(monkeypatch, _BoomQuery())
    w = BigQueryWriter()
    rows, err = w.read_rows()
    assert rows == []
    assert isinstance(err, RuntimeError)


def test_bigquery_writer_client_init_failure_logs_and_propagates(monkeypatch):
    import types

    def _boom_client():
        raise RuntimeError("no credentials")

    bigquery = types.ModuleType("google.cloud.bigquery")
    bigquery.Client = _boom_client
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bigquery)
    w = BigQueryWriter()
    # _get_client surfaces the init failure; write_row turns it into a warning.
    err = w.write_row(_row())
    assert isinstance(err, RuntimeError)
    # read_rows catches the same init failure and returns ([], error).
    rows, rerr = w.read_rows()
    assert rows == []
    assert isinstance(rerr, RuntimeError)


def test_in_memory_writer_read_rows_all_and_filtered():
    w = InMemoryWriter()
    w.write_row(_row(facility_id="f1", angle_deg=1.0))
    w.write_row(_row(facility_id="f2", angle_deg=2.0))
    rows, err = w.read_rows()
    assert err is None
    assert len(rows) == 2
    rows, err = w.read_rows(facility_id="f1")
    assert err is None
    assert len(rows) == 1
    assert rows[0].facility_id == "f1"
    rows, err = w.read_rows(facility_id="nope")
    assert err is None
    assert rows == []


def test_writer_from_config_uses_use_bigquery(monkeypatch):
    import dataclasses

    from cyclotorsion.config import cfg

    # writer_from_config reads `cfg` via a local import of config.cfg.
    patched = dataclasses.replace(cfg, storage_mode="bigquery")
    monkeypatch.setattr("cyclotorsion.config.cfg", patched)
    w = writer_from_config()
    assert isinstance(w, BigQueryWriter)
    assert w._table == patched.bigquery_table


def test_writer_from_config_in_memory(monkeypatch):
    import dataclasses

    from cyclotorsion.config import cfg

    patched = dataclasses.replace(cfg, storage_mode="memory")
    monkeypatch.setattr("cyclotorsion.config.cfg", patched)
    w = writer_from_config()
    assert isinstance(w, InMemoryWriter)


def _case(case_ref="CASE-2026-001", target_axis_deg=85.0, eye="OD", facility=None):
    return CaseRow(
        case_ref=case_ref,
        created_by="uid-1",
        created_at="2026-01-01T00:00:00+00:00",
        eye_laterality=eye,
        target_axis_deg=target_axis_deg,
        facility_id=facility,
    )


def test_result_row_carries_case_ref():
    r = _row(case_ref="CASE-2026-001")
    assert r.case_ref == "CASE-2026-001"
    # Defaults to None (fully optional, PRD Epic 6).
    assert _row().case_ref is None


def test_in_memory_case_upsert_keyed_by_case_ref():
    w = InMemoryWriter()
    assert w.upsert_case(_case()) is None
    assert w.cases["CASE-2026-001"].target_axis_deg == 85.0
    # A retry (same case_ref) updates rather than duplicates (one -> many).
    w.upsert_case(_case(target_axis_deg=95.0))
    assert len(w.cases) == 1
    assert w.cases["CASE-2026-001"].target_axis_deg == 95.0


def test_bigquery_case_upsert_success(monkeypatch):
    client = _FakeBigQueryClient()
    _install_fake_bigquery(monkeypatch, client)
    w = BigQueryWriter(table="proj.dataset.results", cases_table="proj.dataset.cases")
    err = w.upsert_case(_case(target_axis_deg=85.0))
    assert err is None
    # MERGE targets the `cases` table, keyed on case_ref (TDD Section 3.2).
    assert "proj.dataset.cases" in client.queries[0]
    assert "MERGE" in client.queries[0]
    params = client.job_configs[0].query_parameters
    names = {p.name for p in params}
    assert {"case_ref", "target_axis_deg", "eye_laterality"} <= names


def test_bigquery_case_upsert_failure_returns_error(monkeypatch):
    client = _FakeBigQueryClient()
    client.set_query_failure(RuntimeError("MERGE failed"))
    _install_fake_bigquery(monkeypatch, client)
    w = BigQueryWriter()
    err = w.upsert_case(_case())
    assert isinstance(err, RuntimeError)


def test_bigquery_read_rows_hydrates_case_ref(monkeypatch):
    client = _FakeBigQueryClient()
    client.set_rows([_bq_row_dict(case_ref="CASE-2026-001")])
    _install_fake_bigquery(monkeypatch, client)
    w = BigQueryWriter(table="proj.dataset.results")
    rows, err = w.read_rows()
    assert err is None
    assert rows[0].case_ref == "CASE-2026-001"
