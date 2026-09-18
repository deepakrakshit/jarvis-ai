"""Unit tests for ComputerStateVerifier."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jarvis.computer.models import WindowInfo
from jarvis.computer.verifier import ComputerStateVerifier


@pytest.fixture
def verifier() -> ComputerStateVerifier:
    return ComputerStateVerifier()


def test_verifier_verify_visual_state_unchanged(verifier: ComputerStateVerifier) -> None:
    """Verify visual state comparison detects unchanged screen hash."""
    mock_perception = MagicMock()
    mock_snap = MagicMock()
    mock_snap.status = "success"
    mock_snap.metadata.sha256_digest = "abc123hash"
    mock_perception.capture.return_value = mock_snap

    verifier.perception = mock_perception

    res = verifier.verify_visual_state_changed(pre_screenshot_digest="abc123hash")
    assert res["verdict"] == "NO_VISUAL_CHANGE"
    assert res["state_changed"] is False


def test_verifier_verify_visual_state_changed(verifier: ComputerStateVerifier) -> None:
    """Verify visual state comparison detects modified screen hash."""
    mock_perception = MagicMock()
    mock_snap = MagicMock()
    mock_snap.status = "success"
    mock_snap.metadata.sha256_digest = "def456newhash"
    mock_perception.capture.return_value = mock_snap

    verifier.perception = mock_perception

    res = verifier.verify_visual_state_changed(pre_screenshot_digest="abc123oldhash")
    assert res["verdict"] == "VERIFIED"
    assert res["state_changed"] is True


def test_verifier_verify_close_success(verifier: ComputerStateVerifier) -> None:
    """Verify close verification passes when target window is absent."""
    mock_win_api = MagicMock()
    # No matching windows
    mock_win_api.list_desktop_windows.return_value = []
    mock_win_api.list_processes.return_value = []

    verifier.windows_api = mock_win_api

    res = verifier.verify_app_closed(app_name="non_existent_app_9999", timeout_seconds=0.1)
    assert res["verdict"] == "VERIFIED"
    assert "WINDOW_GONE" in res["checkpoints"] or "PROCESS_GONE" in res["checkpoints"]


def test_verifier_verify_launch_success(verifier: ComputerStateVerifier) -> None:
    """Verify launch verification passes when visible window appears."""
    mock_win_api = MagicMock()
    mock_win = WindowInfo(
        hwnd=12345,
        title="Calculator",
        class_name="ApplicationFrameWindow",
        process_id=9999,
        process_name="calc.exe",
        left=100,
        top=100,
        width=400,
        height=600,
        is_visible=True,
        is_foreground=True,
        is_minimized=False,
        is_maximized=False,
    )
    mock_win_api.list_desktop_windows.return_value = [mock_win]

    verifier.windows_api = mock_win_api

    res = verifier.verify_app_launched(app_name="calc", timeout_seconds=0.1)
    assert res["verdict"] == "VERIFIED"
    assert res["hwnd"] == 12345
