"""Integration tests for step and wall-time budget exhaustion in LangGraph."""

from datetime import UTC, datetime, timedelta

from jarvis.core.state.graph import TaskGraphEngine
from jarvis.core.state.status import TaskStatus
from jarvis.core.state.task import JarvisTask, TaskBudget
from jarvis.storage.db import DatabaseManager
from jarvis.storage.task_store import TaskStore


def test_step_budget_exhaustion_in_graph(temp_db: DatabaseManager) -> None:
    """Verify task halts and transitions to EXPIRED when step budget ceiling is reached."""
    store = TaskStore(temp_db)
    engine = TaskGraphEngine(store)

    # Set step limit to 2 (init takes 1, plan takes 2 -> ceiling reached before execute)
    limited_budget = TaskBudget(max_graph_steps=2)
    task = JarvisTask(
        input_prompt="Long running calculation",
        budget=limited_budget,
    )

    final_state = engine.run_task(task)
    assert final_state["current_status"] == TaskStatus.EXPIRED

    reloaded = store.get_task(task.task_id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.EXPIRED
    assert reloaded.is_terminal()


def test_wall_time_budget_exhaustion_in_graph(temp_db: DatabaseManager) -> None:
    """Verify task halts and transitions to EXPIRED when wall-clock budget is exceeded."""
    store = TaskStore(temp_db)
    engine = TaskGraphEngine(store)

    # Simulate expired wall-time budget
    expired_budget = TaskBudget(max_wall_time_seconds=1.0)
    expired_budget.started_at = datetime.now(UTC) - timedelta(seconds=10)

    task = JarvisTask(
        input_prompt="Timed out task",
        budget=expired_budget,
    )

    final_state = engine.run_task(task)
    assert final_state["current_status"] == TaskStatus.EXPIRED

    reloaded = store.get_task(task.task_id)
    assert reloaded is not None
    assert reloaded.status == TaskStatus.EXPIRED
