"""Tests for Control Plane LangGraph State Machine & Task Execution Loop."""

from pathlib import Path

import pytest

from jarvis.actions.broker import ActionBroker
from jarvis.contracts.task import Task, TaskState, TaskType
from jarvis.core.control_plane import ControlPlane
from jarvis.execution.windows.host import windows_node
from jarvis.policy.engine import PolicyEngine
from jarvis.storage.database import DatabaseEngine


@pytest.fixture(autouse=True)
def setup_windows() -> None:
    """Ensure Windows node capabilities are registered."""
    windows_node.register_capabilities()


@pytest.mark.asyncio
async def test_control_plane_system_info_task(temp_dir: Path, test_db: DatabaseEngine) -> None:
    """Verify end-to-end task execution through full state machine."""
    policy = PolicyEngine(workspace_dir=temp_dir, database=test_db)
    broker = ActionBroker(policy=policy, database=test_db)
    cp = ControlPlane(broker=broker, policy=policy, database=test_db)

    task = Task(
        session_id="SESS-TEST-CP-1",
        raw_intent="check system info",
    )

    final_task = await cp.execute_task(task)

    assert final_task.state == TaskState.COMPLETED
    assert final_task.task_type == TaskType.WINDOWS_CONTROL
    assert final_task.verification_passed is True

    # Verify transitions were recorded in state history
    states_visited = [record["to_state"] for record in final_task.state_history]
    assert TaskState.CLASSIFIED.value in states_visited
    assert TaskState.PLANNED.value in states_visited
    assert TaskState.RUNNING.value in states_visited
    assert TaskState.OBSERVING.value in states_visited
    assert TaskState.VERIFYING.value in states_visited
    assert TaskState.COMPLETED.value in states_visited


@pytest.mark.asyncio
async def test_control_plane_dangerous_command_denial(
    temp_dir: Path, test_db: DatabaseEngine
) -> None:
    """Verify policy gate failure on prohibited destructive shell command."""
    policy = PolicyEngine(workspace_dir=temp_dir, database=test_db)
    broker = ActionBroker(policy=policy, database=test_db)
    cp = ControlPlane(broker=broker, policy=policy, database=test_db)

    task = Task(
        session_id="SESS-TEST-CP-2",
        raw_intent="run shell format C:",
    )

    final_task = await cp.execute_task(task)
    assert final_task.state == TaskState.FAILED
    assert "Policy Denial" in (final_task.error_message or "")


@pytest.mark.asyncio
async def test_control_plane_cognitive_conversational_turn(
    temp_dir: Path, test_db: DatabaseEngine
) -> None:
    """Verify pure cognitive tasks route through the Model Router to COMPLETED."""
    policy = PolicyEngine(workspace_dir=temp_dir, database=test_db)
    broker = ActionBroker(policy=policy, database=test_db)
    cp = ControlPlane(broker=broker, policy=policy, database=test_db)

    task = Task(
        session_id="SESS-TEST-CP-3",
        raw_intent="What is the speed of light in vacuum?",
    )

    final_task = await cp.execute_task(task)
    assert final_task.state == TaskState.COMPLETED
    assert final_task.result_summary is not None
    assert len(final_task.result_summary) > 0
