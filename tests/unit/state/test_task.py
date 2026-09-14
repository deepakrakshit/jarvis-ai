"""Tests for JarvisTask domain model and TaskEvent generation."""

import pytest

from jarvis.core.exceptions import StateTransitionError
from jarvis.core.state.status import TaskStatus
from jarvis.core.state.task import JarvisTask


def test_task_creation_and_defaults() -> None:
    """Verify task initialization, correlation IDs, and defaults."""
    task = JarvisTask(input_prompt="Summarize my calendar today")
    assert task.status == TaskStatus.CREATED
    assert task.title == "Untitled Task"
    assert task.input_prompt == "Summarize my calendar today"
    assert task.correlation.task_id == task.task_id
    assert task.correlation.session_id == task.session_id
    assert not task.is_terminal()
    assert task.completed_at is None


def _assert_status(task: JarvisTask, expected: TaskStatus) -> None:
    assert task.status == expected


def test_task_legal_transitions_and_events() -> None:
    """Verify legal state transitions generate correct immutable TaskEvents."""
    task = JarvisTask(title="Code Refactor", input_prompt="Refactor auth.py")

    # CREATED -> QUEUED
    evt1 = task.transition_to(TaskStatus.QUEUED, reason="Enqueued in background worker")
    _assert_status(task, TaskStatus.QUEUED)
    assert evt1.from_status == TaskStatus.CREATED
    assert evt1.to_status == TaskStatus.QUEUED
    assert evt1.payload["reason"] == "Enqueued in background worker"
    assert evt1.task_id == task.task_id

    # QUEUED -> PLANNING
    evt2 = task.transition_to(TaskStatus.PLANNING)
    _assert_status(task, TaskStatus.PLANNING)
    assert evt2.from_status == TaskStatus.QUEUED
    assert evt2.to_status == TaskStatus.PLANNING

    # PLANNING -> EXECUTING (starts budget clock)
    assert task.budget.started_at is None
    evt3 = task.transition_to(TaskStatus.EXECUTING)
    assert evt3.to_status == TaskStatus.EXECUTING
    _assert_status(task, TaskStatus.EXECUTING)
    assert task.budget.started_at is not None

    # EXECUTING -> VERIFYING -> COMPLETED
    task.transition_to(TaskStatus.VERIFYING)
    evt_final = task.transition_to(TaskStatus.COMPLETED)
    _assert_status(task, TaskStatus.COMPLETED)
    assert task.is_terminal()
    assert task.completed_at is not None
    assert evt_final.to_status == TaskStatus.COMPLETED


def test_task_illegal_transition_rejection() -> None:
    """Verify illegal transitions are rejected on JarvisTask."""
    task = JarvisTask(input_prompt="Write an essay")
    with pytest.raises(StateTransitionError):
        task.transition_to(TaskStatus.COMPLETED)
    assert task.status == TaskStatus.CREATED
