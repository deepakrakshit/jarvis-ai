"""Desktop GUI Automation, Screenshots, and Window Controls for Windows.

Implements visual capture, mouse/keyboard automation, and window enumeration.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import pyautogui
import pygetwindow as gw

from jarvis.config import settings
from jarvis.telemetry import logger


def capture_screenshot(save_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Capture current desktop display and save as an artifact."""
    target_dir = save_dir or settings.ARTIFACTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"screenshot_{uuid4().hex[:8]}.png"
    filepath = target_dir / filename

    logger.info(f"Capturing desktop screenshot to {filepath}")
    screenshot = pyautogui.screenshot()
    screenshot.save(str(filepath))

    width, height = screenshot.size
    return {
        "artifact_path": str(filepath),
        "filename": filename,
        "width": width,
        "height": height,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> Dict[str, Any]:
    """Perform mouse click at specific screen coordinates."""
    logger.info(f"Mouse click at ({x}, {y}) button={button} clicks={clicks}")
    pyautogui.click(x=x, y=y, button=button, clicks=clicks)
    return {
        "action": "click",
        "x": x,
        "y": y,
        "button": button,
        "clicks": clicks,
    }


def type_text(text: str, interval: float = 0.02) -> Dict[str, Any]:
    """Type keyboard text into the currently focused window."""
    logger.info(f"Typing text (length {len(text)})")
    pyautogui.write(text, interval=interval)
    return {
        "action": "type",
        "characters_typed": len(text),
    }


def press_key(key: str) -> Dict[str, Any]:
    """Press a specific keyboard key (e.g. 'enter', 'tab', 'esc')."""
    logger.info(f"Pressing keyboard key: {key}")
    pyautogui.press(key)
    return {
        "action": "key_press",
        "key": key,
    }


def list_open_windows() -> List[Dict[str, Any]]:
    """Enumerate all open desktop application windows."""
    windows: List[Dict[str, Any]] = []
    for window in gw.getAllWindows():
        title = window.title.strip()
        if title:
            windows.append(
                {
                    "title": title,
                    "x": window.left,
                    "y": window.top,
                    "width": window.width,
                    "height": window.height,
                    "is_active": window.isActive,
                    "is_minimized": window.isMinimized,
                }
            )
    return windows
