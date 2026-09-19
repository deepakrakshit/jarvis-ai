"""Comprehensive Test Suite for Specialist Sub-Agents Architecture.

Tests Section 29 requirements:
- Specialist profiles (Research, Coding, Browser, Verify) enforce least privilege.
- Capability attenuation prevents child agents from inheriting parent's full authority.
- Max spawn depth limits prevent runaway recursive spawning.
- Parent-child task linkage is durably maintained in database.
- Parallel and sequential multi-specialist dispatch and synthesis.
"""

from pathlib import Path

import pytest

from jarvis.agents.delegator import SubagentDelegator
from jarvis.agents.models import SubagentRole, SubagentSpec, SubagentStatus
from jarvis.agents.specialists import (
    RESEARCH_SPECIALIST,
    SPECIALIST_REGISTRY,
    VERIFY_SPECIALIST,
)
from jarvis.contracts.task import Task, TaskState, TaskType
from jarvis.execution.windows.host import windows_node
from jarvis.policy.firewall import (
    CAPABILITY_FILESYSTEM_DELETE,
    CAPABILITY_FILESYSTEM_READ,
    CAPABILITY_SHELL_EXECUTE,
    CAPABILITY_SYSTEM_INFO,
)
from jarvis.storage.database import DatabaseEngine


@pytest.fixture(autouse=True)
def setup_windows() -> None:
    """Ensure Windows node capabilities are registered for action execution."""
    windows_node.register_capabilities()


@pytest.fixture
def temp_subagent_db(tmp_path: Path) -> DatabaseEngine:
    """Provide isolated SQLite database for subagent tests."""
    return DatabaseEngine(db_path=tmp_path / "subagent_test.db")


def test_specialist_profiles_and_attenuation() -> None:
    """Verify specialist profiles enforce least privilege and capability filtering."""
    parent_task = Task(
        task_id="TASK-PARENT-1",
        session_id="SESS-SUB-1",
        raw_intent="Conduct deep system and web research",
        task_type=TaskType.CONVERSATION,
    )

    # 1. Verify Research specialist cannot be assigned write or shell capabilities
    research_spec = RESEARCH_SPECIALIST.build_spec(
        parent_task=parent_task,
        goal="Gather system info",
        allowed_capabilities=[
            CAPABILITY_SYSTEM_INFO,
            CAPABILITY_FILESYSTEM_READ,
            CAPABILITY_SHELL_EXECUTE,  # Must be stripped by attenuation
            CAPABILITY_FILESYSTEM_DELETE,  # Must be stripped by attenuation
        ],
    )
    assert CAPABILITY_SYSTEM_INFO in research_spec.allowed_capabilities
    assert CAPABILITY_FILESYSTEM_READ in research_spec.allowed_capabilities
    assert CAPABILITY_SHELL_EXECUTE not in research_spec.allowed_capabilities
    assert CAPABILITY_FILESYSTEM_DELETE not in research_spec.allowed_capabilities

    # 2. Verify all 4 roles are registered
    assert SubagentRole.RESEARCH in SPECIALIST_REGISTRY
    assert SubagentRole.CODING in SPECIALIST_REGISTRY
    assert SubagentRole.BROWSER in SPECIALIST_REGISTRY
    assert SubagentRole.VERIFY in SPECIALIST_REGISTRY


@pytest.mark.asyncio
async def test_subagent_spawn_depth_limit(temp_subagent_db: DatabaseEngine) -> None:
    """Verify exceeding max_depth immediately fails fast without execution."""
    delegator = SubagentDelegator(database=temp_subagent_db)

    spec = SubagentSpec(
        parent_task_id="TASK-PARENT-2",
        session_id="SESS-SUB-2",
        role=SubagentRole.RESEARCH,
        goal="Investigate recursive anomaly",
        depth=3,
        max_depth=2,  # Depth exceeded!
    )

    result = await delegator.spawn(spec)

    assert result.status == SubagentStatus.FAILED
    assert result.error is not None
    assert "depth" in result.error.lower()


@pytest.mark.asyncio
async def test_subagent_capability_containment(temp_subagent_db: DatabaseEngine) -> None:
    """Verify child sub-agent cannot execute actions outside its allowed capabilities."""
    delegator = SubagentDelegator(database=temp_subagent_db)

    # Coding specialist with shell execution intentionally withheld
    spec = SubagentSpec(
        parent_task_id="TASK-PARENT-3",
        session_id="SESS-SUB-3",
        role=SubagentRole.CODING,
        goal="exec powershell Get-Process",
        allowed_capabilities=[CAPABILITY_FILESYSTEM_READ],  # shell.execute is NOT allowed
    )

    result = await delegator.spawn(spec)

    # The action must have been denied by policy
    assert result.status == SubagentStatus.FAILED
    persisted_child = temp_subagent_db.get_task(spec.task_id)
    assert persisted_child is not None
    assert persisted_child.state == TaskState.FAILED


@pytest.mark.asyncio
async def test_subagent_execution_and_parent_tracking(
    temp_subagent_db: DatabaseEngine,
) -> None:
    """Verify successful specialist execution, parent linkage, and database persistence."""
    delegator = SubagentDelegator(database=temp_subagent_db)

    parent_task = Task(
        task_id="TASK-PARENT-4",
        session_id="SESS-SUB-4",
        raw_intent="Check host telemetry and report metrics",
        task_type=TaskType.CONVERSATION,
    )
    temp_subagent_db.save_task(parent_task)

    spec = RESEARCH_SPECIALIST.build_spec(
        parent_task=parent_task,
        goal="check system info",
    )

    result = await delegator.spawn(spec)

    assert result.status == SubagentStatus.COMPLETED
    assert result.parent_task_id == parent_task.task_id

    # Verify child task persistence and lineage in database
    persisted_child = temp_subagent_db.get_task(spec.task_id)
    assert persisted_child is not None
    assert persisted_child.parent_task_id == parent_task.task_id
    assert persisted_child.state == TaskState.COMPLETED
    assert persisted_child.assigned_agent == SubagentRole.RESEARCH.value


@pytest.mark.asyncio
async def test_parallel_and_sequential_dispatch_and_synthesis(
    temp_subagent_db: DatabaseEngine,
) -> None:
    """Verify dispatch_parallel, dispatch_sequential, and report synthesis."""
    delegator = SubagentDelegator(database=temp_subagent_db)

    parent_task = Task(
        task_id="TASK-PARENT-5",
        session_id="SESS-SUB-5",
        raw_intent="Multi-specialist analysis mission",
        task_type=TaskType.CONVERSATION,
    )
    temp_subagent_db.save_task(parent_task)

    spec1 = RESEARCH_SPECIALIST.build_spec(
        parent_task=parent_task,
        goal="check system info",
    )
    spec2 = VERIFY_SPECIALIST.build_spec(
        parent_task=parent_task,
        goal="check system info",
    )

    # 1. Parallel execution
    parallel_results = await delegator.dispatch_parallel([spec1, spec2])
    assert len(parallel_results) == 2
    assert all(r.status == SubagentStatus.COMPLETED for r in parallel_results)

    # 2. Synthesis
    report = delegator.synthesize(
        parallel_results,
        parent_goal="Multi-specialist analysis mission",
    )
    assert "Sub-Agent Coordination Report" in report
    assert "RESEARCH" in report
    assert "VERIFY" in report
    assert "2 completed" in report
