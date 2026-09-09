"""Bounded background-queue worker for non-critical I/O (architecture review
Major).

Cloud Trace export and the analytics (BigQuery) write are **off the request hot
path**: a slow Trace/BQ call must never add to clinical-response tail latency,
and telemetry cannot be allowed to grow memory or block the handler.

Design:

  - A single daemon worker thread drains a bounded FIFO ``queue.Queue`` of
    zero-arg callables (timeout "infinity", daemon so it never prevents exit).
  - ``submit`` is non-blocking and O(1): if the queue is full the item is
    dropped and a compact log line is emitted — telemetry backpressure must
    never back up into the hot path.
  - ``flush`` blocks until currently-enqueued work is drained (for shutdown /
    deterministic tests).
  - Jobs are fire-and-forget: they are expected never to raise out (each wraps
    its own best-effort try/except, same convention as the tracing exporter).

Only cheap, idempotent, best-effort jobs belong here — never a job whose
outcome the response must reflect synchronously.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

logger = logging.getLogger("cyclotorsion.background")

_DEFAULT_MAX_PENDING = 512


class BackgroundWorker:
    """Single-drainer FIFO worker with a bounded pending queue."""

    def __init__(self, max_pending: int = _DEFAULT_MAX_PENDING) -> None:
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue(maxsize=max_pending)
        self._max_pending = max_pending
        self._drained = 0
        self._count_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name="bkg-worker", daemon=True
        )
        self._started = False
        self._stop = False
        self._start_lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------------

    def _ensure_started(self) -> None:
        with self._start_lock:
            if not self._started:
                self._thread.start()
                self._started = True

    def _run(self) -> None:
        while True:
            try:
                job = self._queue.get(timeout=None)
            except queue.Empty:  # pragma: no cover - queue never closes this way
                continue
            if self._stop and self._queue.empty():
                return
            try:
                job()
            except Exception:  # noqa: BLE001 - one bad job must not kill the drainer
                logger.exception("background.job_failed")
            finally:
                self._queue.task_done()
                with self._count_lock:
                    self._drained += 1

    # -- public API -----------------------------------------------------------

    def submit(self, job: Callable[[], None]) -> bool:
        """Queue ``job`` for background execution (non-blocking).

        Returns True if enqueued, False if the bounded queue was full (job
        dropped + logged). The caller must treat a False as "telemetry loss",
        not a request failure.
        """
        self._ensure_started()
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            logger.warning(
                "background.queue_full_dropped",
                extra={
                    "event": "background.queue_full_dropped",
                    "max_pending": self._max_pending,
                },
            )
            return False

    def flush(self, timeout: float | None = 10.0) -> None:
        """Block until all currently-enqueued jobs are drained (tests/shutdown)."""
        self._queue.join()

    @property
    def drained(self) -> int:
        with self._count_lock:
            return self._drained

    @property
    def pending(self) -> int:
        return self._queue.qsize()


# Process-wide singleton (tests can create isolated instances directly).
_worker = BackgroundWorker()
_worker_lock = threading.Lock()


def get_worker() -> BackgroundWorker:
    return _worker


def reset() -> None:
    """Replace the singleton worker (tests)."""
    global _worker
    with _worker_lock:
        _worker = BackgroundWorker()
