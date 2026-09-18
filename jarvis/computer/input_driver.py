"""DPI-aware and multi-monitor safe input injection driver for Windows desktop."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from typing import Any

from jarvis.computer.models import DisplayMetrics, MouseButton
from jarvis.computer.windows_api import WindowsAPI
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

try:
    import pyautogui

    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.05
except Exception:  # pragma: no cover
    pyautogui = None


class InputDriver:
    """Low-level input dispatch driver with coordinate safety and normalization."""

    def __init__(self, windows_api: WindowsAPI | None = None) -> None:
        self.windows_api = windows_api or WindowsAPI()
        self.windows_api.init_dpi_awareness()

    def check_emergency_stop(self) -> None:
        """Verify that emergency stop is not triggered before input injection."""
        from jarvis.computer.emergency_stop import get_emergency_stop

        get_emergency_stop().verify_clear()

    def check_integrity_boundary(self, hwnd: int = 0) -> None:
        """Detect Windows UIPI privilege boundaries to avoid silent input drop."""
        if not self.windows_api.is_windows:
            return

        # Check foreground or target window
        target_hwnd = hwnd or (
            self.windows_api._user32.GetForegroundWindow() if self.windows_api._user32 else 0
        )
        if not target_hwnd or not self.windows_api._user32:
            return

        pid = ctypes.wintypes.DWORD()
        self.windows_api._user32.GetWindowThreadProcessId(target_hwnd, ctypes.byref(pid))
        if not pid.value:
            return

        try:
            import psutil  # type: ignore[import-untyped]

            proc = psutil.Process(pid.value)
            p_name = proc.name().lower()
            # Known protected system processes
            if p_name in ("logonui.exe", "winlogon.exe", "taskmgr.exe"):
                raise PermissionError(
                    f"INPUT_BLOCKED_BY_INTEGRITY_BOUNDARY: Process '{p_name}' (PID {pid.value}) is protected by Windows UIPI."
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    def get_metrics(self) -> DisplayMetrics:
        """Retrieve current display metrics."""
        return self.windows_api.get_display_metrics()

    def canonical_to_physical(
        self,
        x: float,
        y: float,
        normalized: bool = False,
        monitor_index: int = 1,
    ) -> tuple[int, int]:
        """Convert any canonical model coordinate into authoritative physical desktop pixels.

        Supported domains:
        1. Unit float [0.0, 1.0]: Multiplied by monitor width and height.
        2. Mille integer [0, 999]: Scaled by 999.0 and monitor width and height.
        3. Physical display pixels (> 999 or explicit normalized=False): Validated directly.
        """
        metrics = self.get_metrics()
        target_mon = None
        for m in metrics.monitors:
            if m.monitor_index == monitor_index:
                target_mon = m
                break
        if not target_mon:
            target_mon = metrics.monitors[0] if metrics.monitors else None

        mon_left = target_mon.left if target_mon else 0
        mon_top = target_mon.top if target_mon else 0
        mon_w = target_mon.width if target_mon else metrics.primary_width
        mon_h = target_mon.height if target_mon else metrics.primary_height

        f_x, f_y = float(x), float(y)

        # 1. Explicit normalized flag
        if normalized:
            if 0.0 <= f_x <= 1.0 and 0.0 <= f_y <= 1.0:
                # Unit float scale [0.0, 1.0]
                clamped_x = max(0.0, min(1.0, f_x))
                clamped_y = max(0.0, min(1.0, f_y))
                px = mon_left + round(clamped_x * mon_w)
                py = mon_top + round(clamped_y * mon_h)
                return px, py
            else:
                # Mille scale [0, 999]
                clamped_x = max(0.0, min(999.0, f_x))
                clamped_y = max(0.0, min(999.0, f_y))
                px = mon_left + round((clamped_x / 999.0) * mon_w)
                py = mon_top + round((clamped_y / 999.0) * mon_h)
                return px, py

        # 2. Implicit unit float scale [0.0, 1.0] for non-integer fractional inputs
        if 0.0 < f_x < 1.0 and 0.0 < f_y < 1.0:
            px = mon_left + round(f_x * mon_w)
            py = mon_top + round(f_y * mon_h)
            return px, py

        # 3. Direct physical pixels
        return round(f_x), round(f_y)

    def denormalize(
        self,
        norm_x: float,
        norm_y: float,
        monitor_index: int = 1,
    ) -> tuple[int, int]:
        """Convert normalized coordinates to physical desktop pixels."""
        return self.canonical_to_physical(
            norm_x, norm_y, normalized=True, monitor_index=monitor_index
        )

    def validate_coordinate_bounds(self, px: int, py: int) -> bool:
        """Verify that physical coordinates lie strictly within active virtual screen bounds."""
        metrics = self.get_metrics()
        v_l = metrics.virtual_left
        v_t = metrics.virtual_top
        v_r = v_l + metrics.virtual_width
        v_b = v_t + metrics.virtual_height

        return v_l <= px < v_r and v_t <= py < v_b

    def click(
        self,
        x: float,
        y: float,
        button: MouseButton | str = MouseButton.LEFT,
        clicks: int = 1,
        normalized: bool = False,
        monitor_index: int = 1,
    ) -> dict[str, Any]:
        """Inject mouse click at specified coordinates with fail-safe gates."""
        self.check_emergency_stop()
        self.check_integrity_boundary()

        if pyautogui is None:
            raise RuntimeError("pyautogui input driver is not available on this platform.")

        self.windows_api.attach_to_default_desktop()

        btn_str = button.value if isinstance(button, MouseButton) else str(button).lower()
        px, py = self.canonical_to_physical(
            x, y, normalized=normalized, monitor_index=monitor_index
        )

        if not self.validate_coordinate_bounds(px, py):
            raise ValueError(
                f"Coordinate exceeds display bounds: ({px}, {py}) outside virtual screen."
            )

        pyautogui.click(x=px, y=py, button=btn_str, clicks=clicks)

        return {
            "status": "success",
            "action": "click",
            "pixel_x": px,
            "pixel_y": py,
            "button": btn_str,
            "clicks": clicks,
            "normalized": normalized,
        }

    def double_click(
        self,
        x: float,
        y: float,
        normalized: bool = False,
        monitor_index: int = 1,
    ) -> dict[str, Any]:
        """Inject double-click at target coordinates."""
        return self.click(
            x=x,
            y=y,
            button=MouseButton.LEFT,
            clicks=2,
            normalized=normalized,
            monitor_index=monitor_index,
        )

    def right_click(
        self,
        x: float,
        y: float,
        normalized: bool = False,
        monitor_index: int = 1,
    ) -> dict[str, Any]:
        """Inject right-click at target coordinates."""
        return self.click(
            x=x,
            y=y,
            button=MouseButton.RIGHT,
            clicks=1,
            normalized=normalized,
            monitor_index=monitor_index,
        )

    def move_mouse(
        self,
        x: float,
        y: float,
        normalized: bool = False,
        monitor_index: int = 1,
    ) -> dict[str, Any]:
        """Move cursor to target coordinates without clicking."""
        self.check_emergency_stop()
        if pyautogui is None:
            raise RuntimeError("pyautogui input driver is not available.")

        self.windows_api.attach_to_default_desktop()
        px, py = self.canonical_to_physical(
            x, y, normalized=normalized, monitor_index=monitor_index
        )

        if not self.validate_coordinate_bounds(px, py):
            raise ValueError(f"Coordinate ({px}, {py}) outside virtual screen bounds.")

        pyautogui.moveTo(x=px, y=py)
        return {"status": "success", "action": "move_mouse", "pixel_x": px, "pixel_y": py}

    def drag(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        normalized: bool = False,
        monitor_index: int = 1,
        duration: float = 0.5,
    ) -> dict[str, Any]:
        """Drag mouse cursor from starting coordinates to ending coordinates."""
        self.check_emergency_stop()
        if pyautogui is None:
            raise RuntimeError("pyautogui input driver is not available.")

        self.windows_api.attach_to_default_desktop()
        f_px, f_py = self.canonical_to_physical(
            from_x, from_y, normalized=normalized, monitor_index=monitor_index
        )
        t_px, t_py = self.canonical_to_physical(
            to_x, to_y, normalized=normalized, monitor_index=monitor_index
        )

        if not self.validate_coordinate_bounds(f_px, f_py) or not self.validate_coordinate_bounds(
            t_px, t_py
        ):
            raise ValueError("Drag coordinates outside virtual screen bounds.")

        pyautogui.moveTo(f_px, f_py)
        pyautogui.dragTo(t_px, t_py, duration=duration, button="left")

        return {
            "status": "success",
            "action": "drag",
            "from_pixel": [f_px, f_py],
            "to_pixel": [t_px, t_py],
        }

    def scroll(self, clicks: int) -> dict[str, Any]:
        """Inject vertical mouse wheel scroll deltas."""
        self.check_emergency_stop()
        if pyautogui is None:
            raise RuntimeError("pyautogui input driver is not available.")

        self.windows_api.attach_to_default_desktop()
        # In Windows pyautogui: positive scrolls up, negative scrolls down
        # 1 click is typically 120 WHEEL_DELTA
        pyautogui.scroll(clicks * 100)
        return {"status": "success", "action": "scroll", "clicks": clicks}

    def type_text(
        self,
        text: str,
        press_enter: bool = False,
        interval: float = 0.01,
    ) -> dict[str, Any]:
        """Type text into currently focused control."""
        self.check_emergency_stop()
        self.check_integrity_boundary()

        if not text:
            raise ValueError("Text content cannot be empty.")
        if pyautogui is None:
            raise RuntimeError("pyautogui input driver is not available.")

        self.windows_api.attach_to_default_desktop()

        # Handle multiline and special characters via write
        pyautogui.write(text, interval=interval)
        if press_enter:
            pyautogui.press("enter")

        return {
            "status": "success",
            "action": "type",
            "character_count": len(text),
            "press_enter": press_enter,
        }

    def press_key(self, key: str) -> dict[str, Any]:
        """Press a single keyboard key."""
        self.check_emergency_stop()
        if pyautogui is None:
            raise RuntimeError("pyautogui input driver is not available.")

        self.windows_api.attach_to_default_desktop()
        pyautogui.press(key.lower().strip())
        return {"status": "success", "action": "press_key", "key": key}

    def hotkey(self, keys: list[str]) -> dict[str, Any]:
        """Trigger keyboard combination."""
        self.check_emergency_stop()
        if pyautogui is None:
            raise RuntimeError("pyautogui input driver is not available.")

        self.windows_api.attach_to_default_desktop()
        clean_keys = [k.lower().strip() for k in keys if k.strip()]
        if not clean_keys:
            raise ValueError("Hotkey combination cannot be empty.")

        pyautogui.hotkey(*clean_keys)
        return {"status": "success", "action": "hotkey", "combination": "+".join(clean_keys)}
