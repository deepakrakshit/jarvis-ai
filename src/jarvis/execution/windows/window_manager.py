"""Windows Window Manager for Deterministic In-App Control.

Provides HWND-based window enumeration, state inspection, multi-instance
disambiguation, and window lifecycle controls (focus, minimize, maximize,
restore, move, resize, close).
"""

import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import psutil

from jarvis.execution.windows.winsta import ensure_interactive_desktop_attached
from jarvis.telemetry import logger

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_MAXIMIZE = 3
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
SW_MINIMIZE = 6
SW_RESTORE = 9

WM_CLOSE = 0x0010

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


@dataclass
class WindowInfo:
    """Authoritative metadata representation of a desktop application window."""

    hwnd: int
    title: str
    pid: int
    process_name: str
    class_name: str
    is_visible: bool
    is_minimized: bool
    is_maximized: bool
    is_active: bool
    left: int
    top: int
    width: int
    height: int

    @property
    def bounds(self) -> Dict[str, int]:
        """Return window bounding rectangle."""
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}

    def to_dict(self) -> Dict[str, Any]:
        """Convert window metadata to a serializable dictionary."""
        d = asdict(self)
        d["bounds"] = self.bounds
        return d


class WindowManager:
    """Manages Windows top-level application windows using native Win32 APIs."""

    def __init__(self) -> None:
        self._ensure_desktop()

    def _ensure_desktop(self) -> None:
        """Ensure thread is attached to the interactive desktop."""
        ensure_interactive_desktop_attached()

    def get_active_window(self) -> Optional[WindowInfo]:
        """Return metadata for the currently active foreground window."""
        self._ensure_desktop()
        hwnd = user32.GetForegroundWindow()
        if not hwnd or hwnd == 0:
            return None
        return self._build_window_info(hwnd)

    def list_windows(self, visible_only: bool = True) -> List[WindowInfo]:
        """Enumerate all desktop application windows."""
        self._ensure_desktop()
        results: List[WindowInfo] = []

        def enum_cb(hwnd: int, lparam: int) -> bool:
            if not user32.IsWindow(hwnd):
                return True

            is_visible = bool(user32.IsWindowVisible(hwnd))
            if visible_only and not is_visible:
                return True

            info = self._build_window_info(hwnd)
            if info is not None:
                if visible_only:
                    # Ignore tiny hidden helper windows or empty titles
                    if info.title and info.width > 20 and info.height > 20:
                        results.append(info)
                else:
                    results.append(info)
            return True

        cb = WNDENUMPROC(enum_cb)
        user32.EnumWindows(cb, 0)
        return results

    def find_window(
        self,
        query: str,
        app_name: Optional[str] = None,
        prefer_active: bool = True,
    ) -> Optional[WindowInfo]:
        """Find the best matching window handle by title, process name, or query."""
        windows = self.list_windows(visible_only=True)
        if not windows:
            return None

        q_lower = query.strip().lower()
        app_lower = app_name.strip().lower() if app_name else None

        candidates: List[tuple[float, WindowInfo]] = []

        for win in windows:
            score = 0.0
            title_lower = win.title.lower()
            proc_lower = win.process_name.lower()

            # Exact title match
            if q_lower == title_lower:
                score += 1.0
            elif q_lower in title_lower:
                score += 0.75

            # Process match
            if app_lower:
                if app_lower == proc_lower or f"{app_lower}.exe" == proc_lower:
                    score += 0.6
                elif app_lower in proc_lower:
                    score += 0.4
            elif q_lower in proc_lower or f"{q_lower}.exe" == proc_lower:
                score += 0.5

            if score <= 0.0:
                continue

            if win.is_active and prefer_active:
                score += 0.1

            # Strongly prefer interactive ApplicationFrameWindow over cloaked UWP CoreWindow
            if win.class_name == "ApplicationFrameWindow":
                score += 0.6
            elif win.class_name == "Windows.UI.Core.CoreWindow":
                score -= 0.5

            # Slightly penalize minimized windows if non-minimized exists
            if win.is_minimized:
                score -= 0.1

            if score > 0.3:
                candidates.append((score, win))

        if not candidates:
            return None

        # Sort by score descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def find_window_by_hwnd(self, hwnd: int) -> Optional[WindowInfo]:
        """Find window information by exact HWND."""
        self._ensure_desktop()
        if not user32.IsWindow(hwnd):
            return None
        return self._build_window_info(hwnd)

    def focus_window(self, hwnd: int) -> bool:
        """Bring target window to the foreground and activate it."""
        self._ensure_desktop()
        if not user32.IsWindow(hwnd):
            logger.warning(f"Invalid HWND {hwnd} passed to focus_window")
            return False

        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)

            # Use AttachThreadInput to reliably steal/bring focus if Windows restricts it
            current_thread = kernel32.GetCurrentThreadId()
            fg_window = user32.GetForegroundWindow()
            fg_thread = user32.GetWindowThreadProcessId(fg_window, None) if fg_window else 0

            if fg_thread and fg_thread != current_thread:
                user32.AttachThreadInput(current_thread, fg_thread, True)
                user32.SetForegroundWindow(hwnd)
                user32.SetFocus(hwnd)
                user32.AttachThreadInput(current_thread, fg_thread, False)
            else:
                user32.SetForegroundWindow(hwnd)
                user32.SetFocus(hwnd)

            logger.info(f"Brought window HWND {hwnd} to foreground")
            return True
        except Exception as err:
            logger.warning(f"Failed to focus window HWND {hwnd}: {err}")
            return False

    def minimize_window(self, hwnd: int) -> bool:
        """Minimize the target window."""
        self._ensure_desktop()
        if not user32.IsWindow(hwnd):
            return False
        return bool(user32.ShowWindow(hwnd, SW_MINIMIZE))

    def maximize_window(self, hwnd: int) -> bool:
        """Maximize the target window."""
        self._ensure_desktop()
        if not user32.IsWindow(hwnd):
            return False
        return bool(user32.ShowWindow(hwnd, SW_MAXIMIZE))

    def restore_window(self, hwnd: int) -> bool:
        """Restore the target window from minimized or maximized state."""
        self._ensure_desktop()
        if not user32.IsWindow(hwnd):
            return False
        return bool(user32.ShowWindow(hwnd, SW_RESTORE))

    def close_window(self, hwnd: int) -> bool:
        """Close the target window cleanly by posting WM_CLOSE."""
        self._ensure_desktop()
        if not user32.IsWindow(hwnd):
            return False
        logger.info(f"Closing window HWND {hwnd}")
        return bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))

    def move_resize_window(self, hwnd: int, x: int, y: int, width: int, height: int) -> bool:
        """Move and resize the window to specified screen coordinates."""
        self._ensure_desktop()
        if not user32.IsWindow(hwnd):
            return False
        return bool(user32.MoveWindow(hwnd, x, y, width, height, True))

    def _build_window_info(self, hwnd: int) -> Optional[WindowInfo]:
        """Construct a WindowInfo object from an HWND handle."""
        try:
            length = user32.GetWindowTextLengthW(hwnd)
            title = ""
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()

            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            class_name = class_buf.value.strip()

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

            process_name = "unknown"
            if pid.value > 0:
                try:
                    process_name = psutil.Process(pid.value).name()
                except Exception:
                    pass

            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            width = max(0, rect.right - rect.left)
            height = max(0, rect.bottom - rect.top)

            is_visible = bool(user32.IsWindowVisible(hwnd))
            is_minimized = bool(user32.IsIconic(hwnd))
            is_maximized = bool(user32.IsZoomed(hwnd))
            active_hwnd = user32.GetForegroundWindow()
            is_active = hwnd == active_hwnd

            return WindowInfo(
                hwnd=hwnd,
                title=title,
                pid=pid.value,
                process_name=process_name,
                class_name=class_name,
                is_visible=is_visible,
                is_minimized=is_minimized,
                is_maximized=is_maximized,
                is_active=is_active,
                left=rect.left,
                top=rect.top,
                width=width,
                height=height,
            )
        except Exception as err:
            logger.debug(f"Error inspecting HWND {hwnd}: {err}")
            return None


# Global singleton instance
window_manager = WindowManager()
