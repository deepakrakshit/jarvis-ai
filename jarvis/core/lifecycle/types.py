"""JARVIS Component Lifecycle States and State Machine Transitions.

Defines the formal lifecycle states and transition validators for all tools,
specialists, models, and skills (ARCHITECTURE.md Layer 11).
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class HealthProbeResult(BaseModel):
    """Result of a component health diagnostic probe (Contract 16)."""

    component_id: str
    healthy: bool
    latency_ms: float = 0.0
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ComponentLifecycleState(StrEnum):
    """Canonical component lifecycle state machine (ARCHITECTURE.md Layer 11).

    REGISTERED -> VALIDATED -> ENABLED -> RUNNING
    RUNNING -> (degradation/anomaly) -> QUARANTINED
    QUARANTINED -> REPAIR | DISABLED | RETIRED
    REPAIR -> VALIDATED | ENABLED | QUARANTINED
    DISABLED -> ENABLED | RETIRED
    """

    REGISTERED = "REGISTERED"
    """Component manifest discovered and loaded into memory."""

    VALIDATED = "VALIDATED"
    """Schema, digests, and contract tests verified."""

    ENABLED = "ENABLED"
    """Approved for runtime discovery and capability firewall matching."""

    RUNNING = "RUNNING"
    """Actively processing invocations."""

    QUARANTINED = "QUARANTINED"
    """Isolated due to consecutive errors, schema drift, or anomaly detection."""

    REPAIR = "REPAIR"
    """Undergoing automated self-healing, schema revalidation, or adapter restart."""

    DISABLED = "DISABLED"
    """Administratively disabled; excluded from router projection."""

    RETIRED = "RETIRED"
    """Permanently decommissioned."""


class ComponentType(StrEnum):
    """Categories of components managed under the lifecycle system."""

    TOOL = "TOOL"
    SPECIALIST = "SPECIALIST"
    MODEL = "MODEL"
    SKILL = "SKILL"


# Allowed Lifecycle State Transitions
LIFECYCLE_TRANSITIONS: dict[ComponentLifecycleState, set[ComponentLifecycleState]] = {
    ComponentLifecycleState.REGISTERED: {
        ComponentLifecycleState.VALIDATED,
        ComponentLifecycleState.DISABLED,
        ComponentLifecycleState.RETIRED,
    },
    ComponentLifecycleState.VALIDATED: {
        ComponentLifecycleState.ENABLED,
        ComponentLifecycleState.DISABLED,
        ComponentLifecycleState.RETIRED,
        ComponentLifecycleState.QUARANTINED,
    },
    ComponentLifecycleState.ENABLED: {
        ComponentLifecycleState.RUNNING,
        ComponentLifecycleState.DISABLED,
        ComponentLifecycleState.QUARANTINED,
        ComponentLifecycleState.RETIRED,
    },
    ComponentLifecycleState.RUNNING: {
        ComponentLifecycleState.ENABLED,
        ComponentLifecycleState.QUARANTINED,
        ComponentLifecycleState.DISABLED,
    },
    ComponentLifecycleState.QUARANTINED: {
        ComponentLifecycleState.REPAIR,
        ComponentLifecycleState.DISABLED,
        ComponentLifecycleState.RETIRED,
    },
    ComponentLifecycleState.REPAIR: {
        ComponentLifecycleState.VALIDATED,
        ComponentLifecycleState.ENABLED,
        ComponentLifecycleState.QUARANTINED,
        ComponentLifecycleState.DISABLED,
    },
    ComponentLifecycleState.DISABLED: {
        ComponentLifecycleState.ENABLED,
        ComponentLifecycleState.VALIDATED,
        ComponentLifecycleState.RETIRED,
    },
    ComponentLifecycleState.RETIRED: set(),
}


class InvalidLifecycleTransitionError(Exception):
    """Raised when an illegal component lifecycle state transition is attempted."""

    def __init__(
        self,
        component_id: str,
        current_state: ComponentLifecycleState,
        target_state: ComponentLifecycleState,
    ) -> None:
        self.component_id = component_id
        self.current_state = current_state
        self.target_state = target_state
        super().__init__(
            f"Component '{component_id}' cannot transition from "
            f"{current_state.value} to {target_state.value}."
        )


class ComponentRecord(BaseModel):
    """Operational lifecycle record for an individual component."""

    record_id: UUID = Field(default_factory=uuid4)
    component_id: str
    component_type: ComponentType
    state: ComponentLifecycleState = ComponentLifecycleState.REGISTERED
    version: str = "1.0.0"
    total_invocations: int = 0
    total_errors: int = 0
    consecutive_errors: int = 0
    quarantine_reason: str | None = None
    quarantine_count: int = 0
    last_health_probe: HealthProbeResult | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def transition_to(
        self,
        new_state: ComponentLifecycleState,
        reason: str | None = None,
    ) -> None:
        """Validate and transition component state."""
        if new_state == self.state:
            return

        allowed = LIFECYCLE_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise InvalidLifecycleTransitionError(
                component_id=self.component_id,
                current_state=self.state,
                target_state=new_state,
            )

        if new_state == ComponentLifecycleState.QUARANTINED:
            self.quarantine_reason = reason or "Consecutive error threshold exceeded"
            self.quarantine_count += 1
        elif self.state in (
            ComponentLifecycleState.QUARANTINED,
            ComponentLifecycleState.REPAIR,
        ) and new_state in (
            ComponentLifecycleState.ENABLED,
            ComponentLifecycleState.VALIDATED,
        ):
            self.quarantine_reason = None
            self.consecutive_errors = 0

        self.state = new_state
