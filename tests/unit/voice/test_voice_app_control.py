"""Unit tests for Realtime Voice Plane Operating System & Desktop Application Control.

Verifies:
- Inclusion of native app control and screen perception in CURATED_LIVE_TOOLS.
- Dynamic tool projection and schema conformity for Gemini 3.8 Live.
- Zero-trust mediation, intent boundary verification, and policy authorization.
- Governed execution of launch_app, close_app, list_apps, screenshot, and get_window.
"""

from unittest.mock import patch

import pytest

from jarvis.core.gateway.mock_realtime import MockRealtimeAdapter
from jarvis.core.gateway.realtime import LiveToolCall
from jarvis.core.voice.task_manager import BackgroundTaskManager
from jarvis.core.voice.tool_bridge import (
    CURATED_LIVE_TOOLS,
    LiveToolBridge,
    is_mutation_authorized_by_intent,
)
from jarvis.core.voice.voice_agent import LiveVoiceAgent


@pytest.mark.asyncio
async def test_curated_live_tools_contains_app_and_screen_capabilities() -> None:
    """Verify CURATED_LIVE_TOOLS declares all 8 native app and screen tools."""
    tool_names = {t["name"] for t in CURATED_LIVE_TOOLS}
    expected_tools = {
        "jarvis_launch_app",
        "jarvis_close_app",
        "jarvis_list_apps",
        "jarvis_screenshot",
        "jarvis_click",
        "jarvis_type",
        "jarvis_hotkey",
        "jarvis_get_window",
    }
    assert expected_tools.issubset(tool_names), f"Missing tools: {expected_tools - tool_names}"

    task_mgr = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_mgr)
    defs = bridge.get_tool_definitions()
    assert len(defs) == 1
    projected_names = {decl["name"] for decl in defs[0]["function_declarations"]}
    assert expected_tools.issubset(projected_names)


@pytest.mark.asyncio
async def test_intent_boundary_authorizes_os_actions() -> None:
    """User intent analyzer authorizes open, launch, close, and quit actions."""
    # Launch intent
    ok_open, _ = is_mutation_authorized_by_intent("open chrome", "jarvis_launch_app")
    assert ok_open is True

    ok_launch, _ = is_mutation_authorized_by_intent("launch notepad", "jarvis_launch_app")
    assert ok_launch is True

    # Close intent
    ok_close, _ = is_mutation_authorized_by_intent("close chrome", "jarvis_close_app")
    assert ok_close is True

    ok_quit, _ = is_mutation_authorized_by_intent("quit notepad", "jarvis_close_app")
    assert ok_quit is True

    # Mutating tool during pure read intent is blocked
    blocked, reason = is_mutation_authorized_by_intent("inspect README.md", "jarvis_launch_app")
    assert blocked is False
    assert "read-only" in reason


@pytest.mark.asyncio
async def test_voice_bridge_launch_app_execution() -> None:
    """Voice tool bridge executes jarvis_launch_app through Action Broker."""
    task_mgr = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_mgr)

    with patch("jarvis.tools.native.app_control.launch_application") as mock_launch:
        mock_launch.return_value = {
            "status": "success",
            "action": "launch",
            "app_name": "notepad",
            "pid": 9999,
            "executable": "C:\\Windows\\System32\\notepad.exe",
            "message": "Successfully launched and verified 'notepad'.",
        }

        call = LiveToolCall(
            call_id="call_launch_001",
            name="jarvis_launch_app",
            arguments={"app_name": "notepad"},
        )
        resp = await bridge.execute_tool_call(
            call,
            session_id="00000000-0000-0000-0000-000000000001",
            user_intent="open notepad",
        )

        assert resp.response.get("status") == "success"
        assert resp.response.get("pid") == 9999


@pytest.mark.asyncio
async def test_voice_bridge_list_apps_execution() -> None:
    """Voice tool bridge executes jarvis_list_apps via safe read path."""
    task_mgr = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_mgr)

    call = LiveToolCall(
        call_id="call_list_001",
        name="jarvis_list_apps",
        arguments={"limit": 5},
    )
    resp = await bridge.execute_tool_call(
        call,
        session_id="00000000-0000-0000-0000-000000000001",
        user_intent="what applications are running",
    )

    assert resp.response.get("status") == "success"
    assert "count" in resp.response
    assert isinstance(resp.response.get("processes"), list)


@pytest.mark.asyncio
async def test_voice_bridge_close_app_execution() -> None:
    """Voice tool bridge executes jarvis_close_app through Action Broker."""
    task_mgr = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_mgr)

    with patch("jarvis.tools.native.app_control.close_application") as mock_close:
        mock_close.return_value = {
            "status": "success",
            "app_name": "notepad",
            "terminated_count": 1,
            "terminated_pids": [9999],
        }

        call = LiveToolCall(
            call_id="call_close_001",
            name="jarvis_close_app",
            arguments={"app_name": "notepad"},
        )
        resp = await bridge.execute_tool_call(
            call,
            session_id="00000000-0000-0000-0000-000000000001",
            user_intent="close notepad",
        )

        assert resp.response.get("status") == "success"
        assert resp.response.get("terminated_count") == 1


@pytest.mark.asyncio
async def test_voice_bridge_get_window_execution() -> None:
    """Voice tool bridge executes jarvis_get_window via safe read path."""
    task_mgr = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_mgr)

    with patch("jarvis.tools.native.screen_control.get_active_window") as mock_win:
        mock_win.return_value = {
            "status": "success",
            "title": "Google Chrome - New Tab",
            "left": 0,
            "top": 0,
            "width": 1920,
            "height": 1080,
        }

        call = LiveToolCall(
            call_id="call_win_001",
            name="jarvis_get_window",
            arguments={},
        )
        resp = await bridge.execute_tool_call(
            call,
            session_id="00000000-0000-0000-0000-000000000001",
            user_intent="what window is active",
        )

        assert resp.response.get("status") == "success"
        assert resp.response.get("title") == "Google Chrome - New Tab"


@pytest.mark.asyncio
async def test_voice_bridge_screenshot_execution() -> None:
    """Voice tool bridge executes jarvis_screenshot via safe read path."""
    task_mgr = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_mgr)

    with patch("jarvis.tools.native.screen_control.capture_screen") as mock_cap:
        mock_cap.return_value = {
            "status": "success",
            "width": 1280,
            "height": 720,
            "base64_data": "fake_base64_frame",
            "mime_type": "image/jpeg",
            "byte_size": 1024,
        }

        call = LiveToolCall(
            call_id="call_shot_001",
            name="jarvis_screenshot",
            arguments={"max_dimension": 1280},
        )
        resp = await bridge.execute_tool_call(
            call,
            session_id="00000000-0000-0000-0000-000000000001",
            user_intent="take a screenshot",
        )

        assert resp.response.get("status") == "success"
        assert resp.response.get("width") == 1280


@pytest.mark.asyncio
async def test_voice_agent_system_instruction_mandates_app_control() -> None:
    """Verify LiveVoiceAgent initializes with explicit Operating System control invariant."""
    adapter = MockRealtimeAdapter()
    agent = LiveVoiceAgent(
        session_id="sess_test_app_control",
        adapter=adapter,
    )

    await agent.start()
    assert adapter.config is not None
    instruction = adapter.config.system_instruction
    assert instruction is not None
    assert "Operating System & Desktop Application Control" in instruction
    assert "jarvis_launch_app" in instruction
    assert "jarvis_close_app" in instruction
    assert "jarvis_list_apps" in instruction
    assert "NEVER tell the user that you cannot open applications" in instruction

    await agent.stop()
