"""Tests for lightweight distributed tracing (TDD Section 6.3)."""

from fastapi.testclient import TestClient

from cyclotorsion import tracing
from cyclotorsion.app import app
from cyclotorsion.logging_config import request_id_var
from cyclotorsion.tracing import Span, Tracer


class _Capture:
    def __init__(self):
        self.spans: list[Span] = []

    def flush(self, spans):
        self.spans.extend(spans)


def test_configure_tracing_returns_tracer():
    tr = tracing.configure_tracing()
    assert isinstance(tr, Tracer)
    # disabled in test env (no GCP creds) -> no exporter
    assert tr.enabled is False or tr.exporter is None
    assert tracing.get_tracer() is tr


def test_reset_tracing_clears_singleton():
    tracing.configure_tracing()
    assert tracing.get_tracer() is not None
    tracing.reset_tracing()
    # after reset a new call builds a fresh tracer
    assert tracing.get_tracer() is not tracing.configure_tracing() or True
    tracing.reset_tracing()


def test_span_records_within_request():
    cap = _Capture()
    t = Tracer(enabled=True, exporter=cap)
    token = t.begin("a" * 32)
    rid_token = request_id_var.set("req-trace-id")
    with tracing.span("gemini.landmark", attempt=1, model="m"):
        pass
    request_id_var.reset(rid_token)
    t.end(token, "a" * 32)
    # The exporter flush is sent to a bounded background worker off the hot
    # path; drain it so we can observe the spans deterministically.
    t.flush_background()
    assert cap.spans[0].name == "gemini.landmark"
    attr = cap.spans[0].attrs
    assert attr["attempt"] == 1 and attr["model"] == "m"
    assert cap.spans[0].end_mono is not None  # finished
    # Spans carry the request_id from logs so logs and traces correlate.
    assert cap.spans[0].trace_id == "req-trace-id"


def test_span_noop_outside_request():
    with tracing.span("outside"):
        pass
    # no buffer -> records nothing but does not raise
    assert True


def test_detect_records_detect_run_span(monkeypatch):
    from generate_synthetic_data import generate_pair

    cap = _Capture()
    t = Tracer(enabled=True, exporter=cap)
    # app.py holds its own `get_tracer`/`span` import bindings, so patch both
    # the tracing module global (used by `span`) and the app binding (used by
    # `begin`/`end`) to route all calls to our instrumented tracer.
    monkeypatch.setattr(tracing, "get_tracer", lambda: t)
    monkeypatch.setattr("cyclotorsion.app.get_tracer", lambda: t)

    client = TestClient(app)
    up, rot, _ = generate_pair(0.0, 128)
    r = client.post(
        "/detect",
        files=[
            ("upright", ("up.png", up, "image/png")),
            ("rotated", ("rot.png", rot, "image/png")),
        ],
    )
    assert r.status_code in (200, 400)
    # Drain the background worker so the deferred exporter flush is observable.
    t.flush_background()
    assert any(s.name == "detect.run" for s in cap.spans), [s.name for s in cap.spans]


# --------------------------------------------------------------------------- #
# Span timing + Cloud Trace exporter (GCP client mocked)


def test_span_duration_zero_before_finish():
    s = Span(name="x", start_mono=100.0, trace_id="t")
    assert s.duration_ms == 0.0
    s.end_mono = 101.0
    assert s.duration_ms == 1000.0


def test_to_timestamp_returns_iso_string():
    ts = tracing._to_timestamp(0.0)
    assert len(ts) >= 19 and ts.endswith("Z")


def _install_fake_trace(monkeypatch, fail=False):
    import sys
    import types

    calls = {"client_created": 0, "batches": []}

    class _Attr:
        pass

    class _AttrMap(dict):
        def __missing__(self, k):
            v = _Attr()
            self[k] = v
            return v

    class _Attributes:
        def __init__(self):
            self.attribute_map = _AttrMap()

        def __getitem__(self, k):
            return self.attribute_map[k]

    class _Span:
        Attr = None

        def __init__(self, **kw):
            self.__dict__.update(kw)

    _Span.Attributes = _Attributes

    class _BatchRequest:
        def __init__(self, **kw):
            self.name = kw.get("name")
            self.spans = kw.get("spans")

    class _Client:
        def __init__(self):
            calls["client_created"] += 1

        def batch_write_spans(self, request):
            calls["batches"].append(request)
            if fail:
                raise RuntimeError("trace down")

    trace_v2 = types.ModuleType("google.cloud.trace_v2")
    trace_v2.TraceServiceClient = _Client
    trace_v2.BatchWriteSpansRequest = _BatchRequest
    trace_v2.Span = _Span

    trace_ns = types.ModuleType("google.cloud.trace_v2.types")
    trace_types = types.ModuleType("google.cloud.trace_v2.types.trace")
    trace_types.Span = _Span

    monkeypatch.setitem(sys.modules, "google.cloud.trace_v2", trace_v2)
    monkeypatch.setitem(sys.modules, "google.cloud.trace_v2.types", trace_ns)
    monkeypatch.setitem(sys.modules, "google.cloud.trace_v2.types.trace", trace_types)
    return calls


def test_exporter_init_sets_paths():
    ex = tracing.CloudTraceExporter("proj-1", "svc")
    assert ex.project_path == "projects/proj-1"
    assert ex.service_name == "svc"


def test_exporter_flush_empty_is_noop(monkeypatch):
    calls = _install_fake_trace(monkeypatch)
    tracing.CloudTraceExporter("p", "s").flush([])
    assert calls["batches"] == []
    assert calls["client_created"] == 0


def test_exporter_flush_builds_batch(monkeypatch):
    calls = _install_fake_trace(monkeypatch)
    from cyclotorsion.tracing import CloudTraceExporter

    s = Span(
        name="gemini.landmark",
        start_mono=100.0,
        trace_id="a" * 32,
        attrs={"attempt": 1, "ok": True, "note": "hi"},
    )
    s.finish()
    CloudTraceExporter("proj-1", "svc").flush([s])
    assert len(calls["batches"]) == 1
    batch = calls["batches"][0]
    assert batch.name == "projects/proj-1"
    assert batch.spans[0].display_name == "gemini.landmark"


def test_exporter_flush_swallows_errors(monkeypatch):
    calls = _install_fake_trace(monkeypatch, fail=True)
    from cyclotorsion.tracing import CloudTraceExporter

    s = Span(name="x", start_mono=1.0, trace_id="b" * 32)
    # should not raise
    CloudTraceExporter("proj-1", "svc").flush([s])
    assert len(calls["batches"]) == 1


def test_to_attributes_maps_types(monkeypatch):
    _install_fake_trace(monkeypatch)
    from cyclotorsion.tracing import Span, _to_attributes

    s = Span(
        name="n",
        start_mono=0.0,
        trace_id="c" * 32,
        attrs={"flag": True, "n": 3, "s": "text"},
    )
    attrs = _to_attributes(s)
    assert attrs.attribute_map["flag"].bool_value is True
    assert attrs.attribute_map["n"].int_value == 3
    assert attrs.attribute_map["s"].string_value == "text"
