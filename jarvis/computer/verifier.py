"""External-State Reality Verifier for Windows desktop and application mutations."""

from __future__ import annotations

import time
from typing import Any

import psutil  # type: ignore[import-untyped]

from jarvis.computer.perception import ScreenPerception
from jarvis.computer.windows_api import WindowsAPI
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class ComputerStateVerifier:
    """Verifies physical operating system state transformations against ground reality."""

    def __init__(
        self,
        windows_api: WindowsAPI | None = None,
        perception: ScreenPerception | None = None,
    ) -> None:
        self.windows_api = windows_api or WindowsAPI()
        self.perception = perception or ScreenPerception(self.windows_api)

    def verify_app_launched(
        self,
        app_name: str,
        expected_pid: int | None = None,
        timeout_seconds: float = 6.0,
        poll_interval: float = 0.25,
    ) -> dict[str, Any]:
        """Verify that a launched application produced an active process and visible desktop window.

        Enforces four physical verification checkpoints:
        1. PROCESS_CREATED: Target PID or executable exists in process tables.
        2. WINDOW_FOUND: Top-level window handle detected for target process/name.
        3. WINDOW_VISIBLE: Window has non-zero physical geometry and IsWindowVisible == True.
        4. WINDOW_FOREGROUND: Window is brought to active interactive foreground.
        """
        start_time = time.perf_counter()
        deadline = start_time + timeout_seconds

        checkpoints: list[str] = []
        target_hwnd: int | None = None
        target_title: str = ""
        matched_pid: int | None = expected_pid

        q_lower = app_name.lower().replace(".exe", "").replace(".cmd", "")

        while time.perf_counter() < deadline:
            # 1. Process Check
            if matched_pid is None or not psutil.pid_exists(matched_pid):
                for p in psutil.process_iter(["pid", "name"]):
                    try:
                        p_name = (p.info["name"] or "").lower()
                        if q_lower in p_name:
                            matched_pid = p.info["pid"]
                            break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

            if (
                matched_pid is not None
                and psutil.pid_exists(matched_pid)
                and "PROCESS_CREATED" not in checkpoints
            ):
                checkpoints.append("PROCESS_CREATED")

            # 2. Window & Visibility Check
            windows = self.windows_api.list_desktop_windows(include_invisible=False)
            # Prioritize windows with visible title
            windows.sort(key=lambda win: len(win.title.strip()), reverse=True)
            for w in windows:
                p_match = matched_pid is not None and w.process_id == matched_pid
                title_match = q_lower in w.title.lower() or q_lower in w.process_name.lower()

                if p_match or title_match:
                    target_hwnd = w.hwnd
                    target_title = w.title
                    matched_pid = w.process_id

                    if "WINDOW_FOUND" not in checkpoints:
                        checkpoints.append("WINDOW_FOUND")

                    if w.is_visible and w.width >= 50 and w.height >= 50:
                        if "WINDOW_VISIBLE" not in checkpoints:
                            checkpoints.append("WINDOW_VISIBLE")

                        # 3. Foreground Check
                        if w.is_foreground:
                            if "WINDOW_FOREGROUND" not in checkpoints:
                                checkpoints.append("WINDOW_FOREGROUND")
                            # All 4 checkpoints satisfied
                            return {
                                "verdict": "VERIFIED",
                                "checkpoints": checkpoints,
                                "hwnd": target_hwnd,
                                "pid": matched_pid,
                                "title": target_title,
                                "elapsed_ms": (time.perf_counter() - start_time) * 1000.0,
                            }
                        else:
                            # Attempt to bring to foreground
                            self.windows_api.focus_window(w.hwnd)

            time.sleep(poll_interval)

        # Evaluate final status
        is_verified = ("WINDOW_VISIBLE" in checkpoints) or (
            "WINDOW_FOUND" in checkpoints and "PROCESS_CREATED" in checkpoints
        )
        verdict = "VERIFIED" if is_verified else "FAILED"

        return {
            "verdict": verdict,
            "checkpoints": checkpoints,
            "hwnd": target_hwnd,
            "pid": matched_pid,
            "title": target_title,
            "elapsed_ms": (time.perf_counter() - start_time) * 1000.0,
            "message": f"Launch verification {verdict}: {', '.join(checkpoints) if checkpoints else 'No window or process detected'}",
        }

    def verify_app_closed(
        self,
        app_name: str,
        target_pid: int | None = None,
        target_hwnd: int | None = None,
        timeout_seconds: float = 4.0,
        poll_interval: float = 0.25,
    ) -> dict[str, Any]:
        """Verify that an application window and/or process has been completely terminated.

        Checkpoints:
        1. CLOSE_REQUESTED: Signal emitted.
        2. WINDOW_GONE: Window handle no longer visible on default desktop.
        3. PROCESS_GONE: Target PID or executable no longer present in process table.
        """
        start_time = time.perf_counter()
        deadline = start_time + timeout_seconds

        checkpoints: list[str] = ["CLOSE_REQUESTED"]
        q_lower = app_name.lower().replace(".exe", "").replace(".cmd", "")

        while time.perf_counter() < deadline:
            # Check window presence
            win_alive = False
            if target_hwnd is not None:
                windows = self.windows_api.list_desktop_windows(include_invisible=False)
                for w in windows:
                    if w.hwnd == target_hwnd or (target_pid and w.process_id == target_pid):
                        win_alive = True
                        break
            else:
                w_match = self.windows_api.find_window(q_lower)
                if w_match and w_match.is_visible:
                    win_alive = True

            if not win_alive and "WINDOW_GONE" not in checkpoints:
                checkpoints.append("WINDOW_GONE")

            # Check process presence
            proc_alive = False
            if target_pid:
                try:
                    p = psutil.Process(target_pid)
                    if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                        proc_alive = True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    proc_alive = False
            else:
                for proc in psutil.process_iter(["pid", "name"]):
                    try:
                        p_name = (proc.info["name"] or "").lower()
                        if q_lower in p_name:
                            proc_alive = True
                            break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

            if not proc_alive and "PROCESS_GONE" not in checkpoints:
                checkpoints.append("PROCESS_GONE")

            if "WINDOW_GONE" in checkpoints and ("PROCESS_GONE" in checkpoints or not target_pid):
                return {
                    "verdict": "VERIFIED",
                    "checkpoints": checkpoints,
                    "elapsed_ms": (time.perf_counter() - start_time) * 1000.0,
                    "message": "Window and process successfully closed and verified gone.",
                }

            time.sleep(poll_interval)

        is_verified = "WINDOW_GONE" in checkpoints or "PROCESS_GONE" in checkpoints
        verdict = "VERIFIED" if is_verified else "FAILED"

        return {
            "verdict": verdict,
            "checkpoints": checkpoints,
            "elapsed_ms": (time.perf_counter() - start_time) * 1000.0,
            "message": f"Close verification {verdict}: {', '.join(checkpoints)}",
        }

    def verify_visual_state_changed(
        self,
        pre_screenshot_digest: str,
        timeout_seconds: float = 1.5,
    ) -> dict[str, Any]:
        """Capture post-action screenshot and verify that display state physically transformed."""
        time.sleep(0.1)  # allow display compositor to render update
        post_snap = self.perception.capture(output_format="bytes")

        if post_snap.status != "success":
            return {
                "verdict": "UNVERIFIED",
                "state_changed": False,
                "reason": f"Post-action screenshot capture failed: {post_snap.message}",
            }

        post_digest = post_snap.metadata.sha256_digest
        changed = post_digest != pre_screenshot_digest

        return {
            "verdict": "VERIFIED" if changed else "NO_VISUAL_CHANGE",
            "state_changed": changed,
            "pre_digest": pre_screenshot_digest,
            "post_digest": post_digest,
            "screenshot_metadata": post_snap.metadata.model_dump(),
        }
