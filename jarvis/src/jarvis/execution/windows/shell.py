"""Controlled PowerShell and CMD Execution for Windows Host.

Enforces execution timeouts, output streaming, exit code capture, and observation.
"""

import asyncio
import os
from typing import Any, Dict, Optional

from jarvis.telemetry import logger


async def run_shell_command(
    command: str,
    shell: str = "powershell",
    timeout_seconds: float = 60.0,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a controlled shell command asynchronously and return results."""
    logger.info(f"Executing controlled {shell} command: {command[:80]}...")

    if shell.lower() in ("powershell", "pwsh"):
        cmd_args = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
    else:
        cmd_args = ["cmd.exe", "/c", command]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or os.getcwd(),
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )

        stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()
        exit_code = process.returncode or 0

        return {
            "stdout": stdout_str,
            "stderr": stderr_str,
            "exit_code": exit_code,
            "timed_out": False,
        }

    except asyncio.TimeoutError:
        logger.warning(f"Shell command timed out after {timeout_seconds}s: {command[:80]}")
        try:
            process.kill()
        except Exception:
            pass
        return {
            "stdout": "",
            "stderr": f"Execution timed out after {timeout_seconds} seconds",
            "exit_code": -1,
            "timed_out": True,
        }
    except Exception as err:
        logger.error(f"Shell execution failed: {err}")
        return {
            "stdout": "",
            "stderr": str(err),
            "exit_code": -1,
            "timed_out": False,
        }
