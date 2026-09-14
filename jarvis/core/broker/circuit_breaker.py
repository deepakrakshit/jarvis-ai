"""JARVIS Centralized Circuit Breakers.

Protects external tools, APIs, and execution fabric against cascading failures,
retry storms, and persistent outages (ARCHITECTURE.md Layer 14 & Failure Modes #22, #23).
"""

from datetime import UTC, datetime, timedelta
from threading import Lock

from pydantic import BaseModel, Field

from jarvis.core.broker.types import CircuitBreakerOpenError, CircuitBreakerState


class CircuitBreakerConfig(BaseModel):
    """Operational parameters for a circuit breaker."""

    failure_threshold: int = Field(default=5, ge=1)
    """Consecutive failures required to trip the breaker from CLOSED to OPEN."""

    recovery_timeout_seconds: float = Field(default=30.0, gt=0.0)
    """Duration to remain in OPEN before transitioning to HALF_OPEN probe."""

    half_open_max_probes: int = Field(default=1, ge=1)
    """Number of concurrent trial requests permitted in HALF_OPEN state."""

    success_threshold: int = Field(default=2, ge=1)
    """Consecutive successes in HALF_OPEN required to close the breaker."""


class CircuitBreaker:
    """Thread-safe circuit breaker implementation."""

    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state: CircuitBreakerState = CircuitBreakerState.CLOSED
        self._consecutive_failures: int = 0
        self._consecutive_successes: int = 0
        self._opened_at: datetime | None = None
        self._active_probes: int = 0
        self._lock = Lock()

    @property
    def state(self) -> CircuitBreakerState:
        with self._lock:
            self._check_recovery_transition(datetime.now(UTC))
            return self._state

    def _check_recovery_transition(self, now: datetime) -> None:
        """Internal helper: transition from OPEN to HALF_OPEN if timeout elapsed."""
        if self._state == CircuitBreakerState.OPEN and self._opened_at:
            elapsed = now - self._opened_at
            if elapsed >= timedelta(seconds=self.config.recovery_timeout_seconds):
                self._state = CircuitBreakerState.HALF_OPEN
                self._active_probes = 0
                self._consecutive_successes = 0

    def can_execute(self) -> bool:
        """Check whether an invocation is currently permitted."""
        with self._lock:
            now = datetime.now(UTC)
            self._check_recovery_transition(now)

            if self._state == CircuitBreakerState.CLOSED:
                return True

            if self._state == CircuitBreakerState.OPEN:
                return False

            # HALF_OPEN state: allow bounded trial probes
            if self._active_probes < self.config.half_open_max_probes:
                self._active_probes += 1
                return True
            return False

    def check_and_raise(self) -> None:
        """Fast-fail with CircuitBreakerOpenError if invocation is blocked."""
        if not self.can_execute():
            raise CircuitBreakerOpenError(
                tool_id=self.name,
                message=f"Circuit breaker for '{self.name}' is OPEN/BUSY. Fast-failing invocation.",
            )

    def record_success(self) -> None:
        """Record a successful execution, updating recovery counters."""
        with self._lock:
            now = datetime.now(UTC)
            self._check_recovery_transition(now)

            if self._state == CircuitBreakerState.HALF_OPEN:
                self._consecutive_successes += 1
                self._active_probes = max(0, self._active_probes - 1)
                if self._consecutive_successes >= self.config.success_threshold:
                    # Healthy again; close breaker
                    self._state = CircuitBreakerState.CLOSED
                    self._consecutive_failures = 0
                    self._consecutive_successes = 0
                    self._opened_at = None
            elif self._state == CircuitBreakerState.CLOSED:
                self._consecutive_failures = 0

    def record_failure(self, error: Exception | None = None) -> None:
        """Record an execution failure, potentially tripping the breaker."""
        with self._lock:
            now = datetime.now(UTC)
            if self._state == CircuitBreakerState.HALF_OPEN:
                # Probe failed: immediate trip back to OPEN
                self._state = CircuitBreakerState.OPEN
                self._opened_at = now
                self._active_probes = 0
                self._consecutive_successes = 0
            elif self._state == CircuitBreakerState.CLOSED:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.config.failure_threshold:
                    self._state = CircuitBreakerState.OPEN
                    self._opened_at = now

    def reset(self) -> None:
        """Administratively reset circuit breaker to pristine CLOSED state."""
        with self._lock:
            self._state = CircuitBreakerState.CLOSED
            self._consecutive_failures = 0
            self._consecutive_successes = 0
            self._opened_at = None
            self._active_probes = 0


class CircuitBreakerRegistry:
    """Central registry of circuit breakers keyed by tool ID or provider."""

    def __init__(self, default_config: CircuitBreakerConfig | None = None) -> None:
        self._default_config = default_config or CircuitBreakerConfig()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = Lock()

    def get_breaker(self, key: str, config: CircuitBreakerConfig | None = None) -> CircuitBreaker:
        """Get or instantiate a circuit breaker for the specified key."""
        with self._lock:
            if key not in self._breakers:
                self._breakers[key] = CircuitBreaker(
                    name=key,
                    config=config or self._default_config,
                )
            return self._breakers[key]

    def reset_all(self) -> None:
        """Reset all registered circuit breakers."""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()
