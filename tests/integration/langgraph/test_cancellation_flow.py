"""Integration tests for mid-flight and pre-flight task cancellation in LangGraph."""

from jarvis.core.state.graph import TaskGraphEngine
from jarvis.core.state.status import TaskStatus
from jarvis.core.state.task import JarvisTask
from jarvis.storage.db import DatabaseManager
from jarvis.storage.task_store import TaskStore


def test_pre_flight_cancellation(temp_db: DatabaseManager) -> None:
    """Verify task already canceled before execution halts immediately in CANCELED."""
    store = TaskStore(temp_db)
    engine = TaskGraphEngine(store)

    task = JarvisTask(input_prompt="Canceled task")
    store.save_task(task)
    store.record_cancellation(task.task_id, reason="User clicked Cancel before start")

    final_state = engine.run_task(task)
    assert final_state["current_status"] == TaskStatus.CANCELED
    assert final_state["is_canceled"] is True

    reloaded = store.get_task(task.task_id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.CANCELED
    assert reloaded.is_terminal()
