"""JARVIS Action Broker Taxonomy, State Machine, and Exceptions.

Defines the 10-state effect lifecycle, idempotency taxonomy, retry classifications,
circuit breaker states, and domain exceptions governing the write/effect execution path
(ARCHITECTURE.md Layer 14).
"""

from enum import StrEnum
from uuid import UUID


class IdempotencyClass(StrEnum):
    """Side-effect and idempotency taxonomy for all tools and capabilities."""

    IDEMPOTENT = "IDEMPOTENT"
    """Safe to retry unconditionally with identical inputs (e.g., GET, file read)."""

    IDEMPOTENT_WITH_KEY = "IDEMPOTENT_WITH_KEY"
    """Safe to retry when passing identical logical_effect_id / idempotency key."""

    NON_IDEMPOTENT = "NON_IDEMPOTENT"
    """Mutating operation with ambiguous outcome on failure; blind retries strictly forbidden."""

    TRANSACTIONAL = "TRANSACTIONAL"
    """Multi-step operation requiring distributed rollback or Saga compensation logic."""

    UNKNOWN = "UNKNOWN"
    """Default fail-safe taxonomy; requires explicit external state verification before retry."""


class EffectState(StrEnum):
    """Comprehensive 10-state lifecycle machine for write/effect operations.

    Transitions:
    PROPOSED -> AUTHORIZED | FAILED
    AUTHORIZED -> DISPATCHING | FAILED
    DISPATCHING -> EXECUTING | FAILED | OUTCOME_UNKNOWN
    EXECUTING -> VERIFIED | FAILED | OUTCOME_UNKNOWN | COMPENSATION_PENDING
    OUTCOME_UNKNOWN -> VERIFIED | FAILED | COMPENSATION_PENDING | QUARANTINED
    COMPENSATION_PENDING -> COMPENSATED | QUARANTINED | FAILED
    Terminal/Stable states: VERIFIED, COMPENSATED, QUARANTINED, FAILED
    """

    PROPOSED = "PROPOSED"
    """Intent synthesized by model and proposed for evaluation."""

    AUTHORIZED = "AUTHORIZED"
    """Pre-approval guard and commit-time authorization verified and bound."""

    DISPATCHING = "DISPATCHING"
    """Acquired execution lease; preparing network or sandbox dispatch."""

    EXECUTING = "EXECUTING"
    """Payload delivered to downstream provider/tool; execution in flight."""

    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    """Network partition or timeout after provider acceptance; outcome ambiguous."""

    VERIFIED = "VERIFIED"
    """Execution completed and side effect confirmed; cached result sealed."""

    FAILED = "FAILED"
    """Execution failed deterministically before mutation or with known clean failure."""

    COMPENSATION_PENDING = "COMPENSATION_PENDING"
    """Partial failure in multi-step operation; Saga compensation running."""

    COMPENSATED = "COMPENSATED"
    """All forward actions successfully rolled back via compensating transactions."""

    QUARANTINED = "QUARANTINED"
    """Compensation failed or anomalous loop detected; held in DLQ for operator inspection."""


class RetryClassification(StrEnum):
    """Evaluation result for failure handling and retry decisions."""

    RETRYABLE_SAFE = "RETRYABLE_SAFE"
    """Safe to retry immediately or with backoff (idempotent tool or failure prior to dispatch)."""

    NON_RETRYABLE_FATAL = "NON_RETRYABLE_FATAL"
    """Deterministic failure (e.g., 400 Bad Request, auth failure); retry forbidden."""

    AMBIGUOUS_OUTCOME = "AMBIGUOUS_OUTCOME"
    """Non-idempotent tool timed out after dispatch; blind retry strictly forbidden."""

    REQUIRES_VERIFICATION = "REQUIRES_VERIFICATION"
    """External state verifier must confirm whether mutation occurred before retrying."""


class CircuitBreakerState(StrEnum):
    """Centralized circuit breaker operational states."""

    CLOSED = "CLOSED"
    """Normal operations; all requests pass through."""

    OPEN = "OPEN"
    """Failure threshold breached; fast-fail without invoking downstream provider."""

    HALF_OPEN = "HALF_OPEN"
    """Recovery trial period; limited probe requests allowed to test service health."""


# Domain Exceptions


class ActionBrokerError(Exception):
    """Base exception for Action Broker operations."""


class IdempotencyConflictError(ActionBrokerError):
    """Raised when an identical logical_effect_id is currently executing under lease."""

    def __init__(self, logical_effect_id: str, message: str | None = None) -> None:
        self.logical_effect_id = logical_effect_id
        super().__init__(
            message or f"Concurrent execution conflict on logical_effect_id: {logical_effect_id}"
        )


class LeaseAcquisitionError(ActionBrokerError):
    """Raised when a distributed lease cannot be acquired for a resource or effect."""

    def __init__(self, resource_id: str, holder_id: str, message: str | None = None) -> None:
        self.resource_id = resource_id
        self.holder_id = holder_id
        super().__init__(
            message
            or f"Could not acquire lease for resource '{resource_id}' by holder '{holder_id}'."
        )


class AmbiguousOutcomeError(ActionBrokerError):
    """Raised when an operation on a NON_IDEMPOTENT tool times out after dispatch.

    Blind retries are strictly forbidden to prevent duplicate side effects (Failure Mode #3 & #46).
    """

    def __init__(
        self,
        logical_effect_id: str,
        tool_id: str,
        attempt_id: UUID | None = None,
        message: str | None = None,
    ) -> None:
        self.logical_effect_id = logical_effect_id
        self.tool_id = tool_id
        self.attempt_id = attempt_id
        super().__init__(
            message
            or f"Ambiguous outcome on non-idempotent tool '{tool_id}' (effect: {logical_effect_id}). "
            "Blind retry forbidden; external verification required before continuing."
        )


class CircuitBreakerOpenError(ActionBrokerError):
    """Raised when an action is fast-failed because the tool's circuit breaker is OPEN."""

    def __init__(self, tool_id: str, message: str | None = None) -> None:
        self.tool_id = tool_id
        super().__init__(
            message or f"Circuit breaker for tool '{tool_id}' is OPEN. Fast-failing invocation."
        )


class CompensationFailedError(ActionBrokerError):
    """Raised when a Saga compensating step fails, forcing the effect into QUARANTINED."""

    def __init__(self, logical_effect_id: str, step_id: str, reason: str) -> None:
        self.logical_effect_id = logical_effect_id
        self.step_id = step_id
        self.reason = reason
        super().__init__(
            f"Saga compensation failed for effect '{logical_effect_id}' at step '{step_id}': {reason}. "
            "Effect moved to QUARANTINED."
        )


class EffectAuthorizationRequiredError(ActionBrokerError):
    """Raised when a state-mutating effect lacks a valid commit-time authorization."""

    def __init__(self, tool_id: str, message: str | None = None) -> None:
        self.tool_id = tool_id
        super().__init__(
            message
            or f"Tool '{tool_id}' produces mutations and requires a valid EffectAuthorization."
        )


class InvalidEffectStateTransitionError(ActionBrokerError):
    """Raised when an illegal transition is attempted in the EffectState machine."""

    def __init__(
        self, current_state: EffectState, target_state: EffectState, message: str | None = None
    ) -> None:
        self.current_state = current_state
        self.target_state = target_state
        super().__init__(
            message
            or f"Illegal effect state transition from {current_state.value} to {target_state.value}."
        )
