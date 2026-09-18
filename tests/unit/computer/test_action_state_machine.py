"""Regression tests for Computer Control State Machine and Precondition Governance.

Validates zero-trust preconditions and action dependency enforcement:
- Test A: Focus succeeds -> type executes
- Test B: Focus fails -> type is rejected
- Test C: Focus returns DENIED -> dependent action cannot execute (PRECONDITION_NOT_SATISFIED)
- Test D: Window disappears after focus -> type is rejected (WINDOW_NOT_FOUND)
- Test E: Foreground changes between focus and type -> type is rejected (WINDOW_NOT_FOREGROUND)
- Test J: End-to-end "Open YouTube in Chrome" workflow
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from jarvis.computer.executor import WindowsComputerExecutor
from jarvis.computer.models import (
    AppResolution,
    ComputerActionReasonCode,
    ComputerActionResult,
    ComputerActionState,
    ComputerActionStatus,
    ResolutionStatus,
    WindowInfo,
)
from jarvis.computer.state_machine import ComputerActionStateMachine


def _create_mock_window(
    hwnd: int = 12345,
    title: str = "Google Chrome",
    process_name: str = "chrome.exe",
    is_foreground: bool = True,
) -> WindowInfo:
    """Helper to instantiate mock WindowInfo."""
    return WindowInfo(
        hwnd=hwnd,
        title=title,
        class_name="Chrome_WidgetWin_1",
        process_id=9999,
        process_name=process_name,
        left=0,
        top=0,
        width=1920,
        height=1080,
        is_visible=True,
        is_foreground=is_foreground,
        is_minimized=False,
        is_maximized=True,
    )


@pytest.fixture
def state_machine() -> ComputerActionStateMachine:
    return ComputerActionStateMachine()


@pytest.fixture
def mock_executor(state_machine: ComputerActionStateMachine) -> WindowsComputerExecutor:
    mock_win_api = MagicMock()
    mock_win_api.is_windows = True
    mock_win_api._user32 = MagicMock()
    mock_win_api._user32.GetForegroundWindow.return_value = 12345
    mock_win_api._user32.IsWindow.return_value = 1

    mock_perception = MagicMock()
    mock_perception.capture.return_value.status = "success"
    mock_perception.capture.return_value.metadata.sha256_digest = "test_digest_1"

    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = AppResolution(
        status=ResolutionStatus.FOUND,
        app_name="chrome",
        resolved_path="C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        resolution_source="REGISTRY_APP_PATHS",
        existing_hwnd=12345,
    )

    mock_input = MagicMock()
    mock_input.type_text.return_value = {
        "status": "success",
        "characters_typed": 11,
        "press_enter": True,
    }
    mock_input.hotkey.return_value = {"status": "success", "keys": ["ctrl", "l"]}

    mock_verifier = MagicMock()
    mock_verifier.verify_visual_state_changed.return_value = {
        "verdict": "VERIFIED",
        "state_changed": True,
    }
    mock_verifier.verify_app_launched.return_value = {
        "verdict": "VERIFIED",
        "pid": 9999,
        "hwnd": 12345,
    }

    executor = WindowsComputerExecutor(
        windows_api=mock_win_api,
        perception=mock_perception,
        resolver=mock_resolver,
        input_driver=mock_input,
        verifier=mock_verifier,
        state_machine=state_machine,
    )
    return executor


def test_test_a_focus_succeeds_then_type_executes(
    mock_executor: WindowsComputerExecutor,
) -> None:
    """Test A: When window focus succeeds, keystroke typing executes safely."""
    win_api: Any = mock_executor.windows_api
    mock_win = _create_mock_window(hwnd=12345, title="Google Chrome", is_foreground=True)
    win_api.find_window.return_value = mock_win
    win_api.focus_window.return_value = True

    # 1. Focus Chrome
    focus_res = mock_executor.focus_window(query="chrome")
    assert focus_res.status == ComputerActionStatus.EXECUTED
    assert focus_res.verification_verdict == "VERIFIED"
    assert (
        str(mock_executor.state_machine.current_state) == ComputerActionState.TARGET_FOCUSED.value
    )
    assert mock_executor.state_machine.target_hwnd == 12345

    # 2. Type text
    type_res = mock_executor.type_text("youtube.com", press_enter=True)
    assert type_res.status == ComputerActionStatus.EXECUTED
    assert type_res.verification_verdict == "VERIFIED"
    assert str(mock_executor.state_machine.current_state) == ComputerActionState.VERIFIED.value


def test_test_b_focus_fails_then_type_is_rejected(
    mock_executor: WindowsComputerExecutor,
) -> None:
    """Test B: When window focus fails, dependent typing is rejected closed."""
    win_api: Any = mock_executor.windows_api
    resolver: Any = mock_executor.resolver
    input_driver: Any = mock_executor.input_driver

    # Mock window not found
    win_api.find_window.return_value = None
    resolver.resolve.return_value = AppResolution(
        status=ResolutionStatus.NOT_FOUND,
        app_name="nonexistent_app",
        diagnostic_message="No binary found",
    )

    # 1. Attempt to focus nonexistent app
    focus_res = mock_executor.focus_window(query="nonexistent_app")
    assert focus_res.status in (ComputerActionStatus.DENIED, ComputerActionStatus.FAILED)
    assert focus_res.reason_code == ComputerActionReasonCode.WINDOW_NOT_FOUND
    assert mock_executor.state_machine.current_state == ComputerActionState.FAILED

    # 2. Dependent typing must be rejected with PRECONDITION_NOT_SATISFIED
    type_res = mock_executor.type_text("youtube.com", press_enter=True)
    assert type_res.status == ComputerActionStatus.DENIED
    assert type_res.reason_code == ComputerActionReasonCode.PRECONDITION_NOT_SATISFIED
    assert "prerequisite" in type_res.message.lower()
    input_driver.type_text.assert_not_called()


def test_test_c_focus_denied_blocks_dependent_actions(
    mock_executor: WindowsComputerExecutor,
) -> None:
    """Test C: When focus returns DENIED, dependent typing cannot execute."""
    input_driver: Any = mock_executor.input_driver
    mock_executor.state_machine.record_action_result(
        ComputerActionResult(
            action="computer:focus_window",
            status=ComputerActionStatus.DENIED,
            reason_code=ComputerActionReasonCode.WINDOW_NOT_FOUND,
            message="No matching window found for query: 'chrome'.",
            verification_verdict="DENIED",
        )
    )

    # Attempt typing
    res = mock_executor.type_text("youtube.com", press_enter=True)
    assert res.status == ComputerActionStatus.DENIED
    assert res.reason_code == ComputerActionReasonCode.PRECONDITION_NOT_SATISFIED
    assert res.suggested_action == "focus_window"
    input_driver.type_text.assert_not_called()


def test_test_d_window_disappears_after_focus(
    mock_executor: WindowsComputerExecutor,
) -> None:
    """Test D: If the target window closes between focus and typing, typing is rejected."""
    win_api: Any = mock_executor.windows_api
    input_driver: Any = mock_executor.input_driver
    mock_win = _create_mock_window(hwnd=12345)
    win_api.find_window.return_value = mock_win
    win_api.focus_window.return_value = True

    # 1. Focus succeeds
    focus_res = mock_executor.focus_window(query="chrome")
    assert focus_res.status == ComputerActionStatus.EXECUTED

    # 2. Target window disappears (IsWindow returns 0)
    win_api._user32.IsWindow.return_value = 0

    # 3. Attempt typing
    type_res = mock_executor.type_text("youtube.com", press_enter=True)
    assert type_res.status == ComputerActionStatus.DENIED
    assert type_res.reason_code == ComputerActionReasonCode.WINDOW_NOT_FOUND
    assert "no longer valid" in type_res.message.lower() or "not exist" in type_res.message.lower()
    input_driver.type_text.assert_not_called()


def test_test_e_foreground_changes_between_focus_and_type(
    mock_executor: WindowsComputerExecutor,
) -> None:
    """Test E: If the foreground changes to an unexpected window, typing is rejected."""
    win_api: Any = mock_executor.windows_api
    input_driver: Any = mock_executor.input_driver
    mock_win = _create_mock_window(hwnd=12345)
    win_api.find_window.return_value = mock_win
    win_api.focus_window.return_value = True

    # 1. Focus target window 12345
    focus_res = mock_executor.focus_window(query="chrome")
    assert focus_res.status == ComputerActionStatus.EXECUTED

    # 2. Foreground shifts to another window (e.g. 54321)
    win_api._user32.GetForegroundWindow.return_value = 54321

    # 3. Attempt typing
    type_res = mock_executor.type_text("youtube.com", press_enter=True)
    assert type_res.status == ComputerActionStatus.DENIED
    assert type_res.reason_code == ComputerActionReasonCode.WINDOW_NOT_FOREGROUND
    assert "not the active foreground window" in type_res.message
    input_driver.type_text.assert_not_called()


def test_test_j_open_youtube_in_chrome_workflow(
    mock_executor: WindowsComputerExecutor,
) -> None:
    """Test J: End-to-end governed sequence for opening YouTube in Chrome."""
    win_api: Any = mock_executor.windows_api
    input_driver: Any = mock_executor.input_driver
    mock_win = _create_mock_window(hwnd=12345, title="Google Chrome", is_foreground=True)
    win_api.find_window.return_value = mock_win
    win_api.focus_window.return_value = True

    # Dispatch navigate_browser capability
    res = mock_executor.navigate_browser(url="youtube.com", browser="chrome")
    assert res.status == ComputerActionStatus.EXECUTED
    assert res.verification_verdict == "VERIFIED"
    assert res.target == "https://youtube.com"

    # Verify input driver received hotkey (Ctrl+L) and URL keystrokes with enter
    input_driver.hotkey.assert_called_with(keys=["ctrl", "l"])
    input_driver.type_text.assert_called_with(text="https://youtube.com", press_enter=True)
    assert str(mock_executor.state_machine.current_state) == ComputerActionState.VERIFIED.value
