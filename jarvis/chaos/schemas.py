"""JARVIS Chaos Engineering Schemas and Fault Contracts.

Implements ARCHITECTURE.md and IMPLEMENTATION_ROADMAP.md: Fault injection models,
resiliency contracts, and fail-closed invariants under synthetic adversity.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ChaosFaultType(StrEnum):
    """Types of synthetic faults injected to test system resiliency."""

    MODEL_RATE_LIMIT = "model_rate_limit"  # Injects 429 to trigger quota failover ladder
    MODEL_TIMEOUT = "model_timeout"  # Injects timeout to trigger fallback
    MODEL_OUTAGE = "model_outage"  # Injects 503 outage to trigger provider switch
    BROKER_AMBIGUOUS_DROP = "broker_ambiguous_drop"  # Mid-flight drop to verify OUTCOME_UNKNOWN
    LEASE_STALE_FENCING = "lease_stale_fencing"  # Stale token to verify orphan rejection
    RESOURCE_HASH_MISMATCH = "resource_hash_mismatch"  # Verification refutation check


class ChaosFault(BaseModel):
    """Definition of an active chaos fault."""

    fault_id: UUID = Field(default_factory=uuid4)
    fault_type: ChaosFaultType
    target_component: str  # e.g., "model_gateway", "action_broker", "lease_manager"
    trigger_limit: int = 1  # Number of times this fault should trigger
    trigger_count: int = 0
    active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    def should_trigger(self, component: str) -> bool:
        """Check if fault matches target component and trigger budget remains."""
        return (
            self.active
            and self.target_component == component
            and self.trigger_count < self.trigger_limit
        )

    def record_triggered(self) -> None:
        """Record that fault was applied once."""
        self.trigger_count += 1
        if self.trigger_count >= self.trigger_limit:
            self.active = False


class ChaosExperimentResult(BaseModel):
    """Outcome of a chaos resiliency experiment."""

    experiment_name: str
    fault_type: ChaosFaultType
    resilient_recovery_observed: bool
    fail_closed_preserved: bool
    expected_error_or_fallback_occurred: bool
    details: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def passed(self) -> bool:
        """Experiment passes only if fail-closed guarantees held and recovery succeeded."""
        return (
            self.fail_closed_preserved
            and self.resilient_recovery_observed
            and self.expected_error_or_fallback_occurred
        )
