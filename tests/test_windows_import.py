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
