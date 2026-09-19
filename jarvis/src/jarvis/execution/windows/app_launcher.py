"""Asynchronous Application Launcher with Closed-Loop Readiness Verification.

Eliminates blocking shell hangs when launching GUI applications by decoupling
process invocation into a detached background operation, followed by a bounded
polling verification loop checking process existence and window visibility.
"""

import asyncio
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import psutil

from jarvis.execution.windows.app_registry import app_registry
from jarvis.execution.windows.window_manager import window_manager
from jarvis.telemetry import logger


@dataclass
class LaunchResult:
    """Structured result of an application launch attempt."""

    success: bool
    status: str  # "VERIFIED", "LAUNCHED_BACKGROUND", "FAILED", "ALREADY_RUNNING"
    app_name: str
    pid: Optional[int] = None
    hwnd: Optional[int] = None
    window_title: Optional[str] = None
    error: Optional[str] = None

    @property
    def process_id(self) -> Optional[int]:
        """Alias for pid."""
        return self.pid

    @property
    def ready(self) -> bool:
        """True if window is verified and ready."""
        return self.success and self.hwnd is not None

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        d = asdict(self)
        d["ready"] = self.ready
        return d


class AppLauncher:
    """Launches applications asynchronously and verifies window readiness."""

    def __init__(self) -> None:
        pass

    def launch(
        self,
        target: str,
        arguments: Optional[List[str]] = None,
        timeout_seconds: float = 6.0,
    ) -> LaunchResult:
        """Launch an application or URI scheme non-blockingly and verify readiness."""
        logger.info(f"Attempting to launch application target: '{target}'")

        # 1. Resolve application through dynamic registry
        app_info = app_registry.find_application(target)
        app_name = app_info.name if app_info else target

        # 2. Check if window already exists in foreground/background
        existing_win = window_manager.find_window(query=app_name, app_name=app_name)
        if existing_win and existing_win.is_visible:
            logger.info(
                f"Application '{app_name}' already running with window HWND {existing_win.hwnd}"
            )
            window_manager.focus_window(existing_win.hwnd)
            return LaunchResult(
                success=True,
                status="ALREADY_RUNNING",
                app_name=app_name,
                pid=existing_win.pid,
                hwnd=existing_win.hwnd,
                window_title=existing_win.title,
            )

        # 3. Determine launch execution method
        spawned_pid: Optional[int] = None

        try:
            if app_info and app_info.uri_scheme:
                logger.info(f"Launching via URI scheme: {app_info.uri_scheme}")
                os.startfile(app_info.uri_scheme)
            elif (
                app_info
                and app_info.executable_path
                and app_info.executable_path.lower().endswith(".lnk")
            ):
                logger.info(f"Launching via Start Menu shortcut: {app_info.executable_path}")
                os.startfile(app_info.executable_path)
            else:
                exe_path = (
                    app_info.executable_path if (app_info and app_info.executable_path) else target
                )
                cmd = [exe_path] + (arguments or [])

                # Spawn as a detached process without blocking the current event loop or shell
                flags = subprocess.CREATE_NEW_PROCESS_GROUP
                if hasattr(subprocess, "DETACHED_PROCESS"):
                    flags |= subprocess.DETACHED_PROCESS

                proc = subprocess.Popen(
                    cmd,
                    creationflags=flags,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    close_fds=True,
                )
                spawned_pid = proc.pid
                logger.info(f"Spawned detached process PID {spawned_pid} for '{app_name}'")
        except Exception as err:
            logger.error(f"Failed to spawn application '{target}': {err}")
            return LaunchResult(
                success=False,
                status="FAILED",
                app_name=app_name,
                error=str(err),
            )

        # 4. Closed-loop readiness verification
        start_time = time.time()
        poll_interval = 0.15

        while time.time() - start_time < timeout_seconds:
            time.sleep(poll_interval)

            # Check if matching window has appeared
            win = window_manager.find_window(query=app_name, app_name=app_name)
            if win is not None and win.is_visible:
                logger.info(
                    f"Verified launch of '{app_name}': HWND {win.hwnd}, Title '{win.title}', PID {win.pid}"
                )
                window_manager.focus_window(win.hwnd)
                return LaunchResult(
                    success=True,
                    status="VERIFIED",
                    app_name=app_name,
                    pid=win.pid,
                    hwnd=win.hwnd,
                    window_title=win.title,
                )

        # Bounded timeout reached: check one last time for window
        final_win = window_manager.find_window(query=app_name, app_name=app_name)
        if final_win is not None and final_win.is_visible:
            return LaunchResult(
                success=True,
                status="VERIFIED",
                app_name=app_name,
                pid=final_win.pid,
                hwnd=final_win.hwnd,
                window_title=final_win.title,
            )
        if spawned_pid is not None and psutil.pid_exists(spawned_pid):
            logger.info(
                f"Process {spawned_pid} running in background (no visible window created yet)"
            )
            return LaunchResult(
                success=True,
                status="LAUNCHED_BACKGROUND",
                app_name=app_name,
                pid=spawned_pid,
            )

        return LaunchResult(
            success=False,
            status="FAILED",
            app_name=app_name,
            error=f"Readiness verification timed out after {timeout_seconds}s",
        )

    async def launch_async(
        self,
        target: str,
        arguments: Optional[List[str]] = None,
        timeout_seconds: float = 6.0,
    ) -> LaunchResult:
        """Asynchronous wrapper around launch executing in default loop executor."""
        return await asyncio.to_thread(self.launch, target, arguments, timeout_seconds)


# Global singleton launcher
app_launcher = AppLauncher()
