"""Tests for the token-bucket rate limiter (TDD Section 4.3)."""

from fastapi.testclient import TestClient

from cyclotorsion.app import app
from cyclotorsion.rate_limit import MemRateLimiter, TokenBucket, reset


class _FakeClock:
    """Deterministic monotonic clock for bucket tests."""

    def __init__(self, start=0.0):
        self._now = start

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now += seconds


def test_token_bucket_starts_full_and_consumes():
    clock = _FakeClock()
    b = TokenBucket(rate=1.0, burst=5.0, now=clock())
    for _ in range(5):
        assert b.try_consume(clock()) is True
    assert b.try_consume(clock()) is False  # exhausted


def test_token_bucket_refills_at_rate():
    clock = _FakeClock()
    b = TokenBucket(rate=1.0, burst=2.0, now=clock())
    assert b.try_consume(clock()) is True
    assert b.try_consume(clock()) is True
    assert b.try_consume(clock()) is False
    clock.advance(1.0)
    assert b.try_consume(clock()) is True  # one token refilled


def test_token_bucket_caps_at_burst():
    clock = _FakeClock()
    b = TokenBucket(rate=10.0, burst=3.0, now=clock())
    clock.advance(100.0)  # long idle must NOT accumulate beyond burst
    for _ in range(3):
        assert b.try_consume(clock()) is True  # exactly the burst worth
    assert b.try_consume(clock()) is False  # capped at 3, now exhausted


def test_limiter_keys_by_uid():
    from cyclotorsion.rate_limit import MemRateLimiter

    class _Client:
        host = "1.2.3.4"

    req = type("R", (), {"client": _Client()})()
    limiter = MemRateLimiter()
    assert limiter.key_from_request(req, uid="user-1") == "uid:user-1"
    assert limiter.key_from_request(req, uid=None) == "ip:1.2.3.4"


def test_limiter_fixed_per_minute(tmp_store=""):
    # 20/min = 1 token every 3 seconds, burst 20 -> 20 immediately allowed.
    clock = _FakeClock()
    store: dict = {}
    limiter = MemRateLimiter(per_minute=20.0, burst=1.0, store=store, clock=clock)
    assert limiter.allow("uid:x") is True
    assert limiter.allow("uid:x") is False  # burst of 1 exhausted
    clock.advance(3.0)
    assert limiter.allow("uid:x") is True  # refilled one (20/60 * 3 = 1)


def test_rate_limit_dependency_returns_429_when_exhausted(monkeypatch):
    # Enable rate limiting with a burst of 1 so the 2nd request is rejected.
    import cyclotorsion.rate_limit as rl
    from cyclotorsion import config as cfg_mod

    class _C:
        rate_limit_enabled = True
        rate_limit_per_minute = 20.0
        rate_limit_burst = 1.0

    monkeypatch.setattr(rl, "cfg", _C())
    # Force a fresh limiter with burst 1.
    reset()
    monkeypatch.setattr(cfg_mod, "cfg", _C())

    data = _png_bytes()
    client = TestClient(app)
    r1 = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/detect",
        files=[
            ("upright", ("u.png", data, "image/png")),
            ("rotated", ("r.png", data, "image/png")),
        ],
    )
    assert r2.status_code == 429
    reset()


def test_detect_rate_429_does_not_leak_concurrency_slot(monkeypatch):
    """Regression (architecture review Critical): a rate-limit 429 raised after
    the concurrency guard would have acquired a slot must NOT permanently
    consume that slot.

    Before the fix, acquisition was a FastAPI dependency; a later 429 from
    ``rate_limit`` aborted the request before the endpoint body's ``finally``
    ran, leaking the slot permanently. After the fix, acquisition happens inside
    the handler body so a 429 skips acquisition entirely and the slot stays free.

    Setup: enable rate limiting (burst 1) AND concurrency limiting (1 slot / run).
    """
    import cyclotorsion.concurrency as cc
    import cyclotorsion.rate_limit as rl
    from cyclotorsion import config as cfg_mod

    class _C:
        rate_limit_enabled = True
        rate_limit_per_minute = 20.0
        rate_limit_burst = 1.0
        concurrency_limit_enabled = True
        max_concurrent_per_user = 1

    monkeypatch.setattr(rl, "cfg", _C())
    monkeypatch.setattr(cc, "cfg", _C())
    monkeypatch.setattr(cfg_mod, "cfg", _C())
    reset()
    cc.reset()

    data = _png_bytes()
    client = TestClient(app)

    def post():
        return client.post(
            "/detect",
            files=[
                ("upright", ("u.png", data, "image/png")),
                ("rotated", ("r.png", data, "image/png")),
            ],
        )

    # First request: succeeds, and the concurrency slot is released in `finally`.
    assert post().status_code == 200
    # Second request: rate limiter is exhausted (burst 1) -> 429.
    assert post().status_code == 429
    # The 429 must NOT have leaked a concurrency slot: a full run should still be
    # able to acquire the single slot and complete. We reset the rate limiter so
    # a follow-up request can pass it and complete — if the slot were leaked the
    # request would be rejected by the concurrency guard.
    rl.reset()
    assert post().status_code == 200
    cc.reset()
    rl.reset()


def _png_bytes() -> bytes:
    from generate_synthetic_data import generate_pair

    upright, _, _ = generate_pair(0.0, 128)
    return upright
