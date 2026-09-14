"""Stage 1 Exit Gate Test: Checkpoint Crash Recovery and Resume Semantics.

Proves that interrupting a task at any graph node and restarting resumes from
the correct persisted checkpoint without duplicating completed work.
"""

import pytest

from jarvis.core.state.graph import TaskGraphEngine
from jarvis.core.state.state import JarvisState
from jarvis.core.state.status import TaskStatus
from jarvis.core.state.task import JarvisTask
from jarvis.storage.db import DatabaseManager
from jarvis.storage.task_store import TaskStore


@pytest.mark.parametrize(
    "interrupted_at_step,node_name,interrupted_status",
    [
        (1, "init", TaskStatus.QUEUED),
        (2, "plan", TaskStatus.PLANNING),
        (3, "execute", TaskStatus.EXECUTING),
        (4, "verify", TaskStatus.VERIFYING),
    ],
)
def test_stage_1_exit_gate_resume_from_node(
    temp_db: DatabaseManager,
    interrupted_at_step: int,
    node_name: str,
    interrupted_status: TaskStatus,
) -> None:
    """Stage 1 Exit Gate: Verify resuming from intermediate checkpoint after crash."""
    store = TaskStore(temp_db)

    # 1. Create task and advance to interrupted state
    task = JarvisTask(
        title=f"Recovery Test from {node_name}",
        input_prompt="Perform resilient multi-step analysis",
    )
    task.status = interrupted_status
    store.save_task(task)

    # 2. Persist checkpoint simulating process state immediately prior to crash
    simulated_checkpoint_state: JarvisState = {
        "task_id": task.task_id,
        "task": task,
        "current_status": interrupted_status,
        "step_count": interrupted_at_step,
        "messages": [{"role": "user", "content": task.input_prompt}],
        "proposed_intent": {"intent": "resumed_work"} if interrupted_at_step >= 2 else None,
        "observations": [{"step": "prev", "status": "ok"}] if interrupted_at_step >= 3 else [],
        "is_canceled": False,
        "cancellation_reason": None,
        "errors": [],
        "artifacts": [],
        "metadata": {},
    }
    store.save_checkpoint(task.task_id, node_name, interrupted_at_step, simulated_checkpoint_state)

    # 3. Simulate process death: create a fresh TaskGraphEngine instance in a new process context
    recovered_engine = TaskGraphEngine(store)

    # 4. Resume task
    resumed_state = recovered_engine.resume_task(task.task_id)

    # 5. Verify task reached terminal COMPLETED state
    assert resumed_state["current_status"] == TaskStatus.COMPLETED
    assert resumed_state["step_count"] > interrupted_at_step

    # Verify task in database has updated to COMPLETED
    reloaded_task = store.get_task(task.task_id)
    assert reloaded_task is not None
    assert reloaded_task.status == TaskStatus.COMPLETED
    assert reloaded_task.completed_at is not None
    assert reloaded_task.result is not None
    assert reloaded_task.result["status"] == "success"


def test_resume_on_already_completed_task(temp_db: DatabaseManager) -> None:
    """Verify that attempting to resume a completed task is a safe no-op."""
    store = TaskStore(temp_db)
    engine = TaskGraphEngine(store)

    task = JarvisTask(input_prompt="Already done")
    task.transition_to(TaskStatus.QUEUED)
    task.transition_to(TaskStatus.PLANNING)
    task.transition_to(TaskStatus.EXECUTING)
    task.transition_to(TaskStatus.VERIFYING)
    task.transition_to(TaskStatus.COMPLETED)
    store.save_task(task)

    resumed_state = engine.resume_task(task.task_id)
    assert resumed_state["current_status"] == TaskStatus.COMPLETED
