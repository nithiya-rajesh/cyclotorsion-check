"""Per-user token-bucket rate limiter (TDD Section 4.3).

Motivation (from the TDD, not theory): during development a single client's
repeated testing exhausted the shared Gemini free-tier daily quota multiple
times, degrading the service for everyone that day. At pilot scale, one user's
rapid retries — or a misbehaving client — could degrade the tool for an entire
hospital during live surgery.

Design (dependency-free, right-sized to the docs' philosophy):

  - **Token bucket** per key: each key refills at ``rate`` tokens/second up to
    a ``burst`` capacity. A request consumes one token; if none remain it is
    rejected with HTTP 429.
  - **Keyed by Firebase UID** when the caller is authenticated; in open/dev
    mode (no auth) it falls back to a per-IP key so the limiter still works
    before auth exists.
  - **In-memory state** is sufficient for V1 (single Cloud Run instance, per
    TDD Section 4.1/4.3). A distributed backing store is explicitly deferred
    until ``min-instances`` exceeds 1. The store is injected so tests can use
    a fresh, deterministic instance.
  - Thread-safe via a lock (a Cloud Run instance serves concurrent requests).
"""

from __future__ import annotations

import threading
import time

from fastapi import Depends, HTTPException, Request, status

from cyclotorsion.auth import UserContext, current_user
from cyclotorsion.config import cfg


class TokenBucket:
    """A single token bucket for one key."""

    __slots__ = ("tokens", "last_refill", "rate", "burst")

    def __init__(self, rate: float, burst: float, now: float | None = None):
        self.tokens = burst
        self.last_refill = now if now is not None else time.monotonic()
        self.rate = rate
        self.burst = burst

    def try_consume(self, now: float | None = None) -> bool:
        """Refill and attempt to consume one token. Returns True on success."""
        now = now if now is not None else time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class MemRateLimiter:
    """Thread-safe, in-memory token-bucket limiter keyed by string."""

    def __init__(
        self,
        per_minute: float = 20.0,
        burst: float | None = None,
        store: dict | None = None,
        clock=time.monotonic,
    ):
        # Rate is measured in tokens/second internally.
        self._rate = per_minute / 60.0
        self._burst = burst if burst is not None else per_minute
        self._buckets: dict[str, TokenBucket] = store if store is not None else {}
        self._lock = threading.Lock()
        self._clock = clock

    def allow(self, key: str) -> bool:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(self._rate, self._burst, self._clock())
                self._buckets[key] = bucket
            return bucket.try_consume(self._clock())

    def key_from_request(self, request: Request, uid: str | None) -> str:
        """Choose the rate-limit key: Firebase UID when available, else client IP."""
        if uid:
            return f"uid:{uid}"
        host = request.client.host if request.client else "unknown"
        return f"ip:{host}"


# Process-wide limiter built from config. Rate-limit state is intentionally NOT
# shared across Cloud Run instances (in-memory only) — consistent with the V1
# single-instance design (TDD Section 4.3).
_limiter: MemRateLimiter | None = None
_limiter_lock = threading.Lock()


def get_limiter() -> MemRateLimiter:
    """Return the process-wide limiter (creating it once from config)."""
    global _limiter
    with _limiter_lock:
        if _limiter is None:
            _limiter = MemRateLimiter(
                per_minute=cfg.rate_limit_per_minute,
                burst=cfg.rate_limit_burst,
            )
        return _limiter


def rate_limit(request: Request, user: UserContext = Depends(current_user)) -> None:
    """FastAPI dependency enforcing the per-user rate limit.
    Depends on ``current_user`` so Firebase verification happens exactly once
    per request and the limiter is keyed by the verified UID (auth failures
    surface as 401 before we reach the limiter — correct fail-closed order).
    In open/dev mode ``user.uid`` is None and we fall back to the client IP.
    No-ops when rate limiting is disabled (local-dev default). Raises HTTP 429
    when the caller's tokens are exhausted.
    """
    if not cfg.rate_limit_enabled:
        return None
    limiter = get_limiter()
    key = limiter.key_from_request(request, user.uid)
    if not limiter.allow(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait a moment and try again.",
            headers={"Retry-After": "60"},
        )


def reset() -> None:
    """Reset the process-wide limiter (used by tests)."""
    global _limiter
    with _limiter_lock:
        _limiter = None
