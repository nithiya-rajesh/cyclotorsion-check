"""Tests for the bounded background-queue worker (architecture review Major)."""

import threading

from cyclotorsion.background import BackgroundWorker


def test_worker_executes_enqueued_jobs():
    w = BackgroundWorker()
    ran = []
    w.submit(lambda: ran.append("a"))
    w.submit(lambda: ran.append("b"))
    w.flush()
    assert ran == ["a", "b"]
    assert w.drained == 2
    assert w.pending == 0


def test_worker_is_non_blocking_and_bounded_drops_when_full():
    w = BackgroundWorker(max_pending=2)
    # Fill the bounded queue faster than the drainer can drain by blocking the
    # drainer thread on the first job.
    blocker = threading.Event()
    start = threading.Event()

    def first_job():
        start.set()
        blocker.wait()

    w.submit(first_job)
    start.wait(5.0)
    assert w.submit(lambda: None) is True
    assert w.submit(lambda: None) is True
    # Queue is now full (2 pending, drainer held on first_job) -> drops.
    assert w.submit(lambda: None) is False
    blocker.set()
    w.flush()
    # The first (held) job plus the two that fit drained; the dropped one never runs.
    assert w.drained <= 3


def test_worker_survives_a_raising_job():
    w = BackgroundWorker()

    def boom():
        raise RuntimeError("job failed")

    w.submit(boom)
    w.submit(lambda: None)
    w.flush(timeout=5.0)
    assert w.drained == 2


def test_worker_returns_true_when_enqueued():
    w = BackgroundWorker()
    ok = w.submit(lambda: None)
    assert ok is True
    w.flush()
