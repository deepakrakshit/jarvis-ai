"""JARVIS Native System Diagnostics Tool.

Provides real-time host CPU, memory, disk, and operating system metrics (ARCHITECTURE.md Layer 10).
"""

import platform
import time
from typing import Any

import psutil  # type: ignore[import-untyped]


def get_system_stats() -> dict[str, Any]:
    """Retrieve host operating system, CPU, memory, and disk statistics."""
    cpu_percent = psutil.cpu_percent(interval=None)
    cpu_count = psutil.cpu_count(logical=True) or 1
    mem = psutil.virtual_memory()

    try:
        disk = psutil.disk_usage("/")
        disk_percent = disk.percent
        disk_free_gb = round(disk.free / (1024**3), 2)
    except Exception:
        disk_percent = 0.0
        disk_free_gb = 0.0

    boot_time = psutil.boot_time()
    uptime_seconds = int(time.time() - boot_time)

    return {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "architecture": platform.machine(),
        "cpu_percent": cpu_percent,
        "cpu_count": cpu_count,
        "memory_total_gb": round(mem.total / (1024**3), 2),
        "memory_used_gb": round(mem.used / (1024**3), 2),
        "memory_percent": mem.percent,
        "disk_percent": disk_percent,
        "disk_free_gb": disk_free_gb,
        "uptime_seconds": uptime_seconds,
    }
