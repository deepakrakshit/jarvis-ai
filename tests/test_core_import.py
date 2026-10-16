"""Regression tests for importing portable core services."""

import os
import subprocess
import sys
from pathlib import Path


def test_control_plane_import_does_not_require_windows_modules() -> None:
    """The portable control plane can load without Windows-only modules."""
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
import builtins

real_import = builtins.__import__


def block_windows_modules(name, *args, **kwargs):
    if name == "winreg":
        raise ImportError("Windows registry module unavailable")
    return real_import(name, *args, **kwargs)


builtins.__import__ = block_windows_modules
from jarvis.core.control_plane import ControlPlane

assert ControlPlane.__name__ == "ControlPlane"
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
