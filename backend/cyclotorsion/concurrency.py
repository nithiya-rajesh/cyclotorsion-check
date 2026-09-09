"""Per-user concurrency guard (TDD Section 4.1 / PRD US-2.3).

Prevents a single client from stacking an unbounded number of simultaneous
``/detect`` requests and tying up all Gemini quota / Cloud Run capacity (the
same exhaustion risk that motivated the rate limiter). The rate limiter caps
*rate* (requests/minute); this caps *inflight concurrency* (how many are being
processed at the same instant).

Design (dependency-free, mirrors ``rate_limit``):

  - A semaphore per key (Firebase UID, or client IP in open mode). Each key may
    run at most ``max_concurrent`` requests at once; beyond that a request is
    rejected with HTTP 429 rather than queued, so latency stays bounded and the
    clinical path is never starved.
  - In-memory state is sufficient while Cloud Run runs a single instance (V1),
    matching the rate limiter's documented constraint.
"""

from __future__ import annotations

import threading

from fastapi import HTTPException, Request, status

from cyclotorsion.auth import UserContext
from cyclotorsion.config import cfg


class ConcurrencyGuard:
    """Thread-safe per-key semaphore limiting in-flight requests."""

    def __init__(self, max_concurrent: int, store: dict | None = None):
        self._max = max(1, int(max_concurrent))
        self._sems: dict[str, threading.Semaphore] = store if store is not None else {}
        self._lock = threading.Lock()

    def key_from_request(self, request: Request, uid: str | None) -> str:
        if uid:
            return f"uid:{uid}"
        host = request.client.host if request.client else "unknown"
        return f"ip:{host}"

    def _semaphore(self, key: str) -> threading.Semaphore:
        with self._lock:
            sem = self._sems.get(key)
            if sem is None:
                sem = threading.Semaphore(self._max)
                self._sems[key] = sem
            return sem

    def acquire(self, key: str) -> bool:
        """Try to acquire one slot; returns True if acquired."""
        return self._semaphore(key).acquire(blocking=False)

    def release(self, key: str) -> None:
        self._semaphore(key).release()


# Process-wide guard built from config.
_guard: ConcurrencyGuard | None = None
_guard_lock = threading.Lock()


def get_guard() -> ConcurrencyGuard:
    global _guard
    with _guard_lock:
        if _guard is None:
            _guard = ConcurrencyGuard(max_concurrent=cfg.max_concurrent_per_user)
        return _guard


def acquire_concurrency(request: Request, user: UserContext) -> None:
    """Acquire a per-user concurrency slot, raising 429 if the cap is hit.

    MUST be called inside the handler's ``try`` block (NOT as a FastAPI
    dependency) so that a later dependency raising (rate limit, auth) cannot
    leave an acquired slot unreleased.  The matching
    ``release_concurrency(request)`` must be called in the handler's
    ``finally``.
    """
    if not cfg.concurrency_limit_enabled:
        return None
    guard = get_guard()
    key = guard.key_from_request(request, user.uid)
    if not guard.acquire(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many concurrent requests. Please wait for the current one to finish.",
            headers={"Retry-After": "30"},
        )
    # Attach the acquired key so release_concurrency() knows what to release.
    request.state.concurrency_key = key


def release_concurrency(request: Request) -> None:
    """Release the slot acquired by ``acquire_concurrency``.

    Must be called exactly once per acquired request (the ``/detect`` handler
    does this in its ``finally``). Safe to call when no slot was acquired
    (no-op), so a request that was rejected before the guard runs won't leak.
    """
    key = getattr(request.state, "concurrency_key", None)
    if key is not None:
        get_guard().release(key)
        # Prevent double-release if the handler somehow runs this twice.
        request.state.concurrency_key = None


def reset() -> None:
    """Reset the process-wide guard (used by tests)."""
    global _guard
    with _guard_lock:
        _guard = None
