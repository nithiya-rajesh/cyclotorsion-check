"""Tests for the per-user concurrency guard (P3 hardening)."""

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from cyclotorsion import app as app_mod
from cyclotorsion import concurrency
from cyclotorsion.app import app
from cyclotorsion.auth import UserContext
from cyclotorsion.concurrency import ConcurrencyGuard

client = TestClient(app)


class _FakeRequest:
    def __init__(self, client_host="1.2.3.4"):
        self.client = type("C", (), {"host": client_host})()
        self.headers = {}


def test_guard_limits_inflight_and_releases():
    g = ConcurrencyGuard(max_concurrent=1)
    r1 = _FakeRequest()
    assert g.acquire(g.key_from_request(r1, None)) is True
    # Second concurrent slot for the same key is denied (only 1 allowed).
    assert g.acquire(g.key_from_request(r1, None)) is False
    g.release(g.key_from_request(r1, None))
    # After release a newer request acquires again.
    assert g.acquire(g.key_from_request(r1, None)) is True


def test_guard_distinct_keys_are_independent():
    g = ConcurrencyGuard(max_concurrent=1)
    assert g.acquire("uid:a") is True
    assert g.acquire("uid:b") is True  # different key not limited


def test_guard_uid_preferred_over_ip():
    g = ConcurrencyGuard(max_concurrent=1)
    req = _FakeRequest()
    assert g.key_from_request(req, "u-1") == "uid:u-1"


def test_acquire_concurrency_disabled_noop(monkeypatch):
    monkeypatch.setattr(
        app_mod, "cfg", type("C", (), {"concurrency_limit_enabled": False})()
    )
    req = _FakeRequest()
    req.state = type("S", (), {})()
    assert concurrency.acquire_concurrency(req, UserContext(uid=None)) is None


def test_acquire_concurrency_rejects_when_saturated(monkeypatch):
    monkeypatch.setattr(
        concurrency, "cfg", type("C", (), {"concurrency_limit_enabled": True})()
    )
    from cyclotorsion.auth import UserContext

    req = _FakeRequest()
    guard = ConcurrencyGuard(max_concurrent=1)
    monkeypatch.setattr(concurrency, "get_guard", lambda: guard)
    # Saturate the slot for req's key.
    assert guard.acquire(guard.key_from_request(req, None)) is True
    with pytest.raises(HTTPException) as e:
        concurrency.acquire_concurrency(req, UserContext(uid=None))
    assert e.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_acquire_release_pairing_recovers_full_capacity():
    """The guard primitive itself must never leak slots when acquire/release
    are correctly paired (which the handler's try/finally guarantees).
    """
    guard = ConcurrencyGuard(max_concurrent=2)
    key = "uid:u1"
    assert guard.acquire(key) is True
    assert guard.acquire(key) is True
    guard.release(key)
    guard.release(key)
    assert guard.acquire(key) is True  # fully recovered after a complete request


def test_release_concurrency_frees_the_acquired_slot(monkeypatch):
    g = ConcurrencyGuard(max_concurrent=1)
    monkeypatch.setattr(concurrency, "get_guard", lambda: g)
    req = _FakeRequest()
    key = g.key_from_request(req, None)
    assert g.acquire(key) is True
    req.state = type("S", (), {"concurrency_key": key})()
    concurrency.release_concurrency(req)
    # After releasing the acquired slot a new request can acquire it.
    assert g.acquire(key) is True


def test_release_concurrency_is_idempotent(monkeypatch):
    g = ConcurrencyGuard(max_concurrent=1)
    monkeypatch.setattr(concurrency, "get_guard", lambda: g)
    req = _FakeRequest()
    key = g.key_from_request(req, None)
    assert g.acquire(key) is True
    req.state = type("S", (), {"concurrency_key": key})()
    concurrency.release_concurrency(req)
    concurrency.release_concurrency(req)  # double-call must be a no-op
    # Only one slot was released, so a single acquire still succeeds but a
    # second concurrent one is denied (no over-release).
    assert g.acquire(key) is True


def test_release_concurrency_without_key_is_noop():
    req = _FakeRequest()
    req.state = type("S", (), {})()
    concurrency.release_concurrency(req)  # should not raise
