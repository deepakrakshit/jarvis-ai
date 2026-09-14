"""Integration tests for the central ActionBroker execution pipeline."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.broker.broker import ActionBroker
from jarvis.core.broker.types import (
    EffectAuthorizationRequiredError,
    EffectState,
    IdempotencyConflictError,
)
from jarvis.core.capabilities.manifest import (
    CapabilityManifest,
    RiskClass,
    SideEffectClass,
    ToolType,
)
from jarvis.core.policy.decision import EffectAuthorization


@pytest.fixture
def broker() -> ActionBroker:
    return ActionBroker()


@pytest.fixture
def read_manifest() -> CapabilityManifest:
    return CapabilityManifest(
        capability_id="native:fs:read_file",
        description="Reads file contents",
        risk_class=RiskClass.READ_ONLY,
        side_effect_class=SideEffectClass.NONE,
        tool_type=ToolType.NATIVE,
    )


@pytest.fixture
def write_manifest() -> CapabilityManifest:
    return CapabilityManifest(
        capability_id="native:fs:write_file",
        description="Writes file contents",
        risk_class=RiskClass.BOUNDED_MUTATION,
        side_effect_class=SideEffectClass.NON_IDEMPOTENT,
        tool_type=ToolType.NATIVE,
    )


@pytest.mark.asyncio
async def test_read_action_executes_without_authorization(
    broker: ActionBroker, read_manifest: CapabilityManifest
) -> None:
    """Read-only safe tools execute through Action Broker without requiring HITL authorization."""
    task_id = uuid4()
    args = {"path": "workspace/README.md"}
    call_count = 0

    def mock_reader(params: dict[str, Any]) -> str:
        nonlocal call_count
        call_count += 1
        return "# Project Documentation"

    result = await broker.execute_action(
        task_id=task_id,
        tool_id="native:fs:read_file",
        arguments=args,
        executor_fn=mock_reader,
        manifest=read_manifest,
    )

    assert result == "# Project Documentation"
    assert call_count == 1


@pytest.mark.asyncio
async def test_mutating_action_requires_authorization(
    broker: ActionBroker, write_manifest: CapabilityManifest
) -> None:
    """State-mutating tools strictly require a valid EffectAuthorization."""
    task_id = uuid4()
    args = {"path": "workspace/output.txt", "content": "hello world"}

    with pytest.raises(EffectAuthorizationRequiredError):
        await broker.execute_action(
            task_id=task_id,
            tool_id="native:fs:write_file",
            arguments=args,
            executor_fn=lambda p: {"status": "ok"},
            manifest=write_manifest,
            authorization=None,  # Missing auth
        )


@pytest.mark.asyncio
async def test_mutating_action_executes_with_valid_authorization(
    broker: ActionBroker, write_manifest: CapabilityManifest
) -> None:
    """State-mutating tools execute successfully when provided valid authorization."""
    task_id = uuid4()
    args = {"path": "workspace/output.txt", "content": "hello world"}
    args_hash = broker.compute_canonical_hash(args)

    auth = EffectAuthorization(
        task_id=task_id,
        user_id="user_admin",
        session_id=uuid4(),
        agent_id="coding_agent",
        tool_id="native:fs:write_file",
        canonical_arguments_hash=args_hash,
        target_resource="workspace/output.txt",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        nonce="nonce_12345",
    )

    result = await broker.execute_action(
        task_id=task_id,
        tool_id="native:fs:write_file",
        arguments=args,
        executor_fn=lambda p: {"bytes_written": 11},
        manifest=write_manifest,
        authorization=auth,
    )

    assert result == {"bytes_written": 11}
    assert auth.used is True

    # Record in ledger is VERIFIED
    logical_effect_id = broker.compute_logical_effect_id(task_id, "native:fs:write_file", args)
    record = broker.ledger.get(logical_effect_id)
    assert record is not None
    assert record.state == EffectState.VERIFIED


@pytest.mark.asyncio
async def test_duplicate_execution_returns_cached_result(
    broker: ActionBroker, write_manifest: CapabilityManifest
) -> None:
    """Invoking identical logical effect returns cached result without re-running executor."""
    task_id = uuid4()
    args = {"path": "workspace/data.json", "content": "{}"}
    args_hash = broker.compute_canonical_hash(args)
    call_count = 0

    def executor(p: dict[str, Any]) -> dict[str, str]:
        nonlocal call_count
        call_count += 1
        return {"file": p["path"], "status": "created"}

    auth1 = EffectAuthorization(
        task_id=task_id,
        user_id="user_admin",
        session_id=uuid4(),
        agent_id="coding_agent",
        tool_id="native:fs:write_file",
        canonical_arguments_hash=args_hash,
        target_resource="workspace/data.json",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        nonce="nonce_1",
    )

    # First call executes
    res1 = await broker.execute_action(
        task_id=task_id,
        tool_id="native:fs:write_file",
        arguments=args,
        executor_fn=executor,
        manifest=write_manifest,
        authorization=auth1,
    )
    assert call_count == 1
    assert res1 == {"file": "workspace/data.json", "status": "created"}

    # Second call with identical effect returns cached result immediately
    res2 = await broker.execute_action(
        task_id=task_id,
        tool_id="native:fs:write_file",
        arguments=args,
        executor_fn=executor,
        manifest=write_manifest,
        authorization=None,  # Cached hit doesn't even consume new auth
    )
    assert call_count == 1  # Not called again!
    assert res2 == {"file": "workspace/data.json", "status": "created"}


@pytest.mark.asyncio
async def test_concurrent_execution_lease_conflict(
    broker: ActionBroker, write_manifest: CapabilityManifest
) -> None:
    """Concurrent execution on identical logical_effect_id raises IdempotencyConflictError."""
    task_id = uuid4()
    args = {"path": "workspace/exclusive.lock", "data": 1}
    logical_effect_id = broker.compute_logical_effect_id(task_id, "native:fs:write_file", args)

    # Worker A acquires lease first
    await broker.lease_manager.acquire_async(
        resource_id=logical_effect_id,
        holder_id="worker_a",
        ttl_seconds=60.0,
    )

    # Worker B tries to execute through ActionBroker
    auth = EffectAuthorization(
        task_id=task_id,
        user_id="user_admin",
        session_id=uuid4(),
        agent_id="agent_b",
        tool_id="native:fs:write_file",
        canonical_arguments_hash=broker.compute_canonical_hash(args),
        target_resource="workspace/exclusive.lock",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        nonce="nonce_b",
    )

    with pytest.raises(IdempotencyConflictError) as exc_info:
        await broker.execute_action(
            task_id=task_id,
            tool_id="native:fs:write_file",
            arguments=args,
            executor_fn=lambda p: {"status": "ok"},
            manifest=write_manifest,
            authorization=auth,
            worker_id="worker_b",
        )

    assert logical_effect_id in str(exc_info.value)
