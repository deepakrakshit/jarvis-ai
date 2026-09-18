"""JARVIS Computer Control Subsystem.

Canonical implementation of Windows OS perception, UI Automation, and input governance.
"""

from jarvis.computer.application_resolver import ApplicationResolver
from jarvis.computer.executor import WindowsComputerExecutor
from jarvis.computer.input_driver import InputDriver
from jarvis.computer.models import (
    AppResolution,
    ComputerActionResult,
    ComputerActionStatus,
    Coordinate,
    DesktopState,
    DisplayMetrics,
    MonitorInfo,
    MouseButton,
    ProcessInfo,
    ResolutionStatus,
    ScreenshotMetadata,
    ScreenshotResult,
    UIAControlType,
    UIElementInfo,
    WindowAction,
    WindowInfo,
)
from jarvis.computer.perception import ScreenPerception
from jarvis.computer.state_machine import ComputerActionStateMachine
from jarvis.computer.uia import UIAEngine
from jarvis.computer.verifier import ComputerStateVerifier
from jarvis.computer.windows_api import WindowsAPI

_default_executor: WindowsComputerExecutor | None = None


def get_computer_executor() -> WindowsComputerExecutor:
    """Retrieve or create the singleton WindowsComputerExecutor."""
    global _default_executor
    if _default_executor is None:
        _default_executor = WindowsComputerExecutor()
    return _default_executor


__all__ = [
    "AppResolution",
    "ApplicationResolver",
    "ComputerActionResult",
    "ComputerActionStateMachine",
    "ComputerActionStatus",
    "ComputerStateVerifier",
    "Coordinate",
    "DesktopState",
    "DisplayMetrics",
    "InputDriver",
    "MonitorInfo",
    "MouseButton",
    "ProcessInfo",
    "ResolutionStatus",
    "ScreenPerception",
    "ScreenshotMetadata",
    "ScreenshotResult",
    "UIAControlType",
    "UIAEngine",
    "UIElementInfo",
    "WindowAction",
    "WindowInfo",
    "WindowsAPI",
    "WindowsComputerExecutor",
    "get_computer_executor",
]
