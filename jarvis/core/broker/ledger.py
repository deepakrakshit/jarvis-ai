"""JARVIS Idempotency Ledger and 10-State Effect Lifecycle Machine.

Maintains immutable records of intended, in-flight, and verified mutations,
enforcing deterministic state transitions and deduplication (ARCHITECTURE.md Layer 14).
"""

from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from jarvis.core.broker.types import (
    EffectState,
    IdempotencyClass,
    InvalidEffectStateTransitionError,
)

# Canonical Valid State Transition Graph
VALID_TRANSITIONS: dict[EffectState, set[EffectState]] = {
    EffectState.PROPOSED: {EffectState.AUTHORIZED, EffectState.FAILED},
    EffectState.AUTHORIZED: {EffectState.DISPATCHING, EffectState.FAILED},
    EffectState.DISPATCHING: {
        EffectState.EXECUTING,
        EffectState.FAILED,
        EffectState.OUTCOME_UNKNOWN,
    },
    EffectState.EXECUTING: {
        EffectState.VERIFIED,
        EffectState.FAILED,
        EffectState.OUTCOME_UNKNOWN,
        EffectState.COMPENSATION_PENDING,
    },
    EffectState.OUTCOME_UNKNOWN: {
        EffectState.VERIFIED,
        EffectState.FAILED,
        EffectState.COMPENSATION_PENDING,
        EffectState.QUARANTINED,
    },
    EffectState.COMPENSATION_PENDING: {
        EffectState.COMPENSATED,
        EffectState.QUARANTINED,
        EffectState.FAILED,
    },
    # Terminal / Stable states
    EffectState.VERIFIED: set(),
    EffectState.COMPENSATED: set(),
    EffectState.QUARANTINED: set(),
    EffectState.FAILED: {EffectState.AUTHORIZED, EffectState.DISPATCHING},
}


class EffectAttempt(BaseModel):
    """Execution attempt metadata for an effect."""

    attempt_id: UUID = Field(default_factory=uuid4)
    attempt_number: int = 1
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    status: EffectState = EffectState.DISPATCHING
    error_message: str | None = None
    raw_result: Any | None = None


class EffectRecord(BaseModel):
    """Durable record of an intended real-world mutation."""

    logical_effect_id: str
    """Deterministic hash identifying this exact logical mutation."""

    task_id: UUID
    tool_id: str
    idempotency_class: IdempotencyClass
    state: EffectState = EffectState.PROPOSED
    canonical_arguments_hash: str
    target_resource: str | None = None
    attempts: list[EffectAttempt] = Field(default_factory=list)
    cached_result: Any | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    compensation_plan: dict[str, Any] | None = None
    quarantine_reason: str | None = None

    def transition_to(self, new_state: EffectState, reason: str | None = None) -> None:
        """Validate and execute a deterministic state machine transition."""
        if new_state == self.state:
            return

        allowed = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise InvalidEffectStateTransitionError(
                current_state=self.state,
                target_state=new_state,
                message=(
                    f"Cannot transition effect '{self.logical_effect_id}' "
                    f"from {self.state.value} to {new_state.value}. Allowed: {[s.value for s in allowed]}"
                ),
            )

        self.state = new_state
        self.updated_at = datetime.now(UTC)
        if new_state == EffectState.QUARANTINED and reason:
            self.quarantine_reason = reason


class IdempotencyLedger:
    """Thread-safe idempotency ledger tracking all real-world effects."""

    def __init__(self) -> None:
        self._records: dict[str, EffectRecord] = {}
        self._lock = Lock()

    def record_proposal(
        self,
        logical_effect_id: str,
        task_id: UUID,
        tool_id: str,
        idempotency_class: IdempotencyClass,
        canonical_arguments_hash: str,
        target_resource: str | None = None,
    ) -> EffectRecord:
        """Record the initial PROPOSED intent for an action."""
        with self._lock:
            existing = self._records.get(logical_effect_id)
            if existing:
                return existing

            record = EffectRecord(
                logical_effect_id=logical_effect_id,
                task_id=task_id,
                tool_id=tool_id,
                idempotency_class=idempotency_class,
                state=EffectState.PROPOSED,
                canonical_arguments_hash=canonical_arguments_hash,
                target_resource=target_resource,
            )
            self._records[logical_effect_id] = record
            return record

    def get(self, logical_effect_id: str) -> EffectRecord | None:
        """Retrieve the effect record for logical_effect_id."""
        with self._lock:
            return self._records.get(logical_effect_id)

    def record_attempt_start(self, logical_effect_id: str) -> EffectAttempt:
        """Record the start of an execution attempt."""
        with self._lock:
            record = self._records[logical_effect_id]
            attempt_num = len(record.attempts) + 1
            attempt = EffectAttempt(
                attempt_number=attempt_num,
                started_at=datetime.now(UTC),
                status=record.state,
            )
            record.attempts.append(attempt)
            record.updated_at = datetime.now(UTC)
            return attempt

    def record_attempt_success(
        self,
        logical_effect_id: str,
        attempt_id: UUID,
        result: Any,
    ) -> None:
        """Mark an attempt and the parent effect record as VERIFIED."""
        with self._lock:
            record = self._records[logical_effect_id]
            now = datetime.now(UTC)
            for attempt in record.attempts:
                if attempt.attempt_id == attempt_id:
                    attempt.completed_at = now
                    attempt.status = EffectState.VERIFIED
                    attempt.raw_result = result
                    break

            record.cached_result = result
            record.transition_to(EffectState.VERIFIED)

    def record_attempt_failure(
        self,
        logical_effect_id: str,
        attempt_id: UUID,
        error: str,
        state: EffectState,
        quarantine_reason: str | None = None,
    ) -> None:
        """Record failure or ambiguous outcome on an attempt."""
        with self._lock:
            record = self._records[logical_effect_id]
            now = datetime.now(UTC)
            for attempt in record.attempts:
                if attempt.attempt_id == attempt_id:
                    attempt.completed_at = now
                    attempt.status = state
                    attempt.error_message = error
                    break

            record.transition_to(state, reason=quarantine_reason)

    def is_duplicate(self, logical_effect_id: str) -> bool:
        """Return True if an effect has already completed and verified."""
        with self._lock:
            record = self._records.get(logical_effect_id)
            if not record:
                return False
            return record.state == EffectState.VERIFIED

    def list_by_task(self, task_id: UUID) -> list[EffectRecord]:
        """Return all effect records belonging to a specific task."""
        with self._lock:
            return [r for r in self._records.values() if r.task_id == task_id]

    def clear(self) -> None:
        """Clear all ledger records (used for test isolation)."""
        with self._lock:
            self._records.clear()
