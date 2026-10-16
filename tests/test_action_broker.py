"""Tests for Action Broker execution pipeline and registry."""

import pytest

from jarvis.actions.broker import ActionBroker
from jarvis.contracts.action import ActionRequest, ActionStatus, RiskTier
from jarvis.policy.engine import PolicyEngine
from jarvis.policy.firewall import CAPABILITY_FILESYSTEM_READ, CAPABILITY_SHELL_EXECUTE


@pytest.mark.asyncio
async def test_action_broker_successful_execution() -> None:
    """Verify end-to-end execution of an authorized capability."""
    broker = ActionBroker()

    # Register mock capability handler
    def mock_read_handler(req: ActionRequest) -> str:
        return f"Contents of {req.arguments.get('path')}"

    from jarvis.actions.registry import capability_registry

    original_handler = capability_registry._handlers.get(CAPABILITY_FILESYSTEM_READ)
    try:
        capability_registry.register(CAPABILITY_FILESYSTEM_READ, mock_read_handler)

        req = ActionRequest(
            task_id="TASK-EXEC-1",
            session_id="SESS-01",
            capability=CAPABILITY_FILESYSTEM_READ,
            arguments={"path": "test.txt"},
            risk_tier=RiskTier.READ_ONLY,
        )

        result = await broker.execute(req)
        assert result.status == ActionStatus.SUCCEEDED
        assert result.output == "Contents of test.txt"
        assert result.verified is False
        assert result.observation["verification"] == "UNVERIFIED"
        assert result.duration_ms > 0.0
    finally:
        if original_handler is not None:
            capability_registry.register(CAPABILITY_FILESYSTEM_READ, original_handler)
        else:
            capability_registry._handlers.pop(CAPABILITY_FILESYSTEM_READ, None)


@pytest.mark.asyncio
async def test_action_broker_policy_denial() -> None:
    """Verify broker aborts execution when policy engine denies action."""
    broker = ActionBroker()

    req = ActionRequest(
        task_id="TASK-EXEC-2",
        session_id="SESS-01",
        capability=CAPABILITY_SHELL_EXECUTE,
        arguments={"command": "del /f /s /q C:\\Windows"},
        risk_tier=RiskTier.HIGH,
    )

    result = await broker.execute(req)
    assert result.status == ActionStatus.DENIED
    assert "Policy Denial" in str(result.error)
    assert result.verified is False


@pytest.mark.asyncio
async def test_action_broker_marks_nonzero_shell_result_failed() -> None:
    """A handler result with a non-zero exit code is not a successful action."""
    broker = ActionBroker(policy=PolicyEngine(require_approvals=False))

    from jarvis.actions.registry import capability_registry

    original_handler = capability_registry._handlers.get(CAPABILITY_SHELL_EXECUTE)
    try:
        capability_registry.register(
            CAPABILITY_SHELL_EXECUTE,
            lambda _req: {"stdout": "", "stderr": "failed", "exit_code": 2, "timed_out": False},
        )
        result = await broker.execute(
            ActionRequest(
                task_id="TASK-EXEC-3",
                session_id="SESS-01",
                capability=CAPABILITY_SHELL_EXECUTE,
                arguments={"command": "false"},
                risk_tier=RiskTier.HIGH,
            )
        )
        assert result.status == ActionStatus.FAILED
        assert result.verified is False
        assert "non-zero" in (result.error or "")
    finally:
        if original_handler is not None:
            capability_registry.register(CAPABILITY_SHELL_EXECUTE, original_handler)
        else:
            capability_registry._handlers.pop(CAPABILITY_SHELL_EXECUTE, None)
