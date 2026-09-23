"""Tests for JARVIS Typed Contracts."""

from jarvis.contracts.action import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    ExecutionTarget,
    RiskTier,
)
from jarvis.contracts.memory import MemoryRecord, MemoryType, ProvenanceSource, TrustLevel
from jarvis.contracts.node import Node, NodeCapability, NodePlatform, NodeStatus
from jarvis.contracts.policy import ApprovalRequest, ApprovalStatus, PolicyDecision, PolicyVerdict
from jarvis.contracts.task import Task, TaskState


def assert_task_state(task: Task, expected: TaskState) -> None:
    assert task.state == expected


def test_task_lifecycle_transitions() -> None:
    """Verify task state transitions and history tracking."""
    task = Task(session_id="SESS-001", raw_intent="Open notepad and write notes")
    assert_task_state(task, TaskState.CREATED)
    assert len(task.state_history) == 0

    task.transition_to(TaskState.PLANNED, reason="Planner formed steps")
    assert_task_state(task, TaskState.PLANNED)
    assert len(task.state_history) == 1
    assert task.state_history[0]["from_state"] == "CREATED"
    assert task.state_history[0]["to_state"] == "PLANNED"

    task.transition_to(TaskState.RUNNING, reason="Dispatched to worker")
    assert_task_state(task, TaskState.RUNNING)

    task.transition_to(TaskState.COMPLETED, reason="Executed and verified")
    assert_task_state(task, TaskState.COMPLETED)
    assert task.completed_at is not None


def test_action_contracts() -> None:
    """Verify ActionRequest and ActionResult validation."""
    req = ActionRequest(
        task_id="TASK-01",
        session_id="SESS-01",
        capability="filesystem.read",
        arguments={"path": "notes.txt"},
        risk_tier=RiskTier.READ_ONLY,
    )
    assert req.action_id.startswith("ACT-")
    assert req.target == ExecutionTarget.HOST

    res = ActionResult(
        action_id=req.action_id,
        task_id=req.task_id,
        status=ActionStatus.SUCCEEDED,
        execution_target=req.target,
        output="Hello world",
        verified=True,
    )
    assert res.status == ActionStatus.SUCCEEDED
    assert res.verified is True


def test_policy_and_approval_contracts() -> None:
    """Verify PolicyDecision and ApprovalRequest models."""
    appr = ApprovalRequest(
        action_id="ACT-01",
        task_id="TASK-01",
        session_id="SESS-01",
        capability="filesystem.delete",
        target_resource="C:/temp/file.txt",
        risk_summary="High risk destructive operation",
    )
    assert appr.status == ApprovalStatus.PENDING

    decision = PolicyDecision(
        action_id="ACT-01",
        verdict=PolicyVerdict.ASK,
        reason="Destructive deletion requires approval",
        capability="filesystem.delete",
        risk_tier="HIGH",
        approval_request=appr,
    )
    assert decision.verdict == PolicyVerdict.ASK
    assert decision.approval_request is not None


def test_memory_and_node_contracts() -> None:
    """Verify MemoryRecord and Node models."""
    mem = MemoryRecord(
        memory_type=MemoryType.USER_PREFERENCE,
        key="editor",
        content="VS Code",
        provenance_source=ProvenanceSource.DIRECT_USER_INSTRUCTION,
        trust_level=TrustLevel.TRUSTED_OPERATOR,
    )
    assert mem.record_id.startswith("MEM-")

    node = Node(
        node_name="Primary Windows PC",
        platform=NodePlatform.WINDOWS,
        status=NodeStatus.ONLINE,
        capabilities=[
            NodeCapability(capability_name="process.launch", description="Launch executable")
        ],
    )
    assert node.node_id.startswith("NODE-")
    assert len(node.capabilities) == 1
