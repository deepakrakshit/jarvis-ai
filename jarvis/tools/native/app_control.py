"""JARVIS Native Operating System Application Lifecycle Controller.

Compatibility wrapper delegating to the canonical WindowsComputerExecutor.
"""

from __future__ import annotations

import re
from typing import Any

from jarvis.computer import get_computer_executor
from jarvis.computer.windows_api import PROTECTED_SYSTEM_PROCESSES


def launch_application(
    app_name: str,
    args: list[str] | None = None,
) -> dict[str, Any]:
    """Launch an operating system application asynchronously with state verification."""
    if not app_name or not app_name.strip():
        raise ValueError("Application name cannot be empty.")

    if not re.match(r"^[a-zA-Z0-9_\-\. :\\/]+$", app_name):
        raise ValueError(f"Application name contains invalid characters: '{app_name}'.")

    executor = get_computer_executor()
    result = executor.launch_app(app_name=app_name.strip(), args=args, verify=True)
    if result.status.value != "EXECUTED":
        raise RuntimeError(f"Failed to launch application '{app_name}': {result.error}")

    return {
        "status": "success",
        "action": "launch",
        "app_name": app_name,
        "pid": result.details.get("pid"),
        "executable": result.details.get("executable"),
        "verification": result.verification_details,
        "message": f"Successfully launched and verified '{app_name}'.",
    }


def close_application(
    app_name: str,
    force: bool = False,
) -> dict[str, Any]:
    """Close or terminate running application processes by name or PID."""
    clean_name = app_name.strip()
    if not clean_name:
        raise ValueError("Application name cannot be empty.")

    lower_name = clean_name.lower()
    base_name = lower_name.replace(".exe", "")
    if (
        lower_name in PROTECTED_SYSTEM_PROCESSES
        or base_name in PROTECTED_SYSTEM_PROCESSES
        or clean_name in ("0", "4")
    ):
        raise PermissionError(f"Refusing to terminate protected system process '{clean_name}'.")

    if clean_name.isdigit() and int(clean_name) in (0, 4):
        raise PermissionError(f"Refusing to terminate protected system process PID {clean_name}.")

    executor = get_computer_executor()

    if clean_name.isdigit():
        pid = int(clean_name)
        result = executor.terminate_process(pid=pid, force=force)
        return {
            "status": "success" if result.status.value == "EXECUTED" else "error",
            "action": "close",
            "app_name": app_name,
            "terminated_pids": [pid] if result.status.value == "EXECUTED" else [],
            "count": 1 if result.status.value == "EXECUTED" else 0,
            "message": f"Terminated PID {pid}."
            if result.status.value == "EXECUTED"
            else str(result.error),
        }

    result = executor.close_window(query=clean_name)
    is_success = result.status.value == "EXECUTED" and result.verification_verdict == "VERIFIED"

    return {
        "status": "success" if is_success else "not_found",
        "action": "close",
        "app_name": app_name,
        "terminated_pids": [result.details["pid"]] if result.details.get("pid") else [],
        "count": 1 if is_success else 0,
        "verification": result.verification_details,
        "message": result.verification_details.get("message", f"Closed application '{app_name}'."),
    }


def list_running_applications(
    filter_name: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List running user-space applications with visible window correlation."""
    executor = get_computer_executor()
    result = executor.list_processes(filter_name=filter_name, limit=limit)
    procs = result.details.get("processes", [])

    return {
        "status": "success",
        "count": len(procs),
        "processes": procs,
    }
