"""JARVIS Native Operating System Screen Perception and Input Controller.

Compatibility wrapper delegating to the canonical WindowsComputerExecutor.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL import Image

from jarvis.computer import get_computer_executor


def capture_screen(
    output_format: str = "base64",
    max_dimension: int = 1280,
    quality: int = 80,
    monitor_index: int = 1,
    test_double_image: Image.Image | None = None,
) -> dict[str, Any]:
    """Capture a screen frame from the primary or specified desktop display."""
    executor = get_computer_executor()
    snap = executor.perception.capture(
        output_format="both",
        max_dimension=max_dimension,
        quality=quality,
        monitor_index=monitor_index,
        test_double_image=test_double_image,
    )
    if snap.status != "success":
        return {
            "status": snap.status,
            "action": "capture",
            "error_code": snap.error_code,
            "message": snap.message,
            "timestamp": snap.metadata.timestamp,
        }

    res: dict[str, Any] = {
        "status": "success",
        "action": "capture",
        "width": snap.metadata.width,
        "height": snap.metadata.height,
        "original_width": snap.metadata.original_width,
        "original_height": snap.metadata.original_height,
        "mime_type": snap.metadata.mime_type,
        "byte_size": snap.metadata.byte_size,
        "timestamp": snap.metadata.timestamp,
    }

    if output_format == "base64":
        res["base64_data"] = snap.base64_data or base64.b64encode(snap.raw_bytes).decode("utf-8")
    elif output_format == "bytes":
        res["bytes"] = snap.raw_bytes
    elif output_format == "both":
        res["base64_data"] = snap.base64_data
        res["bytes"] = snap.raw_bytes

    return res


def click_coordinate(
    x: float | int | None = None,
    y: float | int | None = None,
    button: str = "left",
    clicks: int = 1,
    normalized: bool = False,
    element_query: str | None = None,
    window_title: str | None = None,
    semantic_role: str | None = None,
) -> dict[str, Any]:
    """Click a specific pixel, normalized coordinate, or dynamic UI Automation element."""
    executor = get_computer_executor()
    res = executor.click(
        x=float(x) if x is not None else None,
        y=float(y) if y is not None else None,
        button=button,
        clicks=clicks,
        normalized=normalized,
        element_query=element_query,
        window_title=window_title,
        semantic_role=semantic_role,
    )
    if res.status.value != "EXECUTED":
        structured = res.to_structured_dict()
        structured["status"] = "error"
        structured["error"] = res.error or res.message
        return structured

    return {
        "status": "success",
        "action": "click",
        "x": res.details.get("pixel_x", x),
        "y": res.details.get("pixel_y", y),
        "button": button,
        "clicks": clicks,
        "normalized": normalized,
        "target": res.target,
        "target_source": res.target_source.value if res.target_source else None,
        "verification": res.verification_details,
    }


def type_text(
    text: str,
    press_enter: bool = False,
    interval: float = 0.01,
    element_query: str | None = None,
    window_title: str | None = None,
    recipient: str | None = None,
) -> dict[str, Any]:
    """Inject text keystrokes into the current or specified target foreground control."""
    if not text:
        raise ValueError("Text content cannot be empty.")
    executor = get_computer_executor()
    res = executor.type_text(
        text=text,
        press_enter=press_enter,
        interval=interval,
        element_query=element_query,
        window_title=window_title,
        recipient=recipient,
    )
    if res.status.value != "EXECUTED":
        structured = res.to_structured_dict()
        structured["status"] = "error"
        structured["error"] = res.error or res.message
        return structured

    return {
        "status": "success",
        "action": "type",
        "character_count": len(text),
        "press_enter": press_enter,
        "target": res.target,
        "verification": res.verification_details,
    }


def press_hotkey(keys: list[str]) -> dict[str, Any]:
    """Trigger a system keyboard shortcut."""
    executor = get_computer_executor()
    res = executor.hotkey(keys=keys)
    return {
        "status": "success" if res.status.value == "EXECUTED" else "error",
        "action": "hotkey",
        "keys": keys,
        "message": f"Dispatched hotkey: {'+'.join(keys)}",
    }


def get_active_window() -> dict[str, Any]:
    """Retrieve metadata and geometry of the active foreground window."""
    executor = get_computer_executor()
    executor.windows_api.attach_to_default_desktop()
    windows = executor.windows_api.list_desktop_windows(include_invisible=False)

    for w in windows:
        if w.is_foreground:
            return {
                "status": "success",
                "action": "get_window",
                "hwnd": w.hwnd,
                "title": w.title,
                "process_name": w.process_name,
                "left": w.left,
                "top": w.top,
                "width": w.width,
                "height": w.height,
                "is_maximized": w.is_maximized,
                "is_minimized": w.is_minimized,
            }

    if windows:
        w = windows[0]
        return {
            "status": "success",
            "action": "get_window",
            "hwnd": w.hwnd,
            "title": w.title,
            "process_name": w.process_name,
            "left": w.left,
            "top": w.top,
            "width": w.width,
            "height": w.height,
            "is_maximized": w.is_maximized,
            "is_minimized": w.is_minimized,
        }

    return {
        "status": "not_found",
        "action": "get_window",
        "message": "No active foreground window detected on desktop.",
    }
