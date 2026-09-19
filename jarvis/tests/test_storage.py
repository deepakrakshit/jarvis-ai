"""Tests for SQLite Storage Engine and Repositories."""

from jarvis.contracts.action import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    ExecutionTarget,
    RiskTier,
)
from jarvis.contracts.memory import MemoryRecord, MemoryType
from jarvis.contracts.task import Task, TaskState, TaskType
from jarvis.storage.database import DatabaseEngine


def test_task_storage_roundtrip(test_db: DatabaseEngine) -> None:
    """Verify task persistence and retrieval."""
    task = Task(
        session_id="SESS-TEST-1",
        raw_intent="Check CPU status",
        task_type=TaskType.WINDOWS_CONTROL,
    )
    test_db.save_task(task)

    loaded = test_db.get_task(task.task_id)
    assert loaded is not None
    assert loaded.task_id == task.task_id
    assert loaded.session_id == "SESS-TEST-1"
    assert loaded.raw_intent == "Check CPU status"
    assert loaded.state == TaskState.CREATED

    # Update state and save
    task.transition_to(TaskState.COMPLETED, reason="Done")
    task.verification_passed = True
    task.result_summary = "CPU is at 12%"
    test_db.save_task(task)

    updated = test_db.get_task(task.task_id)
    assert updated is not None
    assert updated.state == TaskState.COMPLETED
    assert updated.verification_passed is True
    assert updated.result_summary == "CPU is at 12%"


def test_action_and_audit_storage(test_db: DatabaseEngine) -> None:
    """Verify action requests, results, and audit logging."""
    task = Task(session_id="SESS-02", raw_intent="Read config")
    test_db.save_task(task)

    req = ActionRequest(
        task_id=task.task_id,
        session_id="SESS-02",
        capability="filesystem.read",
        arguments={"path": "test.txt"},
        risk_tier=RiskTier.READ_ONLY,
    )
    test_db.save_action_request(req)

    res = ActionResult(
        action_id=req.action_id,
        task_id=task.task_id,
        status=ActionStatus.SUCCEEDED,
        execution_target=ExecutionTarget.HOST,
        output="Config data",
        verified=True,
    )
    test_db.save_action_result(res)

    test_db.log_audit_event(
        event_type="ACTION_EXECUTED",
        action_id=req.action_id,
        task_id=task.task_id,
        capability="filesystem.read",
        verdict="ALLOW",
        details={"status": "SUCCEEDED"},
    )


def test_memory_storage_and_search(test_db: DatabaseEngine) -> None:
    """Verify memory persistence and keyword search."""
    mem1 = MemoryRecord(
        memory_type=MemoryType.USER_PREFERENCE,
        key="preferred_voice",
        content="The user prefers the voice Algenib with concise answers.",
        importance_score=0.9,
    )
    mem2 = MemoryRecord(
        memory_type=MemoryType.PROJECT,
        key="repo_details",
        content="JARVIS architecture is defined in ARCHITECTURE.md.",
        importance_score=0.8,
    )
    test_db.save_memory(mem1)
    test_db.save_memory(mem2)

    results = test_db.search_memories("Algenib")
    assert len(results) == 1
    assert results[0].key == "preferred_voice"

    arch_results = test_db.search_memories("ARCHITECTURE")
    assert len(arch_results) == 1
    assert arch_results[0].key == "repo_details"
