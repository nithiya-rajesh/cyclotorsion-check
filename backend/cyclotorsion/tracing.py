"""Lightweight distributed tracing (TDD Section 6.3).

The TDD asks that traces correlate with logs and be exported to Cloud Trace.
Rather than pulling in a full OpenTelemetry SDK + GCP exporter as hard
dependencies, this module:

  - Reuses the per-request ``request_id`` (a 32-char hex UUID from
    ``logging_config``) as the Cloud Trace **trace id** for that request. This
    is exactly the Cloud Trace ID format, so a span and every log line of one
    request are linked by a single id — satisfying "how to correlate logs and
    traces".
  - Records **spans** (name + attributes + timing) around the clinical
    decision path: the whole ``/detect`` request and each Gemini landmark call
    (with attempt number and latency).
  - Exports spans to Cloud Trace via a **lazy, config-gated** exporter using
    ``google.cloud.trace_v2`` (only imported when ``CC_TRACING_ENABLED=1`` and
    it is actually time to flush). Without GCP creds / config, tracing is a
    transparent no-op and the app still imports and runs in dev/tests — the
    same pattern as ``BigQueryWriter``.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

from cyclotorsion.config import cfg
from cyclotorsion.logging_config import current_request_id

logger = logging.getLogger("cyclotorsion.tracing")


@dataclass
class Span:
    name: str
    start_mono: float
    trace_id: str
    attrs: dict[str, Any] = field(default_factory=dict)
    end_mono: float | None = None

    def finish(self) -> None:
        self.end_mono = time.monotonic()

    @property
    def duration_ms(self) -> float:
        if self.end_mono is None:
            return 0.0
        return (self.end_mono - self.start_mono) * 1000.0


# Holds the span list for the request currently on this context (or None).
_span_list_var: contextvars.ContextVar[list[Span] | None] = contextvars.ContextVar(
    "trace_span_list", default=None
)


class CloudTraceExporter:
    """Best-effort Cloud Trace (v2) span batch writer.

    Lazily imports the GCP client so the app stays importable without it.
    Any failure is swallowed and logged (tracing must never break a request).
    """

    def __init__(self, project_id: str | None, service_name: str):
        self.project_path = f"projects/{project_id}"
        self.service_name = service_name

    def flush(self, spans: list[Span]) -> None:
        if not spans:
            return
        try:  # noqa: BLE001 - tracing is best-effort by design
            from google.cloud import trace_v2

            client = trace_v2.TraceServiceClient()
            batch = trace_v2.BatchWriteSpansRequest(
                name=self.project_path,
                spans=[
                    trace_v2.Span(
                        name=f"{self.project_path}/traces/{s.trace_id}/spans/{id(s):x}",
                        span_id=format(id(s), "x")[:16],
                        display_name=s.name,
                        start_time=_to_timestamp(s.start_mono),
                        end_time=_to_timestamp(s.end_mono or s.start_mono),
                        attributes=_to_attributes(s),
                    )
                    for s in spans
                ],
            )
            client.batch_write_spans(request=batch)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "trace.export_failed",
                extra={"event": "trace.export_failed", "error": str(exc)},
            )


def _to_timestamp(mono: float) -> Any:
    from datetime import datetime

    epoch = time.time() - time.monotonic()  # wall-clock offset approximation
    dt = datetime.fromtimestamp(epoch + mono, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _to_attributes(spans_span: Span) -> Any:
    from google.cloud.trace_v2.types import trace

    attrs = trace.Span.Attributes()
    for k, v in spans_span.attrs.items():
        attr = attrs.attribute_map[k]
        if isinstance(v, bool):
            attr.bool_value = v
        elif isinstance(v, (int, float)):
            attr.int_value = int(v)
        else:
            attr.string_value = str(v)
    return attrs


class Tracer:
    """Records spans per request and hands them to an optional exporter.

    The exporter flush is **deferred to a background worker** (architecture
    review Major): a slow Cloud Trace ``batch_write_spans`` network call must
    never add to the clinical-response tail latency. ``end`` enqueues the flush
    (bounded, non-blocking) and returns immediately. Consumers that must observe
    flushed spans (tests) can call ``flush_background()`` to drain the queue.
    """

    def __init__(
        self,
        enabled: bool,
        exporter: CloudTraceExporter | None = None,
        worker=None,
    ):
        self.enabled = enabled
        self.exporter = exporter
        # Default to the process-wide background worker so tracing never runs
        # network I/O on the request path. Tests may inject a fresh worker.
        if worker is None:
            from cyclotorsion.background import get_worker

            worker = get_worker()
        self._worker = worker
        self._lock = threading.Lock()
        self._buffers: dict[str, list[Span]] = {}

    def flush_background(self, timeout: float | None = 10.0) -> None:
        """Drain pending exporter flushes (shutdown / deterministic tests)."""
        if self._worker is not None:
            self._worker.flush(timeout)

    def begin(self, trace_id: str) -> contextvars.Token:
        """Start a request-scope trace buffer. Returns a token for ``end``."""
        if not self.enabled:
            return _span_list_var.set(None)
        with self._lock:
            buf = self._buffers.setdefault(trace_id, [])
        return _span_list_var.set(buf)

    def end(self, token: contextvars.Token, trace_id: str) -> None:
        _span_list_var.reset(token)
        if not self.enabled:
            return
        with self._lock:
            buf = self._buffers.pop(trace_id, None)
        if buf and self.exporter is not None:
            exporter = self.exporter
            spans = list(buf)

            def _flush() -> None:
                exporter.flush(spans)

            # Off the hot path: enqueue the network flush on the background
            # worker. If the bounded queue is full the batch is dropped and
            # logged (submit handles that); tracing never blocks the request.
            if self._worker is not None:
                self._worker.submit(_flush)
            else:
                _flush()

    def record(self, name: str, **attrs: Any) -> Span:
        """Record a span into the current request's buffer (if inside one)."""
        buf = _span_list_var.get()
        span = Span(
            name=name,
            start_mono=time.monotonic(),
            trace_id=current_request_id(),
            attrs=dict(attrs),
        )
        if buf is not None:
            buf.append(span)
        return span


# --- singleton + factory -----------------------------------------------------

_tracer: Tracer | None = None
_tracer_lock = threading.Lock()


def configure_tracing() -> Tracer:
    """Build the process tracer from config. Idempotent / lazy."""
    global _tracer
    if _tracer is not None:
        return _tracer
    with _tracer_lock:
        if _tracer is not None:
            return _tracer
        enabled = cfg.tracing_enabled
        exporter = None
        if enabled and cfg.project_id:
            exporter = CloudTraceExporter(cfg.project_id, "cyclotorsion-check")
        _tracer = Tracer(enabled=enabled, exporter=exporter)
        if enabled:
            logger.info("trace.configured", extra={"event": "trace.configured"})
        return _tracer


def get_tracer() -> Tracer:
    return _tracer or configure_tracing()


def reset_tracing() -> None:
    global _tracer
    with _tracer_lock:
        _tracer = None


@contextmanager
def span(name: str, **attrs: Any):
    """Record a span for the duration of the ``with`` block (request scoped)."""
    s = get_tracer().record(name, **attrs)
    try:
        yield s
    finally:
        s.finish()
