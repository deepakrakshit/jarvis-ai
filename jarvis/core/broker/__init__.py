"""JARVIS Action Broker, Idempotency, Leases, and Saga Compensation Subsystem.

Governs the write/effect path to ensure replay-safety, distributed concurrency control,
circuit-breaking, and ambiguous-outcome containment (ARCHITECTURE.md Layer 14).
"""

from jarvis.core.broker.broker import ActionBroker
from jarvis.core.broker.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
)
from jarvis.core.broker.lease import Lease, LeaseManager
from jarvis.core.broker.ledger import (
    VALID_TRANSITIONS,
    EffectAttempt,
    EffectRecord,
    IdempotencyLedger,
)
from jarvis.core.broker.retry import RetryClassifier
from jarvis.core.broker.saga import (
    CompensationRegistry,
    SagaCompensationPlan,
    SagaStep,
    SagaStepResult,
)
from jarvis.core.broker.types import (
    ActionBrokerError,
    AmbiguousOutcomeError,
    CircuitBreakerOpenError,
    CircuitBreakerState,
    CompensationFailedError,
    EffectAuthorizationRequiredError,
    EffectState,
    IdempotencyClass,
    IdempotencyConflictError,
    InvalidEffectStateTransitionError,
    LeaseAcquisitionError,
    RetryClassification,
)

__all__ = [
    "VALID_TRANSITIONS",
    "ActionBroker",
    "ActionBrokerError",
    "AmbiguousOutcomeError",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerOpenError",
    "CircuitBreakerRegistry",
    "CircuitBreakerState",
    "CompensationFailedError",
    "CompensationRegistry",
    "EffectAttempt",
    "EffectAuthorizationRequiredError",
    "EffectRecord",
    "EffectState",
    "IdempotencyClass",
    "IdempotencyConflictError",
    "IdempotencyLedger",
    "InvalidEffectStateTransitionError",
    "Lease",
    "LeaseAcquisitionError",
    "LeaseManager",
    "RetryClassification",
    "RetryClassifier",
    "SagaCompensationPlan",
    "SagaStep",
    "SagaStepResult",
]
