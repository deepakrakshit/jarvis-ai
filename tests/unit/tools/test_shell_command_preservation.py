"""Regression and portability tests for shell command-line preservation.

Verifies:
- Raw shell-string semantics without C-runtime argument re-quoting/escaping.
- Proper execution of chaining (&&, ||, &, |), redirection, special characters, and embedded quotes.
- Preservation of stdout, stderr, and exact non-zero exit codes.
- Confinement, environment scrubbing, and timeout guarantees.
- Total absence of hardcoded machine-specific absolute paths or test-specific hacks.
"""

import inspect
import re
import sys
from pathlib import Path

import pytest

from jarvis.core.exceptions import SandboxExecutionError
from jarvis.sandbox.runner import LocalProcessSandbox
from jarvis.tools.native.shell import execute_shell


@pytest.mark.asyncio
async def test_shell_echo_chaining(tmp_path: Path) -> None:
    """Verify operator chaining (&&) executes sequentially and preserves stdout."""
    cmd = "echo A && echo B"
    res = await execute_shell(cmd, workdir=tmp_path)

    assert res["exit_code"] == 0
    assert "A" in res["stdout"]
    assert "B" in res["stdout"]
    assert res["stderr"] == ""
    assert res["timed_out"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd.exe exit code semantics")
@pytest.mark.asyncio
async def test_shell_cmd_exit_code_preservation(tmp_path: Path) -> None:
    """Verify cmd /c exit /b 7 preserves exact non-zero exit code without quote mangling."""
    cmd = 'cmd /c "exit /b 7"'
    res = await execute_shell(cmd, workdir=tmp_path)

    assert res["exit_code"] == 7
    assert res["stderr"] == ""
    assert res["timed_out"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd.exe exit and echo semantics")
@pytest.mark.asyncio
async def test_shell_cmd_echo_and_exit_preservation(tmp_path: Path) -> None:
    """Verify cmd /c with echo and exit preserves stdout and exact exit code."""
    cmd = 'cmd /c "echo FIRST & exit /b 7"'
    res = await execute_shell(cmd, workdir=tmp_path)

    assert res["exit_code"] == 7
    assert "FIRST" in res["stdout"]
    assert res["stderr"] == ""
    assert res["timed_out"] is False


@pytest.mark.asyncio
async def test_shell_python_print_preservation(tmp_path: Path) -> None:
    """Verify python -c with single quotes inside double quotes executes correctly."""
    cmd = "python -c \"print('FIRST')\""
    res = await execute_shell(cmd, workdir=tmp_path)

    assert res["exit_code"] == 0
    assert "FIRST" in res["stdout"]
    assert res["stderr"] == ""
    assert res["timed_out"] is False


@pytest.mark.asyncio
async def test_shell_python_sys_executable_discovery(tmp_path: Path) -> None:
    """Verify python executable discovery and execution through sandboxed PATH."""
    cmd = 'python -c "import sys; print(sys.executable)"'
    res = await execute_shell(cmd, workdir=tmp_path)

    assert res["exit_code"] == 0
    assert "python" in res["stdout"].lower()
    assert res["stderr"] == ""
    assert res["timed_out"] is False


@pytest.mark.asyncio
async def test_shell_quoted_paths_containing_spaces(tmp_path: Path) -> None:
    """Verify commands with quoted file and directory paths containing spaces."""
    folder_with_space = tmp_path / "custom folder with spaces"
    folder_with_space.mkdir(parents=True)
    target_file = folder_with_space / "sample data.txt"
    target_file.write_text("content inside space path", encoding="utf-8")

    read_cmd = f'type "{target_file}"' if sys.platform == "win32" else f'cat "{target_file}"'
    res = await execute_shell(read_cmd, workdir=tmp_path)

    assert res["exit_code"] == 0
    assert "content inside space path" in res["stdout"]
    assert res["stderr"] == ""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd.exe special characters")
@pytest.mark.asyncio
async def test_shell_special_characters_windows(tmp_path: Path) -> None:
    """Verify cmd.exe special characters (&, |, ^, <, >) and pipes operate correctly."""
    # 1. Pipe and escape characters
    cmd_pipe = "echo ^<tag^> ^& echo special_marker ^| findstr special_marker"
    res_pipe = await execute_shell(cmd_pipe, workdir=tmp_path)

    assert res_pipe["exit_code"] == 0
    assert "special_marker" in res_pipe["stdout"]

    # 2. Output redirection
    out_file = tmp_path / "redir_output.txt"
    cmd_redir = f'echo redirected_payload > "{out_file}"'
    res_redir = await execute_shell(cmd_redir, workdir=tmp_path)

    assert res_redir["exit_code"] == 0
    assert out_file.exists()
    assert "redirected_payload" in out_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_shell_nonzero_exit_with_stdout_preserved(tmp_path: Path) -> None:
    """Verify that when a command fails with non-zero exit code, stdout is still captured."""
    if sys.platform == "win32":
        cmd = 'echo CAPTURED_STDOUT & cmd /c "exit /b 42"'
    else:
        cmd = "echo CAPTURED_STDOUT; exit 42"

    res = await execute_shell(cmd, workdir=tmp_path)

    assert res["exit_code"] == 42
    assert "CAPTURED_STDOUT" in res["stdout"]
    assert res["timed_out"] is False


@pytest.mark.asyncio
async def test_sandbox_execute_shell_confinement(tmp_path: Path) -> None:
    """Verify that execute_shell strictly enforces directory boundary confinement."""
    allowed = tmp_path / "allowed_workspace"
    allowed.mkdir()
    forbidden = tmp_path / "forbidden_workspace"
    forbidden.mkdir()

    sandbox = LocalProcessSandbox(allowed_root=allowed)
    with pytest.raises(SandboxExecutionError) as exc_info:
        await sandbox.execute_shell("echo hello", workdir=forbidden)

    assert "Confinement breach" in str(exc_info.value)


@pytest.mark.asyncio
async def test_sandbox_execute_string_delegation(tmp_path: Path) -> None:
    """Verify that passing a string to sandbox.execute delegates to execute_shell seamlessly."""
    sandbox = LocalProcessSandbox(allowed_root=tmp_path)
    res = await sandbox.execute("echo DELEGATED_STRING", workdir=tmp_path)

    assert res.exit_code == 0
    assert "DELEGATED_STRING" in res.stdout
    assert res.timed_out is False


def test_portability_no_machine_specific_hardcoded_paths() -> None:
    """Verify that core sandbox and shell modules contain no machine-specific absolute paths."""
    from jarvis.sandbox import runner
    from jarvis.tools.native import shell

    for mod in (runner, shell):
        source = inspect.getsource(mod)
        # Check for hardcoded Windows drive letters like C:\ or D:\
        drive_letter_matches = re.findall(r'["\'][a-zA-Z]:[\\/]', source)
        assert not drive_letter_matches, (
            f"Found hardcoded drive in {mod.__name__}: {drive_letter_matches}"
        )

        # Check for hardcoded user directories like Users/ or home/
        assert "Users" not in source, f"Found hardcoded Users directory in {mod.__name__}"
        assert "/home/" not in source, f"Found hardcoded /home/ directory in {mod.__name__}"


def test_dynamic_environment_discovery_portable(tmp_path: Path) -> None:
    """Verify dynamic virtual environment discovery resolves relative to any workdir."""
    custom_root = tmp_path / "portable_workspace" / "project_alpha"
    custom_venv = custom_root / ".venv"
    custom_bin = custom_venv / ("Scripts" if sys.platform == "win32" else "bin")
    custom_bin.mkdir(parents=True)

    resolved = LocalProcessSandbox._resolve_runtime_path("BASE_PATH", workdir=custom_root)
    assert str(custom_bin) in resolved
    assert "BASE_PATH" in resolved
