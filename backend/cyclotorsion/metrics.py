"""Custom metrics registry (TDD Section 6.1).

Implements the four recommended custom metrics without adding a heavyweight
telemetry SDK, consistent with the document's "narrow, justified tooling"
philosophy:

    gemini_api_latency_seconds     histogram — isolates AI-provider latency
    gemini_retry_count             counter   — transient 503/quota signals
    sanity_check_flag_rate         counter pair — clinical-quality signal
    bigquery_write_failure_rate    counter pair — silent analytics-path health

Design:
  - **In-process registry** of counters/histograms (dependency-free, testable,
    works in local dev).
  - **Prometheus text exposition** via ``render_prometheus()`` served at
    ``/metrics``. Cloud Monitoring's managed Prometheus / an OTel collector
    (externally deployed, not bundled) can scrape this — no GCP SDK inside the
    app. This is the same "the app stays importable and runnable without cloud
    creds" pattern used elsewhere (mock detector, in-memory writer).
  - Labels are kept minimal and deliberately exclude any patient-identifiable
    dimension.
"""

from __future__ import annotations

import threading


class Counter:
    """A monotonic counter with optional fixed label set."""

    def __init__(self, name: str, help: str, labels: dict[str, str] | None = None):
        self.name = name
        self.help = help
        self.labels = dict(labels or {})
        self._value = 0.0
        # Increments are guarded so concurrent /detect requests (the app's
        # supported mode) never lose updates (architecture review Minor).
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value += amount

    def render(self) -> str:
        name = self.name
        labels = _label_suffix(self.labels)
        return (
            f"# HELP {name} {self.help}\n"
            f"# TYPE {name} counter\n"
            f"{name}{labels} {_fmt(self._value)}\n"
        )


class Histogram:
    """A histogram of observed values with fixed label set."""

    def __init__(
        self,
        name: str,
        help: str,
        labels: dict[str, str] | None = None,
        buckets: tuple[float, ...] = (0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    ):
        self.name = name
        self.help = help
        self.labels = dict(labels or {})
        self.buckets = buckets
        self._count = 0.0
        self._sum = 0.0
        self._buckets = {b: 0.0 for b in buckets}
        self._buckets[float("inf")] = 0.0
        # observe() is guarded so concurrent requests cannot lose updates.
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self._count += 1
            self._sum += value
            for b in self.buckets:
                if value <= b:
                    self._buckets[b] += 1
            self._buckets[float("inf")] += 1

    def render(self) -> str:
        lines = [
            f"# HELP {self.name} {self.help}",
            f"# TYPE {self.name} histogram",
        ]
        for b in self.buckets:
            le = _label_suffix({**self.labels, "le": _fmt(b)})
            lines.append(f"{self.name}_bucket{le} {_fmt(self._buckets[b])}")
        inf = _label_suffix({**self.labels, "le": "+Inf"})
        lines.append(f"{self.name}_bucket{inf} {_fmt(self._buckets[float('inf')])}")
        lines.append(f"{self.name}_sum{_label_suffix(self.labels)} {_fmt(self._sum)}")
        lines.append(
            f"{self.name}_count{_label_suffix(self.labels)} {_fmt(self._count)}"
        )
        return "\n".join(lines) + "\n"


def _fmt(value: float) -> str:
    return f"{value:g}"


def _label_suffix(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = ",".join(f'{k}="{v}"' for k, v in labels.items())
    return "{" + parts + "}"


class Registry:
    """Thread-safe container of metrics."""

    def __init__(self) -> None:
        self._metrics: dict[str, Counter | Histogram] = {}
        self._lock = threading.Lock()

    def make_counter(
        self, name: str, help: str, labels: dict[str, str] | None = None
    ) -> Counter:
        key = (name, tuple(sorted((labels or {}).items())))
        with self._lock:
            metric = self._metrics.get(key)
            if metric is None:
                metric = Counter(name, help, labels)
                self._metrics[key] = metric
            return metric  # type: ignore[return-value]

    def make_histogram(
        self, name: str, help: str, labels: dict[str, str] | None = None
    ) -> Histogram:
        key = (name, tuple(sorted((labels or {}).items())))
        with self._lock:
            metric = self._metrics.get(key)
            if metric is None:
                metric = Histogram(name, help, labels)
                self._metrics[key] = metric
            return metric  # type: ignore[return-value]

    def render(self) -> str:
        with self._lock:
            return "".join(m.render() for m in self._metrics.values())


# Process-wide singleton.
_registry = Registry()
_registry_lock = threading.Lock()


def get_registry() -> Registry:
    return _registry


def reset() -> None:
    """Reset the registry (tests)."""
    global _registry
    with _registry_lock:
        _registry = Registry()


# --- Convenience recorders (called from detectors / app) --------------------


def gemini_api_latency(seconds: float) -> None:
    get_registry().make_histogram(
        "gemini_api_latency_seconds", "Latency of a Gemini landmark-detection call"
    ).observe(seconds)


def gemini_retry_inc() -> None:
    get_registry().make_counter(
        "gemini_retry_count", "Total transient retries against the Gemini API"
    ).inc()


def detection_recorded(passed: bool, flagged: bool) -> None:
    reg = get_registry()
    reg.make_counter("sanity_checks_total", "Total detections processed").inc()
    if not passed or flagged:
        reg.make_counter(
            "sanity_check_flags_total",
            "Detections that raised a sanity flag (did not pass)",
        ).inc()


def analytics_write(failed: bool) -> None:
    reg = get_registry()
    reg.make_counter("bigquery_writes_total", "Analytics writes attempted").inc()
    if failed:
        reg.make_counter(
            "bigquery_write_failures_total",
            "Analytics writes that failed (non-blocking)",
        ).inc()
