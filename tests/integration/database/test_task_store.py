"""Integration tests for TaskStore and 6 canonical database tables."""

from jarvis.core.state.state import JarvisState
from jarvis.core.state.status import TaskStatus
from jarvis.core.state.task import JarvisTask
from jarvis.storage.db import DatabaseManager
from jarvis.storage.task_store import TaskStore


def test_task_crud(temp_db: DatabaseManager) -> None:
    """Verify task saving, retrieval, and updates."""
    store = TaskStore(temp_db)
    task = JarvisTask(title="Initial Title", input_prompt="Initial Prompt")
    store.save_task(task)

    retrieved = store.get_task(task.task_id)
    assert retrieved is not None
    assert retrieved.task_id == task.task_id
    assert retrieved.title == "Initial Title"
    assert retrieved.status == TaskStatus.CREATED

    # Update status
    task.transition_to(TaskStatus.QUEUED)
    store.save_task(task)

    updated = store.get_task(task.task_id)
    assert updated is not None
    assert updated.status == TaskStatus.QUEUED


def test_event_recording_and_retrieval(temp_db: DatabaseManager) -> None:
    """Verify task event persistence and chronological ordering."""
    store = TaskStore(temp_db)
    task = JarvisTask(input_prompt="Event test")
    store.save_task(task)

    evt1 = task.transition_to(TaskStatus.QUEUED)
    store.record_event(evt1)

    evt2 = task.transition_to(TaskStatus.PLANNING)
    store.record_event(evt2)

    events = store.get_task_events(task.task_id)
    assert len(events) == 2
    assert events[0].to_status == TaskStatus.QUEUED
    assert events[1].to_status == TaskStatus.PLANNING


def test_checkpoint_saving_and_latest_retrieval(temp_db: DatabaseManager) -> None:
    """Verify state checkpointing and fetching highest step_count."""
    store = TaskStore(temp_db)
    task = JarvisTask(input_prompt="Checkpoint test")
    store.save_task(task)

    state1: JarvisState = {
        "task_id": task.task_id,
        "current_status": TaskStatus.PLANNING,
        "step_count": 1,
        "messages": [{"role": "user", "content": "prompt"}],
    }
    store.save_checkpoint(task.task_id, "plan", 1, state1)

    state2: JarvisState = {
        "task_id": task.task_id,
        "current_status": TaskStatus.EXECUTING,
        "step_count": 2,
        "messages": [{"role": "user", "content": "prompt"}],
    }
    store.save_checkpoint(task.task_id, "execute", 2, state2)

    latest = store.get_latest_checkpoint(task.task_id)
    assert latest is not None
    assert latest["node_name"] == "execute"
    assert latest["step_count"] == 2
    assert latest["state"]["current_status"] == "EXECUTING"


def test_record_error_and_cancellation(temp_db: DatabaseManager) -> None:
    """Verify recording execution errors and cancellation signals."""
    store = TaskStore(temp_db)
    task = JarvisTask(input_prompt="Error & Cancel test")
    store.save_task(task)

    store.record_error(
        task_id=task.task_id,
        error_type="SandboxTimeoutError",
        message="Command exceeded 30s limit",
        step_count=3,
        details={"pid": 1234},
    )

    store.record_cancellation(task.task_id, reason="Admin stopped task")
    is_canc, reason = store.is_task_canceled(task.task_id)
    assert is_canc is True
    assert reason == "Admin stopped task"
