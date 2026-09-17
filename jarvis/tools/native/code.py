"""JARVIS Native Governed Python Script and Test Execution Tool.

Executes workspace-local Python scripts and pytest test suites within
the LocalProcessSandbox using direct subprocess execution (shell=False),
credential-scrubbed environments, workspace boundary confinement, and strict timeouts.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from jarvis.core.exceptions import SandboxExecutionError, SandboxTimeoutError
from jarvis.core.logging import get_logger
from jarvis.sandbox.runner import LocalProcessSandbox

logger = get_logger(__name__)

# Disallowed shell metacharacters in test arguments to prevent parameter injection
FORBIDDEN_ARG_CHARS = re.compile(r"[;&|><$`\n\r]")


def _validate_test_args(test_args: list[str]) -> list[str]:
    """Validate that extra arguments do not contain shell injection metacharacters."""
    sanitized: list[str] = []
    for arg in test_args:
        clean = str(arg).strip()
        if not clean:
            continue
        if FORBIDDEN_ARG_CHARS.search(clean):
            raise ValueError(f"Invalid test argument '{clean}': contains forbidden metacharacters.")
        sanitized.append(clean)
    return sanitized


async def run_python_test(
    file_path: str,
    mode: str = "script",
    test_args: list[str] | None = None,
    timeout_seconds: float = 30.0,
    workspace_root: Path | str | None = None,
) -> dict[str, Any]:
    """Execute a Python file or pytest suite within workspace boundaries.

    Args:
        file_path: Relative or absolute path to the Python file within workspace.
        mode: Execution mode, either 'script' (direct python run) or 'pytest' (pytest test runner).
        test_args: Optional additional flags passed to pytest or the script (validated against injection).
        timeout_seconds: Maximum allowed execution time (bounded between 1.0 and 60.0 seconds).
        workspace_root: Approved workspace directory root.

    Returns:
        Structured execution result containing exit_code, stdout, stderr, duration, and timeout indicators.
    """
    ws_root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()

    # 1. Path resolution and boundary confinement check
    target = Path(file_path)
    if ".." in str(file_path).replace("\\", "/").split("/"):
        raise PermissionError(
            f"Path traversal rejected: '{file_path}' contains parent directory references."
        )

    resolved_target = target.resolve() if target.is_absolute() else (ws_root / target).resolve()

    if not resolved_target.is_relative_to(ws_root):
        raise PermissionError(
            f"Confinement breach: Target file '{resolved_target}' is outside workspace root '{ws_root}'."
        )

    if not resolved_target.is_file():
        return {
            "status": "error",
            "file_path": str(file_path),
            "error": f"Target file '{file_path}' does not exist on disk.",
            "exit_code": 1,
            "stdout": "",
            "stderr": f"FileNotFoundError: {file_path}",
            "duration_seconds": 0.0,
            "timed_out": False,
            "truncated": False,
        }

    if resolved_target.suffix.lower() != ".py":
        return {
            "status": "error",
            "file_path": str(file_path),
            "error": f"Target file '{file_path}' must be a Python (.py) file.",
            "exit_code": 1,
            "stdout": "",
            "stderr": "ValueError: Only .py files can be executed by run_python_test.",
            "duration_seconds": 0.0,
            "timed_out": False,
            "truncated": False,
        }

    # 2. Validate extra arguments if provided
    clean_extra_args: list[str] = []
    if test_args:
        try:
            clean_extra_args = _validate_test_args(test_args)
        except ValueError as val_err:
            return {
                "status": "error",
                "file_path": str(file_path),
                "error": str(val_err),
                "exit_code": 1,
                "stdout": "",
                "stderr": str(val_err),
                "duration_seconds": 0.0,
                "timed_out": False,
                "truncated": False,
            }

    # 3. Formulate direct argv execution command (shell=False) using current Python executable
    python_bin = sys.executable
    clean_mode = str(mode).strip().lower()

    if clean_mode == "pytest":
        command = [python_bin, "-m", "pytest", str(resolved_target), *clean_extra_args]
    else:
        command = [python_bin, str(resolved_target), *clean_extra_args]

    # 4. Enforce bounded execution timeout
    bounded_timeout = min(max(float(timeout_seconds), 1.0), 60.0)

    # 5. Execute via LocalProcessSandbox (shell=False, scrubbed env, workspace cwd)
    sandbox = LocalProcessSandbox(allowed_root=ws_root)
    max_output_chars = 20_000

    try:
        res = await sandbox.execute(
            command=command,
            workdir=ws_root,
            timeout_seconds=bounded_timeout,
        )

        stdout_text = res.stdout
        stderr_text = res.stderr
        truncated = len(stdout_text) > max_output_chars or len(stderr_text) > max_output_chars

        return {
            "status": "success" if res.exit_code == 0 else "failed",
            "file_path": str(resolved_target.relative_to(ws_root)),
            "mode": clean_mode,
            "exit_code": res.exit_code,
            "stdout": stdout_text[:max_output_chars],
            "stderr": stderr_text[:max_output_chars],
            "duration_seconds": res.duration_seconds,
            "timed_out": res.timed_out,
            "truncated": truncated,
        }

    except SandboxTimeoutError:
        logger.warning(
            "python_test_execution_timed_out",
            file_path=str(resolved_target),
            timeout=bounded_timeout,
        )
        return {
            "status": "timed_out",
            "file_path": str(resolved_target.relative_to(ws_root)),
            "mode": clean_mode,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Execution timed out after {bounded_timeout} seconds.",
            "duration_seconds": bounded_timeout,
            "timed_out": True,
            "truncated": False,
        }

    except SandboxExecutionError as exc:
        logger.error("python_test_execution_failed", file_path=str(resolved_target), error=str(exc))
        return {
            "status": "error",
            "file_path": str(resolved_target.relative_to(ws_root)),
            "mode": clean_mode,
            "exit_code": 1,
            "stdout": "",
            "stderr": str(exc),
            "duration_seconds": 0.0,
            "timed_out": False,
            "truncated": False,
        }
