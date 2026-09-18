"""Windows Win32 API wrapper for desktop attachment, DPI awareness, and window management."""

from __future__ import annotations

import ctypes
import platform
from contextlib import suppress
from ctypes import wintypes
from typing import Any

import psutil  # type: ignore[import-untyped]

from jarvis.computer.models import (
    DesktopState,
    DisplayMetrics,
    MonitorInfo,
    ProcessInfo,
    WindowInfo,
)
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

# Win32 Constants
SW_HIDE = 0
SW_NORMAL = 1
SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9
WM_CLOSE = 0x0010

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CXSCREEN = 0
SM_CYSCREEN = 1

DESKTOP_ALL = 0x01FF

# Protected system processes that must NEVER be terminated
PROTECTED_SYSTEM_PROCESSES: set[str] = {
    "system",
    "system idle process",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "winlogon.exe",
    "fontdrvhost.exe",
    "dwm.exe",
    "spoolsv.exe",
    "logonui.exe",
}


class WindowsAPI:
    """Encapsulates direct Win32 API interactions with Per-Monitor DPI awareness."""

    def __init__(self) -> None:
        self.is_windows = platform.system() == "Windows"
        self._dpi_initialized = False
        self._user32: Any = None
        self._shcore: Any = None
        self._kernel32: Any = None

        if self.is_windows:
            self._user32 = ctypes.windll.user32
            self._kernel32 = ctypes.windll.kernel32
            try:
                self._shcore = ctypes.windll.shcore
            except Exception:
                self._shcore = None
            self.init_dpi_awareness()

    def init_dpi_awareness(self) -> bool:
        """Set process to Per-Monitor v2 DPI awareness to avoid coordinate and scaling distortion."""
        if not self.is_windows or self._dpi_initialized:
            return True

        # Try Per-Monitor v2 context first (Windows 10 1703+)
        if hasattr(self._user32, "SetProcessDpiAwarenessContext"):
            try:
                # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
                res = self._user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
                if res:
                    self._dpi_initialized = True
                    return True
            except Exception:
                pass

        # Fallback to shcore SetProcessDpiAwareness (Windows 8.1+)
        if self._shcore and hasattr(self._shcore, "SetProcessDpiAwareness"):
            try:
                # PROCESS_PER_MONITOR_DPI_AWARE = 2
                res = self._shcore.SetProcessDpiAwareness(2)
                if res == 0:  # S_OK
                    self._dpi_initialized = True
                    return True
            except Exception:
                pass

        self._dpi_initialized = True
        return False

    def attach_to_default_desktop(self) -> bool:
        """Attach current calling thread to the interactive 'default' user desktop (WinSta0\\default)."""
        if not self.is_windows or not self._user32 or not self._kernel32:
            return False

        try:
            cur_dt = self._user32.GetThreadDesktop(self._kernel32.GetCurrentThreadId())
            if cur_dt:
                return True
            h_default = self._user32.OpenDesktopW("default", 0, False, DESKTOP_ALL)
            if h_default:
                res = self._user32.SetThreadDesktop(h_default)
                return bool(res)
        except Exception as exc:
            logger.debug("attach_desktop_error", error=str(exc))
        return False

    def detect_desktop_state(self) -> DesktopState:
        """Determine whether the current interactive session is normal, locked, or secure."""
        if not self.is_windows or not self._user32:
            return DesktopState.UNAVAILABLE

        self.attach_to_default_desktop()

        try:
            # Check if input desktop can be opened
            # DESKTOP_SWITCHDESKTOP = 0x0100
            h_input = self._user32.OpenInputDesktop(0, False, 0x0100)
            if not h_input:
                err = ctypes.GetLastError()
                # ERROR_ACCESS_DENIED = 5: typically UAC secure desktop or screen saver lock
                if err == 5:
                    return DesktopState.SECURE_DESKTOP
                return DesktopState.LOCKED_DESKTOP

            self._user32.CloseDesktop(h_input)

            # Check foreground window process name
            fg_hwnd = self._user32.GetForegroundWindow()
            if fg_hwnd:
                pid = wintypes.DWORD()
                self._user32.GetWindowThreadProcessId(fg_hwnd, ctypes.byref(pid))
                if pid.value:
                    try:
                        p = psutil.Process(pid.value)
                        p_name = p.name().lower()
                        if p_name in ("logonui.exe", "winlogon.exe"):
                            return DesktopState.LOCKED_DESKTOP
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

            return DesktopState.NORMAL_DESKTOP
        except Exception:
            return DesktopState.UNAVAILABLE

    def get_display_metrics(self) -> DisplayMetrics:
        """Retrieve authoritative physical screen metrics and monitor bounds."""
        if not self.is_windows or not self._user32:
            return DisplayMetrics(
                virtual_left=0,
                virtual_top=0,
                virtual_width=1920,
                virtual_height=1080,
                primary_width=1920,
                primary_height=1080,
                monitors=[
                    MonitorInfo(
                        monitor_index=1,
                        is_primary=True,
                        left=0,
                        top=0,
                        right=1920,
                        bottom=1080,
                        width=1920,
                        height=1080,
                    )
                ],
            )

        self.init_dpi_awareness()
        self.attach_to_default_desktop()

        p_w = self._user32.GetSystemMetrics(SM_CXSCREEN)
        p_h = self._user32.GetSystemMetrics(SM_CYSCREEN)
        v_l = self._user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        v_t = self._user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        v_w = self._user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        v_h = self._user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)

        monitors: list[MonitorInfo] = []

        # Monitor enum callback
        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )

        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32),
            ]

        def _mon_cb(h_mon: Any, hdc: Any, lprc: Any, lparam: Any) -> bool:
            mi = MONITORINFOEXW()
            mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if self._user32.GetMonitorInfoW(h_mon, ctypes.byref(mi)):
                r = mi.rcMonitor
                is_prim = bool(mi.dwFlags & 1)  # MONITORINFOF_PRIMARY = 1
                monitors.append(
                    MonitorInfo(
                        monitor_index=len(monitors) + 1,
                        is_primary=is_prim,
                        left=r.left,
                        top=r.top,
                        right=r.right,
                        bottom=r.bottom,
                        width=r.right - r.left,
                        height=r.bottom - r.top,
                    )
                )
            return True

        with suppress(Exception):
            self._user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_mon_cb), 0)

        if not monitors:
            monitors.append(
                MonitorInfo(
                    monitor_index=1,
                    is_primary=True,
                    left=0,
                    top=0,
                    right=p_w,
                    bottom=p_h,
                    width=p_w,
                    height=p_h,
                )
            )

        return DisplayMetrics(
            virtual_left=v_l,
            virtual_top=v_t,
            virtual_width=v_w if v_w > 0 else p_w,
            virtual_height=v_h if v_h > 0 else p_h,
            primary_width=p_w,
            primary_height=p_h,
            monitors=monitors,
        )

    def list_desktop_windows(
        self,
        include_invisible: bool = False,
        min_dimension: int = 10,
    ) -> list[WindowInfo]:
        """Enumerate top-level windows on the interactive default desktop."""
        if not self.is_windows or not self._user32:
            return []

        self.attach_to_default_desktop()

        windows: list[WindowInfo] = []
        visited_hwnds: set[int] = set()
        fg_hwnd = self._user32.GetForegroundWindow()

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        # Cache process names
        proc_names: dict[int, str] = {}

        def _enum_cb(hwnd: int, lparam: Any) -> bool:
            if not hwnd or hwnd in visited_hwnds:
                return True
            visited_hwnds.add(hwnd)

            is_vis = bool(self._user32.IsWindowVisible(hwnd))
            if not is_vis and not include_invisible:
                return True

            # Check title
            length = self._user32.GetWindowTextLengthW(hwnd)
            title = ""
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                self._user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value.strip()

            # Class name
            cls_buff = ctypes.create_unicode_buffer(256)
            self._user32.GetClassNameW(hwnd, cls_buff, 256)
            cls_name = cls_buff.value.strip()

            # Rect
            rect = wintypes.RECT()
            self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top

            if not include_invisible and (w < min_dimension or h < min_dimension):
                return True

            # PID
            pid = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_val = pid.value

            p_name = proc_names.get(pid_val)
            if not p_name and pid_val:
                try:
                    p = psutil.Process(pid_val)
                    p_name = p.name()
                    proc_names[pid_val] = p_name
                except Exception:
                    p_name = "unknown"
                    proc_names[pid_val] = p_name

            # Minimized / Maximized
            # IsIconic = minimized, IsZoomed = maximized
            is_min = bool(self._user32.IsIconic(hwnd))
            is_max = bool(self._user32.IsZoomed(hwnd))
            is_fg = hwnd == fg_hwnd

            windows.append(
                WindowInfo(
                    hwnd=hwnd,
                    title=title,
                    class_name=cls_name,
                    process_id=pid_val,
                    process_name=p_name or "unknown",
                    left=rect.left,
                    top=rect.top,
                    width=w,
                    height=h,
                    is_visible=is_vis,
                    is_foreground=is_fg,
                    is_minimized=is_min,
                    is_maximized=is_max,
                )
            )
            return True

        try:
            self._user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        except Exception as exc:
            logger.debug("enum_windows_failed", error=str(exc))

        try:
            h_desk = self._user32.OpenInputDesktop(0, False, DESKTOP_ALL)
            if not h_desk:
                h_desk = self._user32.OpenDesktopW("default", 0, False, DESKTOP_ALL)
            if h_desk:
                self._user32.EnumDesktopWindows(h_desk, WNDENUMPROC(_enum_cb), 0)
                self._user32.CloseDesktop(h_desk)
        except Exception as exc:
            logger.debug("enum_desktop_windows_failed", error=str(exc))

        return windows

    def find_window(
        self,
        query: str,
        process_name: str | None = None,
        exact_title: bool = False,
    ) -> WindowInfo | None:
        """Find the best matching window by title pattern or process association."""
        q_lower = query.strip().lower()
        windows = self.list_desktop_windows(include_invisible=False)

        # 1. Exact match on title
        for win in windows:
            if exact_title and win.title.lower() == q_lower:
                return win

        # 2. Substring match on title
        for win in windows:
            if q_lower in win.title.lower():
                return win

        # 3. Match on process name
        if process_name:
            pn_lower = process_name.strip().lower().replace(".exe", "")
            for win in windows:
                w_pn = win.process_name.lower().replace(".exe", "")
                if pn_lower in (w_pn, win.title.lower()):
                    return win

        # 4. Fallback: match query against process name
        for win in windows:
            if q_lower in win.process_name.lower():
                return win

        return None

    def get_foreground_window(self) -> int:
        """Retrieve HWND of the current foreground window on interactive desktop."""
        if not self.is_windows or not self._user32:
            return 0
        self.attach_to_default_desktop()
        return int(self._user32.GetForegroundWindow() or 0)

    def focus_window(self, hwnd: int) -> bool:
        """Bring window to foreground and activate it with thread attachment unlock."""
        if not self.is_windows or not self._user32:
            return False

        self.attach_to_default_desktop()

        try:
            fg_hwnd = self._user32.GetForegroundWindow()
            if fg_hwnd == hwnd:
                return True

            # If minimized, restore first
            if self._user32.IsIconic(hwnd):
                self._user32.ShowWindow(hwnd, SW_RESTORE)

            cur_tid = self._kernel32.GetCurrentThreadId() if self._kernel32 else 0
            fg_tid = 0
            if fg_hwnd:
                pid = wintypes.DWORD()
                fg_tid = self._user32.GetWindowThreadProcessId(fg_hwnd, ctypes.byref(pid))

            attached = False
            if fg_tid and cur_tid and fg_tid != cur_tid:
                attached = bool(self._user32.AttachThreadInput(cur_tid, fg_tid, True))

            # Simulate Alt key to bypass Windows LockSetForegroundWindow restrictions
            self._user32.keybd_event(0x12, 0, 0, 0)
            self._user32.BringWindowToTop(hwnd)
            res = self._user32.SetForegroundWindow(hwnd)
            self._user32.keybd_event(0x12, 0, 2, 0)

            if attached and cur_tid and fg_tid:
                self._user32.AttachThreadInput(cur_tid, fg_tid, False)

            return bool(res)
        except Exception as exc:
            logger.warning("focus_window_failed", hwnd=hwnd, error=str(exc))
            return False

    def set_window_state(self, hwnd: int, action: str) -> bool:
        """Apply window state transformation (minimize, maximize, restore, close)."""
        if not self.is_windows or not self._user32:
            return False

        self.attach_to_default_desktop()

        try:
            act = action.lower()
            if act == "minimize":
                return bool(self._user32.ShowWindow(hwnd, SW_MINIMIZE))
            if act == "maximize":
                return bool(self._user32.ShowWindow(hwnd, SW_MAXIMIZE))
            if act in ("restore", "normal"):
                return bool(self._user32.ShowWindow(hwnd, SW_RESTORE))
            if act == "close":
                return bool(self._user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))
            return False
        except Exception as exc:
            logger.warning("set_window_state_failed", hwnd=hwnd, action=action, error=str(exc))
            return False

    def list_processes(self, filter_name: str | None = None, limit: int = 100) -> list[ProcessInfo]:
        """Inspect host processes and correlate with visible top-level windows."""
        filter_lower = filter_name.strip().lower() if filter_name else None
        windows = self.list_desktop_windows(include_invisible=False)

        pid_to_titles: dict[int, list[str]] = {}
        for w in windows:
            if w.title:
                pid_to_titles.setdefault(w.process_id, []).append(w.title)

        results: list[ProcessInfo] = []

        for proc in psutil.process_iter(
            ["pid", "name", "exe", "status", "create_time", "memory_percent"]
        ):
            try:
                name = proc.info["name"] or ""
                p_pid = proc.info["pid"]
                if filter_lower and filter_lower not in name.lower():
                    continue

                titles = pid_to_titles.get(p_pid, [])
                results.append(
                    ProcessInfo(
                        pid=p_pid,
                        name=name,
                        exe_path=proc.info.get("exe"),
                        status=proc.info.get("status", "running"),
                        create_time=proc.info.get("create_time", 0.0),
                        memory_percent=round(proc.info.get("memory_percent", 0.0) or 0.0, 2),
                        has_visible_window=bool(titles),
                        window_titles=titles,
                    )
                )
                if len(results) >= limit:
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        return results

    def terminate_process(self, pid: int, force: bool = False) -> bool:
        """Terminate a host process by PID, enforcing protected system process blacklist."""
        if pid in (0, 4):
            raise PermissionError(
                f"Security Gate: Refusing to terminate protected system process (PID: {pid})."
            )
        try:
            proc = psutil.Process(pid)
            p_name = (proc.name() or "").lower()

            if p_name in PROTECTED_SYSTEM_PROCESSES:
                raise PermissionError(
                    f"Security Gate: Refusing to terminate protected system process '{p_name}' (PID: {pid})."
                )

            if force:
                proc.kill()
            else:
                proc.terminate()
            return True
        except PermissionError:
            raise
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            raise RuntimeError(f"Failed to terminate PID {pid}: {exc}") from exc
