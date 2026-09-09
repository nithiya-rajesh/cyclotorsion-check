"""Circuit breaker for the Gemini provider (TDD Section 4.4).

Motivation: on a *sustained* Gemini outage, the existing retry loop (max 3
attempts, 3s backoff) would keep hammering a service known to be down — slow,
clear failure for the user and wasted spend/hits against a degraded upstream.
The breaker fail-fasts instead.

Behavior:
  - **CLOSED** (normal): every call flows through. Each failure increments a
    consecutive-failure counter; success resets it.
  - **OPEN**: after ``failure_threshold`` consecutive failures, the breaker
    opens. Calls fail immediately with a ``ProviderError`` ("AI provider
    temporarily unavailable") and never touch the network. After
    ``recovery_timeout`` seconds it transitions to HALF_OPEN.
  - **HALF_OPEN**: a single probe call is admitted. Success -> CLOSED (reset);
    failure -> OPEN again (restart cooldown).

Thread-safe: the breaker is shared by concurrent requests on a Cloud Run
instance and reached from multiple threads.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"

FALLBACK_MESSAGE = "AI provider temporarily unavailable. Please try again shortly."


def _provider_error(message: str) -> Exception:
    """Lazily import and construct the detector's ProviderError.

    Imported lazily to avoid a circular import (detector.py imports this module
    at module load; this module must not import detector at module load).
    """
    from cyclotorsion.detector import ProviderError

    return ProviderError(message)


@dataclass(frozen=True)
class BreakerConfig:
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 60.0


class CircuitBreaker:
    """A thread-safe circuit breaker guarding the upstream AI provider."""

    def __init__(
        self,
        config: BreakerConfig | None = None,
        clock=time.monotonic,
    ):
        self._conf = config or BreakerConfig()
        self._clock = clock
        self._state = STATE_CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._in_probe = False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def _current_state(self, now: float) -> str:
        """Return the logical state, applying the OPEN->HALF_OPEN transition."""
        if (
            self._state == STATE_OPEN
            and self._opened_at is not None
            and now - self._opened_at >= self._conf.recovery_timeout_seconds
        ):
            self._state = STATE_HALF_OPEN
        return self._state

    def before_call(self) -> None:
        """Raise ProviderError if the breaker is open; otherwise allow the call.

        A single probe is admitted while in HALF_OPEN.
        """
        with self._lock:
            now = self._clock()
            state = self._current_state(now)
            if state == STATE_CLOSED:
                return
            if state == STATE_OPEN:
                raise _provider_error(FALLBACK_MESSAGE)
            # HALF_OPEN: admit exactly one probe at a time.
            if self._in_probe:
                raise _provider_error(FALLBACK_MESSAGE)
            self._in_probe = True

    def on_success(self) -> None:
        """Record a successful call. Resets the breaker to CLOSED."""
        with self._lock:
            self._state = STATE_CLOSED
            self._failures = 0
            self._opened_at = None
            self._in_probe = False

    def on_failure(self) -> None:
        """Record a failed call. May transition CLOSED->OPEN."""
        with self._lock:
            self._failures += 1
            self._in_probe = False
            if self._failures >= self._conf.failure_threshold:
                self._state = STATE_OPEN
                self._opened_at = self._clock()

    def reset(self) -> None:
        with self._lock:
            self._state = STATE_CLOSED
            self._failures = 0
            self._opened_at = None
            self._in_probe = False
