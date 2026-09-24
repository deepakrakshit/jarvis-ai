"""Tests for Windows Node Native OS Execution Capabilities."""

from pathlib import Path

import pytest

from jarvis.actions.broker import ActionBroker
from jarvis.contracts.action import ActionRequest, ActionStatus, ExecutionTarget, RiskTier
from jarvis.execution.windows.filesystem import list_directory, read_file, write_file
from jarvis.execution.windows.host import windows_node
from jarvis.execution.windows.process import enumerate_processes, inspect_process
from jarvis.execution.windows.shell import run_shell_command
from jarvis.execution.windows.system import get_system_info, get_system_volume
from jarvis.policy.engine import PolicyEngine
from jarvis.policy.firewall import (
    CAPABILITY_FILESYSTEM_READ,
    CAPABILITY_FILESYSTEM_WRITE,
    CAPABILITY_SYSTEM_INFO,
)


@pytest.fixture(autouse=True)
def setup_windows_capabilities() -> None:
    """Ensure Windows node capabilities are registered before test runs."""
    windows_node.register_capabilities(force=True)


@pytest.mark.asyncio
async def test_powershell_execution() -> None:
    """Verify controlled PowerShell execution."""
    result = await run_shell_command("Write-Output 'JARVIS Windows Execution Online'")
    assert result["exit_code"] == 0
    assert "JARVIS Windows Execution Online" in result["stdout"]
    assert result["timed_out"] is False


def test_filesystem_operations(temp_dir: Path) -> None:
    """Verify sandboxed write, read, and list operations."""
    test_file = temp_dir / "jarvis_test.txt"
    write_res = write_file(str(test_file), "JARVIS OS State 100%")
    assert write_res["status"] == "written"
    assert test_file.exists()

    content = read_file(str(test_file))
    assert content == "JARVIS OS State 100%"

    entries = list_directory(str(temp_dir))
    assert any(e["name"] == "jarvis_test.txt" for e in entries)


def test_process_inspection() -> None:
    """Verify process enumeration and current process inspection."""
    import os

    procs = enumerate_processes(limit=10)
    assert len(procs) > 0

    current_pid = os.getpid()
    info = inspect_process(current_pid)
    assert info is not None
    assert info["pid"] == current_pid
    assert "python" in info["name"].lower()


def test_system_info_and_volume() -> None:
    """Verify hardware telemetry and audio volume queries."""
    sys_info = get_system_info()
    assert sys_info["os"] == "Windows"
    assert sys_info["cpu_count"] > 0
    assert sys_info["memory_total_gb"] > 0

    vol = get_system_volume()
    assert "volume_percent" in vol
    assert 0 <= vol["volume_percent"] <= 100


@pytest.mark.asyncio
async def test_action_broker_with_windows_node(temp_dir: Path) -> None:
    """Verify ActionBroker executing Windows capabilities end-to-end."""
    broker = ActionBroker(policy=PolicyEngine(workspace_dir=temp_dir))
    target_file = temp_dir / "broker_win_test.txt"

    # Action 1: Write file
    write_req = ActionRequest(
        task_id="TASK-WIN-1",
        session_id="SESS-01",
        capability=CAPABILITY_FILESYSTEM_WRITE,
        arguments={"path": str(target_file), "content": "ActionBroker integration verified"},
        target=ExecutionTarget.WINDOWS_NODE,
    )
    res1 = await broker.execute(write_req)
    assert res1.status == ActionStatus.SUCCEEDED
    assert res1.verified is True

    # Action 2: Read file
    read_req = ActionRequest(
        task_id="TASK-WIN-1",
        session_id="SESS-01",
        capability=CAPABILITY_FILESYSTEM_READ,
        arguments={"path": str(target_file)},
        target=ExecutionTarget.WINDOWS_NODE,
        risk_tier=RiskTier.READ_ONLY,
    )
    res2 = await broker.execute(read_req)
    assert res2.status == ActionStatus.SUCCEEDED
    assert res2.output == "ActionBroker integration verified"

    # Action 3: System Info
    info_req = ActionRequest(
        task_id="TASK-WIN-1",
        session_id="SESS-01",
        capability=CAPABILITY_SYSTEM_INFO,
        arguments={},
        target=ExecutionTarget.WINDOWS_NODE,
        risk_tier=RiskTier.READ_ONLY,
    )
    res3 = await broker.execute(info_req)
    assert res3.status == ActionStatus.SUCCEEDED
    assert res3.output["os"] == "Windows"
