"""Unit tests for SagaCompensationPlan, SagaStep, and CompensationRegistry."""

from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.broker.saga import CompensationRegistry, SagaCompensationPlan
from jarvis.core.broker.types import CompensationFailedError


@pytest.mark.asyncio
async def test_saga_lifo_compensation_success() -> None:
    """Verify executed steps are rolled back in reverse chronological order (LIFO)."""
    plan = SagaCompensationPlan(task_id=uuid4())
    rollback_order: list[str] = []

    plan.add_step(
        logical_effect_id="eff_1",
        tool_id="fs.mkdir",
        forward_arguments={"dir": "data/"},
        compensating_tool_id="fs.rmdir",
        compensating_arguments={"dir": "data/"},
    )
    plan.add_step(
        logical_effect_id="eff_2",
        tool_id="fs.create_file",
        forward_arguments={"path": "data/app.log"},
        compensating_tool_id="fs.delete_file",
        compensating_arguments={"path": "data/app.log"},
    )

    async def mock_executor(tool_id: str, args: dict[str, Any]) -> None:
        rollback_order.append(tool_id)

    results = await plan.compensate_all(mock_executor)

    assert len(results) == 2
    # Step 2 rolled back before Step 1
    assert rollback_order == ["fs.delete_file", "fs.rmdir"]
    assert all(r.success for r in results)
    assert all(s.compensated for s in plan.steps)


@pytest.mark.asyncio
async def test_saga_compensation_failure_raises_error() -> None:
    """Verify failure during compensation raises CompensationFailedError."""
    plan = SagaCompensationPlan(task_id=uuid4())

    plan.add_step(
        logical_effect_id="eff_fail",
        tool_id="resource.create",
        forward_arguments={"id": "res_123"},
        compensating_tool_id="resource.delete",
        compensating_arguments={"id": "res_123"},
    )

    async def failing_executor(tool_id: str, args: dict[str, Any]) -> None:
        raise RuntimeError("Cloud provider 500 internal server error")

    with pytest.raises(CompensationFailedError) as exc_info:
        await plan.compensate_all(failing_executor)

    assert "Cloud provider 500" in str(exc_info.value)
    assert plan.steps[0].compensated is False
    assert plan.steps[0].compensation_error is not None


def test_compensation_registry() -> None:
    """Verify registry resolves dynamic compensating actions."""
    registry = CompensationRegistry()

    def git_commit_compensator(
        args: dict[str, Any], result: Any
    ) -> tuple[str, dict[str, Any]] | None:
        return "git.reset", {"commit_hash": "HEAD~1"}

    registry.register("git.commit", git_commit_compensator)

    resolved = registry.resolve("git.commit", {"message": "initial commit"}, {"sha": "abc1234"})
    assert resolved is not None
    tool, c_args = resolved
    assert tool == "git.reset"
    assert c_args["commit_hash"] == "HEAD~1"

    # Unregistered tool returns None
    assert registry.resolve("unknown.tool", {}, {}) is None
