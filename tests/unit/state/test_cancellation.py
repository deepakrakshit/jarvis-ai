"""Tests for CancellationManager and recursive child cancellation."""

from jarvis.core.state.cancellation import CancellationManager
from jarvis.core.state.status import TaskStatus
from jarvis.core.state.task import JarvisTask
from jarvis.storage.db import DatabaseManager
from jarvis.storage.task_store import TaskStore


def test_single_task_cancellation(temp_db: DatabaseManager) -> None:
    """Verify canceling an individual task."""
    store = TaskStore(temp_db)
    cm = CancellationManager(store)

    task = JarvisTask(input_prompt="Run batch process")
    task.transition_to(TaskStatus.QUEUED)
    store.save_task(task)

    canceled_ids = cm.cancel_task(task.task_id, reason="User clicked stop")
    assert task.task_id in canceled_ids

    # Verify task state updated to CANCELED
    reloaded = store.get_task(task.task_id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.CANCELED
    assert reloaded.is_terminal()

    # Verify is_canceled check
    is_canc, reason = cm.is_canceled(task.task_id)
    assert is_canc is True
    assert reason == "User clicked stop"


def test_cascading_cancellation_propagation(temp_db: DatabaseManager) -> None:
    """Verify canceling parent cancels all children and grandchildren."""
    store = TaskStore(temp_db)
    cm = CancellationManager(store)

    # Create root parent task
    parent = JarvisTask(input_prompt="Parent pipeline", status=TaskStatus.EXECUTING)
    store.save_task(parent)

    # Create child 1 & child 2
    child1 = JarvisTask(
        parent_task_id=parent.task_id, input_prompt="Subtask 1", status=TaskStatus.EXECUTING
    )
    store.save_task(child1)

    child2 = JarvisTask(
        parent_task_id=parent.task_id, input_prompt="Subtask 2", status=TaskStatus.QUEUED
    )
    store.save_task(child2)

    # Create grandchild
    grandchild = JarvisTask(
        parent_task_id=child1.task_id, input_prompt="Sub-subtask", status=TaskStatus.EXECUTING
    )
    store.save_task(grandchild)

    # Cancel root parent
    affected = cm.cancel_task(parent.task_id, reason="Emergency abort")

    assert parent.task_id in affected
    assert child1.task_id in affected
    assert child2.task_id in affected
    assert grandchild.task_id in affected

    # Verify all are marked CANCELED
    t1 = store.get_task(child1.task_id)
    t2 = store.get_task(child2.task_id)
    t3 = store.get_task(grandchild.task_id)
    assert t1 is not None and t1.status == TaskStatus.CANCELED
    assert t2 is not None and t2.status == TaskStatus.CANCELED
    assert t3 is not None and t3.status == TaskStatus.CANCELED

    # Verify is_canceled returns True for grandchild
    is_canc, _ = cm.is_canceled(grandchild.task_id)
    assert is_canc is True
