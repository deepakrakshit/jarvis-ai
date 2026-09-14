"""JARVIS 14-State Task Lifecycle and State Transition Validator.

Enforces deterministic, replay-safe state machine transitions conforming to Layer 5.
"""

from enum import StrEnum

from jarvis.core.exceptions import StateTransitionError


class TaskStatus(StrEnum):
    """The 14 canonical task lifecycle states of JARVIS v1.0.0."""

    CREATED = "CREATED"
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    WAITING_TOOL = "WAITING_TOOL"
    WAITING_USER = "WAITING_USER"
    VERIFYING = "VERIFYING"
    REPLANNING = "REPLANNING"
    DEGRADED = "DEGRADED"  # Completed with reduced service / fallback model
    QUARANTINED = "QUARANTINED"  # Stalled or anomalous task moved to DLQ
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"  # Gracefully aborted by user or policy
    EXPIRED = "EXPIRED"  # Exceeded maximum wall-time or step budget


# Terminal states from which no further transitions are permitted
TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.EXPIRED,
        TaskStatus.QUARANTINED,
    }
)

# Deterministic allowed state transition graph
VALID_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset(
        {
            TaskStatus.QUEUED,
            TaskStatus.CANCELED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.QUEUED: frozenset(
        {
            TaskStatus.PLANNING,
            TaskStatus.CANCELED,
            TaskStatus.EXPIRED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.PLANNING: frozenset(
        {
            TaskStatus.EXECUTING,
            TaskStatus.WAITING_USER,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
            TaskStatus.EXPIRED,
        }
    ),
    TaskStatus.EXECUTING: frozenset(
        {
            TaskStatus.WAITING_TOOL,
            TaskStatus.WAITING_USER,
            TaskStatus.VERIFYING,
            TaskStatus.DEGRADED,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
            TaskStatus.EXPIRED,
        }
    ),
    TaskStatus.WAITING_TOOL: frozenset(
        {
            TaskStatus.EXECUTING,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
            TaskStatus.EXPIRED,
            TaskStatus.QUARANTINED,
        }
    ),
    TaskStatus.WAITING_USER: frozenset(
        {
            TaskStatus.EXECUTING,
            TaskStatus.PLANNING,
            TaskStatus.CANCELED,
            TaskStatus.EXPIRED,
        }
    ),
    TaskStatus.VERIFYING: frozenset(
        {
            TaskStatus.COMPLETED,
            TaskStatus.REPLANNING,
            TaskStatus.DEGRADED,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
            TaskStatus.EXPIRED,
        }
    ),
    TaskStatus.REPLANNING: frozenset(
        {
            TaskStatus.PLANNING,
            TaskStatus.EXECUTING,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
            TaskStatus.EXPIRED,
        }
    ),
    TaskStatus.DEGRADED: frozenset(
        {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
            TaskStatus.EXPIRED,
        }
    ),
    # Terminal states have an empty set of valid outgoing transitions
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELED: frozenset(),
    TaskStatus.EXPIRED: frozenset(),
    TaskStatus.QUARANTINED: frozenset(),
}


def is_terminal_status(status: TaskStatus) -> bool:
    """Return True if status is a terminal state."""
    return status in TERMINAL_STATUSES


def validate_transition(current_status: TaskStatus, target_status: TaskStatus) -> None:
    """Validate whether transitioning from current_status to target_status is legal.

    Raises StateTransitionError if the transition is illegal or attempts to leave a terminal state.
    """
    if current_status == target_status:
        return  # Idempotent re-affirmation of current status is safe

    if is_terminal_status(current_status):
        raise StateTransitionError(
            f"Cannot transition out of terminal state '{current_status.value}' to '{target_status.value}'."
        )

    allowed_targets = VALID_TRANSITIONS.get(current_status, frozenset())
    if target_status not in allowed_targets:
        raise StateTransitionError(
            f"Illegal state transition from '{current_status.value}' to '{target_status.value}'."
        )
