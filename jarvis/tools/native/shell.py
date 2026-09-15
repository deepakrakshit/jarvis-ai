"""JARVIS Native Shell Execution Tool.

Executes shell commands strictly within the isolated LocalProcessSandbox.
"""

from pathlib import Path
from typing import Any

from jarvis.sandbox.runner import LocalProcessSandbox


async def execute_shell(
    command: str,
    workdir: Path | str | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Execute a shell command inside the local process sandbox."""
    resolved_dir = Path(workdir).resolve() if workdir else Path.cwd().resolve()
    sandbox = LocalProcessSandbox()

    # Normalize command into list of tokens
    import shlex

    try:
        cmd_tokens = shlex.split(command, posix=False)
    except Exception:
        cmd_tokens = command.split()

    res = await sandbox.execute(
        command=cmd_tokens,
        workdir=resolved_dir,
        timeout_seconds=timeout_seconds,
    )

    return {
        "command": command,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "exit_code": res.exit_code,
        "duration_seconds": res.duration_seconds,
        "timed_out": res.timed_out,
    }
