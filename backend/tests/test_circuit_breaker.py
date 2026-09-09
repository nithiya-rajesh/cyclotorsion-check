"""Tests for the circuit breaker (TDD Section 4.4)."""

import pytest

from cyclotorsion.circuit_breaker import (
    FALLBACK_MESSAGE,
    STATE_CLOSED,
    STATE_OPEN,
    BreakerConfig,
    CircuitBreaker,
)
from cyclotorsion.detector import ProviderError


class _FakeClock:
    def __init__(self, start=0.0):
        self._now = start

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now += seconds


def test_breaker_opens_after_threshold_consecutive_failures():
    cb = CircuitBreaker(BreakerConfig(failure_threshold=3, recovery_timeout_seconds=60))
    cb.before_call()  # closed, allow
    cb.on_failure()
    cb.on_failure()
    assert cb.state == STATE_CLOSED
    cb.on_failure()  # 3rd consecutive -> open
    assert cb.state == STATE_OPEN
    with pytest.raises(ProviderError) as e:
        cb.before_call()
    assert FALLBACK_MESSAGE in str(e.value)


def test_breaker_fails_fast_while_open():
    cb = CircuitBreaker(BreakerConfig(failure_threshold=1, recovery_timeout_seconds=60))
    cb.on_failure()  # opens immediately
    for _ in range(5):
        with pytest.raises(ProviderError):
            cb.before_call()  # never touches upstream again


def test_breaker_recovers_to_half_open_after_timeout_then_closed_on_success():
    clock = _FakeClock()
    cb = CircuitBreaker(
        BreakerConfig(failure_threshold=2, recovery_timeout_seconds=10),
        clock=clock,
    )
    cb.on_failure()
    cb.on_failure()  # open
    assert cb.state == STATE_OPEN
    with pytest.raises(ProviderError):
        cb.before_call()
    clock.advance(11)  # past recovery timeout
    # Half-open probe admitted:
    cb.before_call()
    assert cb.state != STATE_OPEN
    cb.on_success()  # probe succeeds -> closed
    assert cb.state == STATE_CLOSED
    cb.before_call()  # normal traffic resumes


def test_breaker_probe_failure_reopens():
    clock = _FakeClock()
    cb = CircuitBreaker(
        BreakerConfig(failure_threshold=2, recovery_timeout_seconds=10),
        clock=clock,
    )
    cb.on_failure()
    cb.on_failure()  # open
    clock.advance(11)
    cb.before_call()  # half-open probe admitted
    cb.on_failure()  # probe fails -> reopen
    assert cb.state == STATE_OPEN
    with pytest.raises(ProviderError):
        cb.before_call()


def test_breaker_success_resets_failure_count_within_closed():
    cb = CircuitBreaker(BreakerConfig(failure_threshold=3, recovery_timeout_seconds=60))
    cb.on_failure()
    cb.on_failure()
    cb.on_success()  # resets
    cb.on_failure()
    cb.on_failure()
    assert cb.state == STATE_CLOSED  # still closed (only 2 since reset)


def test_breaker_only_one_probe_admitted_in_half_open():
    clock = _FakeClock()
    cb = CircuitBreaker(
        BreakerConfig(failure_threshold=1, recovery_timeout_seconds=5),
        clock=clock,
    )
    cb.on_failure()  # open
    clock.advance(6)
    cb.before_call()  # probe #1 admitted
    with pytest.raises(ProviderError):
        cb.before_call()  # probe already in-flight -> rejected


def test_breaker_reset_restores_closed():
    cb = CircuitBreaker(BreakerConfig(failure_threshold=2, recovery_timeout_seconds=10))
    cb.on_failure()
    cb.on_failure()  # open
    assert cb.state == STATE_OPEN
    cb.reset()
    assert cb.state == STATE_CLOSED
    # normal traffic flows again
    cb.before_call()
    assert cb.state == STATE_CLOSED
