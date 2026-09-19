"""Tests for Gemini 3.8 Live Conversational Bridge."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.cognition.gemini_live import (
    DEFAULT_LIVE_TOOLS,
    TOOL_TO_CAPABILITY_MAP,
    GeminiLiveBridge,
    LiveSessionState,
)
from jarvis.config import settings
from jarvis.contracts.action import ActionResult, ActionStatus, ExecutionTarget
from jarvis.policy.firewall import CAPABILITY_SYSTEM_INFO


@pytest.fixture
def mock_broker() -> AsyncMock:
    """Mock ActionBroker for testing tool call handling."""
    broker = AsyncMock()
    broker.execute = AsyncMock(
        return_value=ActionResult(
            action_id="ACT-LIVE-1",
            task_id="TASK-1",
            status=ActionStatus.SUCCEEDED,
            execution_target=ExecutionTarget.WINDOWS_NODE,
            output={"cpu_percent": 12.5, "os": "Windows"},
            verified=True,
            audit_logged=True,
        )
    )
    return broker


def test_gemini_live_bridge_defaults() -> None:
    """Verify default initial state of the Live bridge."""
    bridge = GeminiLiveBridge()
    assert bridge.state == LiveSessionState.DISCONNECTED
    assert bridge.session_id.startswith("LIVE-")
    assert bridge.voice_name == settings.VOICE_DEFAULT_NAME
    assert len(DEFAULT_LIVE_TOOLS) >= 7
    assert "system_info" in TOOL_TO_CAPABILITY_MAP

    tools = bridge._build_tools()
    assert len(tools) == 1
    assert tools[0].function_declarations is not None
    assert len(tools[0].function_declarations) == len(DEFAULT_LIVE_TOOLS)


@pytest.mark.asyncio
async def test_tool_call_interception(mock_broker: AsyncMock) -> None:
    """Verify tool call routing from Gemini Live to ActionBroker."""
    bridge = GeminiLiveBridge(broker=mock_broker)

    # Fake active session to capture send_tool_response
    mock_session = AsyncMock()
    bridge._active_session = mock_session

    fake_tool_call = MagicMock()
    fake_func_call = MagicMock()
    fake_func_call.id = "call_test_123"
    fake_func_call.name = "system_info"
    fake_func_call.args = {}
    fake_tool_call.function_calls = [fake_func_call]

    await bridge._handle_tool_call(fake_tool_call)

    # Verify ActionBroker was called with correct capability
    assert mock_broker.execute.called
    call_arg = mock_broker.execute.call_args[0][0]
    assert call_arg.capability == CAPABILITY_SYSTEM_INFO
    assert call_arg.target == ExecutionTarget.WINDOWS_NODE

    # Verify tool response was sent back to the session
    assert mock_session.send_tool_response.called
    responses = mock_session.send_tool_response.call_args[1]["function_responses"]
    assert len(responses) == 1
    assert responses[0].id == "call_test_123"
    assert responses[0].name == "system_info"
    assert responses[0].response["status"] == "success"
    assert responses[0].response["output"]["os"] == "Windows"


@pytest.mark.asyncio
async def test_live_gemini_3_8_connection_and_audio() -> None:
    """Live integration test connecting to Gemini 3.8 Live over WebSocket."""
    if not settings.GEMINI_API_KEY:
        pytest.skip("GEMINI_API_KEY not configured")

    bridge = GeminiLiveBridge(voice_name="Aoede")
    audio_received = asyncio.Event()

    async def on_audio(chunk: bytes) -> None:
        if len(chunk) > 0:
            audio_received.set()

    bridge.audio_chunk_handler = on_audio

    try:
        await bridge.connect()
        assert bridge.state == LiveSessionState.ACTIVE

        # Send text turn
        await bridge.send_text("Good morning JARVIS. Say hello.")

        # Wait up to 10 seconds for streaming audio response
        try:
            await asyncio.wait_for(audio_received.wait(), timeout=10.0)
            assert audio_received.is_set()
        except asyncio.TimeoutError:
            pytest.fail("Timed out waiting for live audio chunks from Gemini 3.8 Live")

    finally:
        await bridge.disconnect()
        assert bridge.state == LiveSessionState.CLOSED
