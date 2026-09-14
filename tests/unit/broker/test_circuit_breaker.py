"""Unit tests for Centralized Circuit Breakers and Registry."""

from datetime import UTC, datetime, timedelta

import pytest

from jarvis.core.broker.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
)
from jarvis.core.broker.types import CircuitBreakerOpenError, CircuitBreakerState


def _assert_breaker_state(cb: CircuitBreaker, expected: CircuitBreakerState) -> None:
    assert cb.state == expected


def test_circuit_breaker_initial_closed_state() -> None:
    """Verify initial state is CLOSED and can execute."""
    cb = CircuitBreaker("test_service")
    _assert_breaker_state(cb, CircuitBreakerState.CLOSED)
    assert cb.can_execute() is True


def test_circuit_breaker_trips_to_open() -> None:
    """Verify threshold failures trip the breaker to OPEN and fast-fail."""
    config = CircuitBreakerConfig(failure_threshold=3, recovery_timeout_seconds=10.0)
    cb = CircuitBreaker("flaky_api", config=config)

    cb.record_failure()
    cb.record_failure()
    _assert_breaker_state(cb, CircuitBreakerState.CLOSED)

    # Third failure trips the breaker
    cb.record_failure()
    _assert_breaker_state(cb, CircuitBreakerState.OPEN)
    assert cb.can_execute() is False

    with pytest.raises(CircuitBreakerOpenError):
        cb.check_and_raise()


def test_circuit_breaker_recovery_to_half_open_and_closed() -> None:
    """Verify OPEN transitions to HALF_OPEN after timeout and closes on successes."""
    config = CircuitBreakerConfig(
        failure_threshold=2,
        recovery_timeout_seconds=5.0,
        half_open_max_probes=1,
        success_threshold=2,
    )
    cb = CircuitBreaker("recoverable_service", config=config)

    # Trip breaker
    cb.record_failure()
    cb.record_failure()
    _assert_breaker_state(cb, CircuitBreakerState.OPEN)

    # Simulate timeout elapsed
    past = datetime.now(UTC) - timedelta(seconds=6)
    cb._opened_at = past

    # Should transition to HALF_OPEN
    _assert_breaker_state(cb, CircuitBreakerState.HALF_OPEN)
    assert cb.can_execute() is True

    # In HALF_OPEN, max_probes=1 so second concurrent request is blocked
    assert cb.can_execute() is False

    # First success probe
    cb.record_success()
    _assert_breaker_state(cb, CircuitBreakerState.HALF_OPEN)

    # Second success probe reaches success_threshold=2 -> CLOSED
    assert cb.can_execute() is True
    cb.record_success()
    _assert_breaker_state(cb, CircuitBreakerState.CLOSED)
    assert cb.can_execute() is True


def test_circuit_breaker_half_open_failure_re_trips() -> None:
    """Verify any failure in HALF_OPEN trips immediately back to OPEN."""
    config = CircuitBreakerConfig(
        failure_threshold=2,
        recovery_timeout_seconds=5.0,
        half_open_max_probes=1,
        success_threshold=2,
    )
    cb = CircuitBreaker("sensitive_service", config=config)

    # Trip breaker
    cb.record_failure()
    cb.record_failure()
    cb._opened_at = datetime.now(UTC) - timedelta(seconds=6)
    _assert_breaker_state(cb, CircuitBreakerState.HALF_OPEN)

    # Probe fails
    cb.record_failure()
    _assert_breaker_state(cb, CircuitBreakerState.OPEN)


def test_circuit_breaker_registry() -> None:
    """Verify registry caches and separates breakers by key."""
    registry = CircuitBreakerRegistry()
    b1 = registry.get_breaker("tool_alpha")
    b2 = registry.get_breaker("tool_beta")
    assert b1.name == "tool_alpha"
    assert b2.name == "tool_beta"
    assert b1 is not b2

    b1.record_failure()
    assert b1._consecutive_failures == 1
    assert b2._consecutive_failures == 0

    registry.reset_all()
    assert b1._consecutive_failures == 0
