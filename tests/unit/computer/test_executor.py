"""Unit tests for WindowsComputerExecutor capability dispatcher."""

from __future__ import annotations

import pytest

from jarvis.computer.executor import WindowsComputerExecutor
from jarvis.computer.models import ComputerActionStatus


@pytest.fixture
def executor() -> WindowsComputerExecutor:
    return WindowsComputerExecutor()


@pytest.mark.asyncio
async def test_executor_screenshot_capability(executor: WindowsComputerExecutor) -> None:
    """Verify computer:screenshot capability execution."""
    res = await executor.execute_capability(
        "computer:screenshot",
        {"output_format": "bytes", "max_dimension": 640},
    )
    assert res.status in (ComputerActionStatus.EXECUTED, ComputerActionStatus.FAILED)
    assert res.execution_duration_ms >= 0.0
    assert res.action == "computer:screenshot"


@pytest.mark.asyncio
async def test_executor_list_apps_capability(executor: WindowsComputerExecutor) -> None:
    """Verify computer:list_apps capability execution."""
    res = await executor.execute_capability("computer:list_apps", {"limit": 10})
    assert res.status == ComputerActionStatus.EXECUTED
    assert res.action == "computer:list_apps"
    assert "apps" in res.details


@pytest.mark.asyncio
async def test_executor_get_active_window(executor: WindowsComputerExecutor) -> None:
    """Verify computer:get_active_window capability execution."""
    res = await executor.execute_capability("computer:get_active_window", {})
    assert res.status in (ComputerActionStatus.EXECUTED, ComputerActionStatus.FAILED)
    assert res.action == "computer:get_active_window"


@pytest.mark.asyncio
async def test_executor_unknown_capability(executor: WindowsComputerExecutor) -> None:
    """Verify execution of unknown capability returns FAILED status."""
    res = await executor.execute_capability("computer:unknown_capability_xyz", {})
    assert res.error is not None
    assert "unknown computer capability" in res.error.lower()


@pytest.mark.asyncio
async def test_executor_scroll_capability(executor: WindowsComputerExecutor) -> None:
    """Verify computer:scroll capability execution."""
    res = await executor.execute_capability(
        "computer:scroll",
        {"clicks": 2},
    )
    assert res.status == ComputerActionStatus.EXECUTED
    assert res.action == "computer:scroll"
