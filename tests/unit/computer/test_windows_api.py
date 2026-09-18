"""Unit tests for Windows Win32 Desktop and Window Manager Engine."""

from __future__ import annotations

import pytest

from jarvis.computer.models import DesktopState
from jarvis.computer.windows_api import WindowsAPI


@pytest.fixture
def win_api() -> WindowsAPI:
    return WindowsAPI()


def test_windows_api_init_and_dpi(win_api: WindowsAPI) -> None:
    """Verify DPI awareness initialization does not crash and runs safely."""
    initialized = win_api.init_dpi_awareness()
    assert isinstance(initialized, bool)


def test_windows_api_attach_desktop(win_api: WindowsAPI) -> None:
    """Verify attaching worker thread to interactive desktop."""
    attached = win_api.attach_to_default_desktop()
    assert isinstance(attached, bool)


def test_windows_api_detect_desktop_state(win_api: WindowsAPI) -> None:
    """Verify desktop state detection returns a recognized DesktopState."""
    state = win_api.detect_desktop_state()
    assert state in (
        DesktopState.NORMAL_DESKTOP,
        DesktopState.ELEVATED_DESKTOP,
        DesktopState.SECURE_DESKTOP,
        DesktopState.LOCKED_DESKTOP,
        DesktopState.UNAVAILABLE,
    )


def test_windows_api_display_metrics(win_api: WindowsAPI) -> None:
    """Verify virtual screen and monitor metrics queries."""
    metrics = win_api.get_display_metrics()
    assert metrics.virtual_width > 0
    assert metrics.virtual_height > 0
    assert len(metrics.monitors) >= 1

    primary = metrics.monitors[0]
    assert primary.width > 0
    assert primary.height > 0


def test_windows_api_list_windows(win_api: WindowsAPI) -> None:
    """Verify enumeration of visible windows on interactive desktop."""
    windows = win_api.list_desktop_windows(include_invisible=False)
    assert isinstance(windows, list)
    for win in windows:
        assert win.hwnd > 0
        assert win.width >= 0
        assert win.height >= 0


def test_windows_api_list_processes(win_api: WindowsAPI) -> None:
    """Verify process enumeration with visible window correlation."""
    processes = win_api.list_processes(limit=20)
    assert isinstance(processes, list)
    assert len(processes) > 0
    assert all(proc.pid >= 0 for proc in processes)
    assert any(proc.name != "" for proc in processes)


def test_windows_api_protected_process_termination(win_api: WindowsAPI) -> None:
    """Verify termination of protected critical OS system processes is strictly rejected."""
    # Test protected PIDs (0=System Idle, 4=System Kernel)
    for protected_pid in (0, 4):
        with pytest.raises(
            PermissionError, match="Security Gate: Refusing to terminate protected system process"
        ):
            win_api.terminate_process(pid=protected_pid)
