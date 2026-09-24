"""Tests for Action Broker execution pipeline and registry."""

import pytest

from jarvis.actions.broker import ActionBroker
from jarvis.contracts.action import ActionRequest, ActionStatus, RiskTier
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
        assert result.verified is True
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
