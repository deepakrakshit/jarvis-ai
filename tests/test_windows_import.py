"""Regression tests for importing desktop execution modules."""

import os
import subprocess
import sys
from pathlib import Path


def test_windows_package_import_without_gui_dependencies() -> None:
    """Importing the package does not require an available desktop session."""
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
import builtins

real_import = builtins.__import__


def block_gui_dependencies(name, *args, **kwargs):
    if name in {"pyautogui", "pygetwindow"}:
        raise ImportError("GUI dependency unavailable")
    return real_import(name, *args, **kwargs)


builtins.__import__ = block_gui_dependencies
from jarvis.execution.windows import capture_screenshot

assert callable(capture_screenshot)
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(source_root), environment.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_app_registry_import_without_registry_module() -> None:
    """Importing the registry module does not require the optional host module."""
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
import builtins

real_import = builtins.__import__


def block_registry_module(name, *args, **kwargs):
    if name == "winreg":
        raise ImportError("registry module unavailable")
    return real_import(name, *args, **kwargs)


builtins.__import__ = block_registry_module
from jarvis.execution.windows.app_registry import AppRegistry

assert AppRegistry.__name__ == "AppRegistry"
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(source_root), environment.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_window_station_import_without_windows_ctypes() -> None:
    """Importing the window-station helper does not require Win32 bindings."""
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
from jarvis.execution.windows.winsta import ensure_interactive_desktop_attached

assert ensure_interactive_desktop_attached() is False
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(source_root), environment.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_window_manager_import_without_windows_ctypes() -> None:
    """Importing the window manager does not require Win32 bindings."""
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
from jarvis.execution.windows.window_manager import WindowManager

manager = WindowManager()
assert manager.list_windows() == []
assert manager.get_active_window() is None
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(source_root), environment.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
