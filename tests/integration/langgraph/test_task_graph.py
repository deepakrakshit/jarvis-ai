"""Integration tests for TaskGraphEngine complete task execution."""

from jarvis.core.state.graph import TaskGraphEngine
from jarvis.core.state.status import TaskStatus
from jarvis.core.state.task import JarvisTask
from jarvis.storage.db import DatabaseManager
from jarvis.storage.task_store import TaskStore


def test_full_task_graph_execution(temp_db: DatabaseManager) -> None:
    """Verify execution through init -> plan -> execute -> verify -> complete."""
    store = TaskStore(temp_db)
    engine = TaskGraphEngine(store)

    task = JarvisTask(
        title="Analyze System Health",
        input_prompt="Check disk usage and memory consumption",
    )

    final_state = engine.run_task(task)

    # Verify final state in memory
    assert final_state["current_status"] == TaskStatus.COMPLETED
    assert final_state["step_count"] == 5  # init, plan, execute, verify, complete
    assert final_state["proposed_intent"] is not None
    assert final_state["proposed_intent"]["intent"] == "analyze_and_execute"
    assert len(final_state["observations"]) > 0

    # Verify task updated in database
    persisted_task = store.get_task(task.task_id)
    assert persisted_task is not None
    assert persisted_task.status == TaskStatus.COMPLETED
    assert persisted_task.completed_at is not None
    assert persisted_task.result is not None
    assert persisted_task.result["status"] == "success"

    # Verify all 5 checkpoints saved
    with temp_db.get_connection() as conn:
        count = conn.execute(
            "SELECT count(*) FROM task_checkpoints WHERE task_id = ?;",
            (task.task_id,),
        ).fetchone()[0]
        assert count == 5

    # Verify all 5 state transition events persisted
    events = store.get_task_events(task.task_id)
    assert len(events) == 5
    statuses = [e.to_status for e in events]
    assert statuses == [
        TaskStatus.QUEUED,
        TaskStatus.PLANNING,
        TaskStatus.EXECUTING,
        TaskStatus.VERIFYING,
        TaskStatus.COMPLETED,
    ]
