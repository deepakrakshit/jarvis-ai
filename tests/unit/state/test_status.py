"""Tests for JARVIS 14-State Task Lifecycle and Transition Validator."""

import pytest

from jarvis.core.exceptions import StateTransitionError
from jarvis.core.state.status import (
    TERMINAL_STATUSES,
    TaskStatus,
    is_terminal_status,
    validate_transition,
)


def test_14_canonical_states_exist() -> None:
    """Verify all 14 canonical task lifecycle states are defined."""
    assert len(TaskStatus) == 14
    expected_states = {
        "CREATED",
        "QUEUED",
        "PLANNING",
        "EXECUTING",
        "WAITING_TOOL",
        "WAITING_USER",
        "VERIFYING",
        "REPLANNING",
        "DEGRADED",
        "QUARANTINED",
        "COMPLETED",
        "FAILED",
        "CANCELED",
        "EXPIRED",
    }
    actual_states = {s.value for s in TaskStatus}
    assert actual_states == expected_states


def test_terminal_statuses_set() -> None:
    """Verify the terminal states set."""
    assert is_terminal_status(TaskStatus.COMPLETED)
    assert is_terminal_status(TaskStatus.FAILED)
    assert is_terminal_status(TaskStatus.CANCELED)
    assert is_terminal_status(TaskStatus.EXPIRED)
    assert is_terminal_status(TaskStatus.QUARANTINED)

    assert not is_terminal_status(TaskStatus.CREATED)
    assert not is_terminal_status(TaskStatus.QUEUED)
    assert not is_terminal_status(TaskStatus.PLANNING)
    assert not is_terminal_status(TaskStatus.EXECUTING)


def test_valid_forward_transitions() -> None:
    """Verify legal state transitions pass validation without error."""
    # Happy path: CREATED -> QUEUED -> PLANNING -> EXECUTING -> VERIFYING -> COMPLETED
    validate_transition(TaskStatus.CREATED, TaskStatus.QUEUED)
    validate_transition(TaskStatus.QUEUED, TaskStatus.PLANNING)
    validate_transition(TaskStatus.PLANNING, TaskStatus.EXECUTING)
    validate_transition(TaskStatus.EXECUTING, TaskStatus.VERIFYING)
    validate_transition(TaskStatus.VERIFYING, TaskStatus.COMPLETED)

    # Tool waiting and resume
    validate_transition(TaskStatus.EXECUTING, TaskStatus.WAITING_TOOL)
    validate_transition(TaskStatus.WAITING_TOOL, TaskStatus.EXECUTING)

    # User waiting and resume
    validate_transition(TaskStatus.PLANNING, TaskStatus.WAITING_USER)
    validate_transition(TaskStatus.WAITING_USER, TaskStatus.PLANNING)

    # Replanning and degraded
    validate_transition(TaskStatus.VERIFYING, TaskStatus.REPLANNING)
    validate_transition(TaskStatus.REPLANNING, TaskStatus.EXECUTING)
    validate_transition(TaskStatus.EXECUTING, TaskStatus.DEGRADED)
    validate_transition(TaskStatus.DEGRADED, TaskStatus.COMPLETED)

    # Idempotent re-affirmation is valid
    validate_transition(TaskStatus.EXECUTING, TaskStatus.EXECUTING)


def test_invalid_transitions_rejected() -> None:
    """Verify illegal transitions raise StateTransitionError."""
    # Cannot jump from CREATED directly to COMPLETED or EXECUTING
    with pytest.raises(StateTransitionError) as exc:
        validate_transition(TaskStatus.CREATED, TaskStatus.COMPLETED)
    assert "Illegal state transition" in str(exc.value)

    with pytest.raises(StateTransitionError):
        validate_transition(TaskStatus.CREATED, TaskStatus.EXECUTING)

    # Cannot transition backwards from EXECUTING to CREUED or CREATED
    with pytest.raises(StateTransitionError):
        validate_transition(TaskStatus.EXECUTING, TaskStatus.CREATED)


def test_terminal_state_permanence() -> None:
    """Verify that transitioning out of any terminal state is strictly forbidden."""
    for term_status in TERMINAL_STATUSES:
        for any_status in TaskStatus:
            if any_status != term_status:
                with pytest.raises(StateTransitionError) as exc:
                    validate_transition(term_status, any_status)
                assert "Cannot transition out of terminal state" in str(exc.value)
