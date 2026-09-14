"""Tests for JARVIS Local Process Sandbox."""

import sys
from pathlib import Path

import pytest

from jarvis.core.exceptions import SandboxExecutionError, SandboxTimeoutError
from jarvis.sandbox.runner import LocalProcessSandbox


@pytest.mark.asyncio
async def test_sandbox_successful_execution(tmp_path: Path) -> None:
    """Verify standard command execution within sandbox."""
    sandbox = LocalProcessSandbox(allowed_root=tmp_path)
    result = await sandbox.execute(
        command=[sys.executable, "-c", "print('hello from sandbox')"],
        workdir=tmp_path,
        timeout_seconds=5.0,
    )
    assert result.exit_code == 0
    assert "hello from sandbox" in result.stdout
    assert result.timed_out is False
    assert result.duration_seconds > 0


@pytest.mark.asyncio
async def test_sandbox_confinement_violation(tmp_path: Path) -> None:
    """Verify that workdirs outside allowed_root are rejected."""
    allowed = tmp_path / "sandbox_home"
    allowed.mkdir()
    outside = tmp_path / "forbidden_home"
    outside.mkdir()

    sandbox = LocalProcessSandbox(allowed_root=allowed)
    with pytest.raises(SandboxExecutionError) as exc_info:
        await sandbox.execute(
            command=[sys.executable, "-c", "print('illegal')"],
            workdir=outside,
        )
    assert "Confinement breach" in str(exc_info.value)


@pytest.mark.asyncio
async def test_sandbox_environment_scrubbing(tmp_path: Path) -> None:
    """Verify that attempting to inject forbidden credentials raises SandboxExecutionError."""
    sandbox = LocalProcessSandbox(allowed_root=tmp_path)
    with pytest.raises(SandboxExecutionError) as exc_info:
        await sandbox.execute(
            command=[sys.executable, "-c", "print('test')"],
            workdir=tmp_path,
            env={"GEMINI_API_KEY": "leaked-secret-123"},
        )
    assert "Attempted to inject sensitive credential key" in str(exc_info.value)


@pytest.mark.asyncio
async def test_sandbox_timeout_enforcement(tmp_path: Path) -> None:
    """Verify that commands exceeding timeout are killed."""
    sandbox = LocalProcessSandbox(allowed_root=tmp_path)
    with pytest.raises(SandboxTimeoutError) as exc_info:
        await sandbox.execute(
            command=[sys.executable, "-c", "import time; time.sleep(5)"],
            workdir=tmp_path,
            timeout_seconds=0.5,
        )
    assert "timed out" in str(exc_info.value)
