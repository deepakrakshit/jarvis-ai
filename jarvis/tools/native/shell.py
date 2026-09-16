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

    import sys

    # Route through the host's standard system shell to properly support
    # shell operators (&&, ||, |), builtins (dir, echo), and chained commands.
    # On Windows, normalize common Unix commands (like rm / rm -f) for portability.
    cmd_to_run = command.strip()
    if sys.platform == "win32":
        if cmd_to_run.startswith("rm -rf "):
            target = cmd_to_run[7:].strip()
            cmd_to_run = f"rmdir /s /q {target}"
        elif cmd_to_run.startswith("rm -r "):
            target = cmd_to_run[6:].strip()
            cmd_to_run = f"rmdir /s /q {target}"
        elif cmd_to_run.startswith("rm -f "):
            target = cmd_to_run[6:].strip()
            cmd_to_run = f"del /f /q {target}"
        elif cmd_to_run.startswith("rm "):
            target = cmd_to_run[3:].strip()
            cmd_to_run = f"del /f /q {target}"

    res = await sandbox.execute_shell(
        command=cmd_to_run,
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
