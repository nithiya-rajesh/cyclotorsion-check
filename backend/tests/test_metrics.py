"""Tests for custom metrics registry + /metrics endpoint (TDD Section 6.1)."""

from fastapi.testclient import TestClient

from cyclotorsion import metrics
from cyclotorsion.app import app


def test_counter_inc_and_render():
    metrics.reset()
    c = metrics.get_registry().make_counter("x_total", "desc")
    c.inc()
    c.inc(2)
    out = c.render()
    assert "# TYPE x_total counter" in out
    assert "x_total 3" in out


def test_histogram_buckets_sum_count():
    metrics.reset()
    h = metrics.get_registry().make_histogram("h_seconds", "desc")
    h.observe(0.2)
    h.observe(1.5)
    out = h.render()
    assert "h_seconds_count 2" in out
    assert "h_seconds_sum 1.7" in out
    # 0.2 and 1.5 both fall in the <=2.5 bucket
    assert 'le="2.5"' in out


def test_registry_dedupes_by_name_and_labels():
    metrics.reset()
    reg = metrics.get_registry()
    a = reg.make_counter("c_total", "desc", {"facility": "A"})
    b = reg.make_counter("c_total", "desc", {"facility": "A"})
    d = reg.make_counter("c_total", "desc", {"facility": "B"})
    assert a is b
    assert a is not d


def test_reset_clears_registry():
    metrics.reset()
    metrics.gemini_retry_inc()
    reg = metrics.get_registry()
    assert reg._metrics
    metrics.reset()
    assert not metrics.get_registry()._metrics


def test_convenience_recorders_populate_metrics():
    metrics.reset()
    metrics.gemini_api_latency(0.3)
    metrics.gemini_retry_inc()
    metrics.detection_recorded(passed=False, flagged=True)
    metrics.analytics_write(failed=True)
    out = metrics.get_registry().render()
    assert "gemini_api_latency_seconds_count 1" in out
    assert "gemini_retry_count 1" in out
    assert "sanity_checks_total 1" in out
    assert "sanity_check_flags_total 1" in out
    assert "bigquery_writes_total 1" in out
    assert "bigquery_write_failures_total 1" in out


def test_metrics_endpoint_returns_prometheus():
    metrics.reset()
    client = TestClient(app)
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "gemini_retry_count" not in r.text  # registry was empty


def test_metrics_reflect_detect_activity():
    from generate_synthetic_data import generate_pair

    metrics.reset()
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
    out = client.get("/metrics").text
    assert "sanity_checks_total" in out


def test_counter_increment_safe_under_concurrency():
    """Regression (architecture review Minor): concurrent increments must never
    lose updates. Counters/histograms are shared across /detect requests.
    """
    import threading

    metrics.reset()
    metrics.reset()  # ensure a fresh registry
    c = metrics.get_registry().make_counter("c_total", "desc")

    n_threads = 8
    per_thread = 1000
    barrier = threading.Barrier(n_threads)

    def worker():
        barrier.wait()  # maximize contention
        for _ in range(per_thread):
            c.inc()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert c._value == n_threads * per_thread


def test_histogram_observe_safe_under_concurrency():
    import threading

    metrics.reset()
    h = metrics.get_registry().make_histogram("h_seconds", "desc")

    n_threads = 8
    per_thread = 500
    barrier = threading.Barrier(n_threads)

    def worker():
        barrier.wait()
        for _ in range(per_thread):
            h.observe(1.0)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert h._count == n_threads * per_thread
    assert h._sum == n_threads * per_thread * 1.0
