"""Unit tests for UIAEngine semantic element inspection."""

from __future__ import annotations

import pytest

from jarvis.computer.uia import UIAEngine


@pytest.fixture
def uia_engine() -> UIAEngine:
    return UIAEngine()


def test_uia_engine_init(uia_engine: UIAEngine) -> None:
    """Verify UIAEngine initializes on Windows without crashing."""
    assert uia_engine.windows_api is not None


def test_uia_engine_inspect_elements(uia_engine: UIAEngine) -> None:
    """Verify element inspection on available visible desktop windows."""
    win_api = uia_engine.windows_api
    windows = win_api.list_desktop_windows(include_invisible=False)
    if not windows:
        pytest.skip("No visible desktop windows found for UIA inspection.")

    # Inspect the first visible window (e.g. Explorer/Taskbar/Terminal)
    target_win = windows[0]
    elements = uia_engine.inspect_window_elements(target_win.hwnd, max_elements=20)
    assert isinstance(elements, list)
    # Each inspected element must have valid geometry and types
    for el in elements:
        assert el.control_type != ""
        assert el.width >= 0
        assert el.height >= 0
