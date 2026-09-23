"""Windows Process Management & Inspection for JARVIS.

Provides process enumeration, inspection, launching, and termination.
"""

import subprocess
from typing import Any, Dict, List, Optional

import psutil

from jarvis.telemetry import logger


def enumerate_processes(limit: int = 50, filter_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """List running processes with resource usage metrics."""
    processes: List[Dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent"]):
        try:
            info = proc.info
            name = info.get("name") or ""
            if filter_name and filter_name.lower() not in name.lower():
                continue
            processes.append(
                {
                    "pid": info.get("pid"),
                    "name": name,
                    "username": info.get("username"),
                    "cpu_percent": info.get("cpu_percent") or 0.0,
                    "memory_percent": info.get("memory_percent") or 0.0,
                }
            )
            if len(processes) >= limit:
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return processes


def inspect_process(pid: int) -> Optional[Dict[str, Any]]:
    """Retrieve detailed metadata for a specific process ID."""
    try:
        proc = psutil.Process(pid)
        return {
            "pid": proc.pid,
            "name": proc.name(),
            "exe": proc.exe(),
            "cwd": proc.cwd(),
            "status": proc.status(),
            "num_threads": proc.num_threads(),
            "memory_info": proc.memory_info()._asdict(),
            "cpu_percent": proc.cpu_percent(interval=0.1),
            "create_time": proc.create_time(),
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied) as err:
        logger.warning(f"Could not inspect PID {pid}: {err}")
        return None


def launch_process(executable: str, arguments: Optional[List[str]] = None) -> Dict[str, Any]:
    """Launch a new Windows application or process."""
    cmd = [executable] + (arguments or [])
    logger.info(f"Launching process: {cmd}")
    try:
        proc = subprocess.Popen(
            cmd,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {
            "pid": proc.pid,
            "executable": executable,
            "status": "launched",
        }
    except Exception as err:
        logger.error(f"Failed to launch process {executable}: {err}")
        raise RuntimeError(f"Failed to launch process {executable}: {err}") from err


def terminate_process(pid: int, force: bool = False) -> Dict[str, Any]:
    """Terminate a running process by PID."""
    logger.warning(f"Terminating PID {pid} (force={force})")
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        if force:
            proc.kill()
        else:
            proc.terminate()
        proc.wait(timeout=5.0)
        return {
            "pid": pid,
            "name": name,
            "status": "terminated",
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied) as err:
        logger.error(f"Failed to terminate PID {pid}: {err}")
        raise RuntimeError(f"Failed to terminate PID {pid}: {err}") from err
