"""API tests using FastAPI's TestClient (httpx)."""

from fastapi.testclient import TestClient

from cyclotorsion.app import app

client = TestClient(app)


def _png_bytes() -> bytes:
    from generate_synthetic_data import generate_pair

    upright, _, _ = generate_pair(0.0, 128)
    return upright


def _upload(name: str = "photo.png", data: bytes | None = None):
    return {"filename": name, "content": data or _png_bytes()}


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_detect_success():
    data = _png_bytes()
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    assert "angle_deg" in body
    assert "passed_sanity_check" in body
    assert "test_id" in body


def test_detect_missing_slot():
    data = _png_bytes()
    r = client.post("/detect", files=[("upright", ("u.png", data, "image/png"))])
    assert r.status_code == 422  # FastAPI required-field validation


def test_detect_rejects_wrong_mime():
    data = b"not really an image but a text file"
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.txt", data, "text/plain")),
            ("rotated", ("r.png", _png_bytes(), "image/png")),
        ],
    )
    assert r.status_code == 415


def test_detect_with_case_fields_returns_corrected_axis():
    # PRD Epic 6 / US-6.2: when a target_axis_deg is supplied, the corrected
    # axis is computed from it server-side (real, not the 85 placeholder). All
    # three Epic 6 fields are optional form fields.
    data = _png_bytes()
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
        data={
            "case_ref": "CASE-2026-001",
            "eye_laterality": "OD",
            "target_axis_deg": "85",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["case_ref"] == "CASE-2026-001"
    assert body["eye_laterality"] == "OD"
    assert body["corrected_axis_deg"] == 85.0  # 85 + 0 rotation (same image)


def test_detect_case_fields_are_optional():
    # US-6.1/6.2/6.3 AC: the core Epic 1-3 workflow is unchanged with no case
    # reference attached. No case fields must be present in the response.
    data = _png_bytes()
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    assert "case_ref" not in body
    assert "corrected_axis_deg" not in body


def test_detect_rejects_invalid_eye_laterality():
    data = _png_bytes()
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
        data={"eye_laterality": "OU"},
    )
    assert r.status_code == 422
    assert "OD" in r.json()["detail"]


def test_detect_rejects_out_of_range_target_axis():
    data = _png_bytes()
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
        data={"target_axis_deg": "190"},
    )
    assert r.status_code == 422


def test_detect_persists_case_to_cases_table(monkeypatch):
    # TDD Section 3.2: case-level context is upserted into the `cases` table
    # keyed by case_ref, linking a detection to its clinical context.
    from cyclotorsion import app as app_mod
    from cyclotorsion.storage import InMemoryWriter

    w = InMemoryWriter()
    monkeypatch.setattr(app_mod, "_WRITER", w)
    from cyclotorsion.background import BackgroundWorker

    worker = BackgroundWorker()
    monkeypatch.setattr(app_mod, "_ANALYTICS_WORKER", worker)

    data = _png_bytes()
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
        data={"case_ref": "CASE-2026-002", "target_axis_deg": "90"},
    )
    assert r.status_code == 200
    app_mod.flush_analytics()
    assert "CASE-2026-002" in w.cases
    assert w.cases["CASE-2026-002"].target_axis_deg == 90.0
    assert w.cases["CASE-2026-002"].eye_laterality is None


def test_detect_rejects_oversized_file():
    big = b"x" * (10 * 1024 * 1024 + 1)
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", big, "image/png")),
            ("rotated", ("r.png", _png_bytes(), "image/png")),
        ],
    )
    assert r.status_code == 413


def test_detect_rejects_decompression_bomb_dimensions():
    # MEDIUM-4: an image declaring huge dimensions (decompression bomb) must be
    # rejected (413) before any full decode / memory exhaustion. A valid, solid
    # PNG with dimensions strictly above the pixel cap is tiny on disk yet huge
    # decoded — the exact bomb shape.
    import io
    import math

    from cyclotorsion.app import MAX_IMAGE_PIXELS

    side = math.isqrt(MAX_IMAGE_PIXELS) + 2  # side^2 > MAX_IMAGE_PIXELS
    from PIL import Image

    buf = io.BytesIO()
    Image.new("L", (side, side), 0).save(buf, format="PNG")
    bomb = buf.getvalue()
    assert bomb and len(bomb) < 1_000_000  # small on disk, huge decoded

    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", bomb, "image/png")),
            ("rotated", ("r.png", _png_bytes(), "image/png")),
        ],
    )
    assert r.status_code == 413


def test_detect_rejects_corrupt_image_with_400():
    # Architecture review Minor: a corrupted / undecodable image must fail fast
    # (400) rather than silently fall back to placeholder dimensions and produce
    # a misleading clinical result.
    corrupt = b"\x89PNG\r\n\x1a\n this is not a real png payload"
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", corrupt, "image/png")),
            ("rotated", ("r.png", _png_bytes(), "image/png")),
        ],
    )
    assert r.status_code == 400
    assert "corrupt" in r.json()["detail"].lower()


def test_stats_aggregate():
    # Run one detect to seed the in-memory writer, then check stats shape.
    # The analytics write is enqueued on a background worker off the hot path,
    # so drain it before reading stats (read-after-write determinism).
    from cyclotorsion.app import flush_analytics

    data = _png_bytes()
    client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
    )
    flush_analytics()
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "total_tests",
        "average_abs_angle_deg",
        "sanity_check_pass_rate",
    }
    assert isinstance(body["total_tests"], int)


def test_detect_protected_without_token_when_auth_enabled(monkeypatch):
    # Turn on enforcement after the app imported (dependency reads cfg at
    # request time), then verify /detect fails closed with 401 for a request
    # carrying no Authorization header.
    from cyclotorsion import auth as auth_mod

    monkeypatch.setattr(auth_mod, "cfg", type("C", (), {"auth_enabled": True})())
    data = _png_bytes()
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
    )
    assert r.status_code == 401


def test_detect_fails_closed_in_production_with_auth_disabled(monkeypatch):
    # HIGH-1: even a misconfigured production deploy (auth off) must NOT run open.
    from cyclotorsion import auth as auth_mod

    monkeypatch.setattr(
        auth_mod,
        "cfg",
        type("C", (), {"auth_enabled": False, "app_env": "production"})(),
    )
    data = _png_bytes()
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
    )
    assert r.status_code == 401


def test_stats_fails_closed_in_production_with_auth_disabled(monkeypatch):
    from cyclotorsion import auth as auth_mod

    monkeypatch.setattr(
        auth_mod,
        "cfg",
        type("C", (), {"auth_enabled": False, "app_env": "production"})(),
    )
    r = client.get("/stats")
    assert r.status_code == 401


def test_detect_analytics_write_failure_stays_internal_and_off_path(monkeypatch):
    # Architecture review Major: the analytics (BigQuery) write is enqueued on a
    # background worker off the request hot path. A write failure must never
    # (a) leak internal text into the response, nor (b) block the clinical
    # result. This asserts the response is clean and that the failure is
    # recorded server-side (metric) rather than echoed to the client.
    from cyclotorsion import app as app_mod
    from cyclotorsion import metrics as metrics_mod

    writes = []

    class _ThrowingWriter:
        def write_row(self, row):
            writes.append(row)
            return RuntimeError(
                "gcloud projects/secret-proj/zones/us-central1 FAILED @ SELECT ..."
            )

        def read_rows(self, facility_id=None):
            return [], None

    monkeypatch.setattr(app_mod, "_WRITER", _ThrowingWriter())
    # Isolated background worker so we can drain deterministically.
    from cyclotorsion.background import BackgroundWorker

    worker = BackgroundWorker()
    monkeypatch.setattr(app_mod, "_ANALYTICS_WORKER", worker)

    # Reset the failure-rate metric so we can observe the recorded failure.
    metrics_mod.reset()

    data = _png_bytes()
    r = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    # No internal fragment, and NO per-request write warning anymore: the write
    # is off-path, so the response cannot leak anything about it.
    assert "logging_warning" not in body
    assert "projects/" not in str(body)
    assert "SELECT" not in str(body)

    # Drain the background worker so the write actually executes.
    app_mod.flush_analytics()
    assert len(writes) == 1  # the row was enqueued and written in the background

    # The failure is recorded as a metric (bigquery_write_failures_total), the
    # observability path, not returned to the client.
    registry = metrics_mod.get_registry()
    rendered = registry.render()
    assert "bigquery_writes_total 1" in rendered
    assert "bigquery_write_failures_total 1" in rendered


def test_stats_protected_without_token_when_auth_enabled(monkeypatch):
    from cyclotorsion import auth as auth_mod

    monkeypatch.setattr(auth_mod, "cfg", type("C", (), {"auth_enabled": True})())
    r = client.get("/stats")
    assert r.status_code == 401


def test_stats_aggregate_from_in_memory_writer(monkeypatch):
    # Patch the app's writer with a pre-seeded in-memory one and confirm /stats
    # returns the computed aggregate (not zeros).
    from cyclotorsion import app as app_mod
    from cyclotorsion.storage import InMemoryWriter, ResultRow

    def _row(angle, passed, facility=None):
        return ResultRow(
            test_id="t",
            timestamp="2026-01-01T00:00:00+00:00",
            angle_deg=angle,
            upright_landmark="up",
            rotated_landmark="rot",
            passed_sanity_check=passed,
            sanity_flags="",
            facility_id=facility,
        )

    w = InMemoryWriter()
    w.write_row(_row(2.0, True))
    w.write_row(_row(-4.0, False))
    monkeypatch.setattr(app_mod, "_WRITER", w)
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total_tests"] == 2
    assert body["average_abs_angle_deg"] == 3.0  # (2 + 4) / 2
    assert body["sanity_check_pass_rate"] == 0.5


def test_stats_reads_from_bigquery_writer(monkeypatch):
    # /stats must query the BigQuery writer (not fall back to an in-memory
    # `.rows` list that does not exist), proving production stats work.
    import sys
    import types

    from cyclotorsion import app as app_mod
    from cyclotorsion.storage import BigQueryWriter

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def __iter__(self):
            return iter(self._rows)

    bq = types.ModuleType("google.cloud.bigquery")

    class _Scalar:
        def __init__(self, name, t, value):
            self.name = name
            self.value = value

    class _QC:
        def __init__(self, query_parameters=None, use_legacy_sql=False):
            self.query_parameters = query_parameters or []

    bq.ScalarQueryParameter = _Scalar
    bq.QueryJobConfig = _QC

    class _Client:
        def __init__(self):
            self.seen_queries = []
            self._rows = [
                {
                    "test_id": "a",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "angle_deg": 3.0,
                    "upright_landmark": "up",
                    "rotated_landmark": "rot",
                    "passed_sanity_check": True,
                    "sanity_flags": "",
                    "facility_id": None,
                    "user_uid": None,
                }
            ]

        def query(self, query, job_config=None):
            self.seen_queries.append(query)
            return _FakeResult(self._rows)

    bq_client = _Client()
    bq.Client = lambda: bq_client
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bq)

    monkeypatch.setattr(app_mod, "_WRITER", BigQueryWriter(table="proj.ds.results"))
    app_mod._WRITER._client = bq_client
    resp = client.get("/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_tests"] == 1
    assert body["average_abs_angle_deg"] == 3.0
    assert body["sanity_check_pass_rate"] == 1.0
    assert bq_client.seen_queries, "BigQueryWriter.read_rows should have run a query"


def test_stats_surfaces_analytics_read_failure(monkeypatch):
    # Architecture review Major: a BigQuery read failure must surface an
    # analytics_warning on /stats rather than silently read as "no data".
    from cyclotorsion import app as app_mod

    class _FlakyWriter:
        def write_row(self, row):
            return None

        def read_rows(self, facility_id=None):
            return [], RuntimeError("gcloud projects/secret-proj SELECT broke")

    monkeypatch.setattr(app_mod, "_WRITER", _FlakyWriter())
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["analytics_warning"] == "analytics read failed"
    assert body["total_tests"] == 0
    # No internal fragment leaks into the response.
    assert "projects/" not in str(body)
    assert "SELECT" not in str(body)
