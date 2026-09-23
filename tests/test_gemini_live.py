"""Tests for Gemini 3.8 Live Conversational Bridge."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.mark.asyncio
async def test_tool_call_delegate_task() -> None:
    """Verify tool call delegation to specialist model (GPT-OSS 120B)."""
    from unittest.mock import patch

    from jarvis.contracts.model import ModelFamily, ModelInvocationResponse, ModelProvider

    bridge = GeminiLiveBridge()
    mock_session = AsyncMock()
    bridge._active_session = mock_session

    fake_tool_call = MagicMock()
    fake_func_call = MagicMock()
    fake_func_call.id = "call_del_1"
    fake_func_call.name = "delegate_task"
    fake_func_call.args = {
        "instruction": "Write a binary search function in Python.",
        "target_model": "GPT-OSS 120B",
        "task_type": "CODING",
    }
    fake_tool_call.function_calls = [fake_func_call]

    mock_resp = ModelInvocationResponse(
        model_family=ModelFamily.GPT_OSS_120B,
        provider=ModelProvider.GROQ,
        text_content="def binary_search(arr, target): ...",
        total_tokens=85,
    )

    with patch(
        "jarvis.cognition.gemini_live.model_router.invoke",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ) as mock_invoke:
        await bridge._handle_tool_call(fake_tool_call)
        assert mock_invoke.called
        call_kw = mock_invoke.call_args[1]
        assert call_kw["request"].model_family == ModelFamily.GPT_OSS_120B
        assert "binary search" in call_kw["request"].prompt

    assert mock_session.send_tool_response.called
    responses = mock_session.send_tool_response.call_args[1]["function_responses"]
    assert len(responses) == 1
    assert responses[0].id == "call_del_1"
    assert responses[0].response["status"] == "success"
    assert responses[0].response["delegated_model"] == "GPT-OSS 120B"
    assert "binary_search" in responses[0].response["result"]


@pytest.mark.asyncio
async def test_tool_call_memory_store_and_search() -> None:
    """Verify in-flight memory storage and search during Gemini Live sessions."""
    bridge = GeminiLiveBridge()
    mock_session = AsyncMock()
    bridge._active_session = mock_session

    # Store memory
    fake_tool_call_store = MagicMock()
    fake_func_call_store = MagicMock()
    fake_func_call_store.id = "call_mem_1"
    fake_func_call_store.name = "memory_store"
    fake_func_call_store.args = {
        "content": "Operator prefers concise terminal outputs.",
        "key": "cli_preference",
    }
    fake_tool_call_store.function_calls = [fake_func_call_store]

    await bridge._handle_tool_call(fake_tool_call_store)
    assert mock_session.send_tool_response.called
    store_resp = mock_session.send_tool_response.call_args[1]["function_responses"][0]
    assert store_resp.response["status"] == "success"
    assert store_resp.response["stored"] is True

    # Search memory
    fake_tool_call_search = MagicMock()
    fake_func_call_search = MagicMock()
    fake_func_call_search.id = "call_mem_2"
    fake_func_call_search.name = "memory_search"
    fake_func_call_search.args = {
        "query": "concise terminal",
    }
    fake_tool_call_search.function_calls = [fake_func_call_search]

    await bridge._handle_tool_call(fake_tool_call_search)
    search_resp = mock_session.send_tool_response.call_args[1]["function_responses"][0]
    assert search_resp.response["status"] == "success"
    assert search_resp.response["count"] >= 1


@pytest.mark.asyncio
async def test_multimodal_send_image_and_video() -> None:
    """Verify sending image and video frame inputs to the Live session."""
    bridge = GeminiLiveBridge()
    bridge.state = LiveSessionState.ACTIVE
    mock_session = AsyncMock()
    bridge._active_session = mock_session

    # 1. Send image with prompt
    await bridge.send_image(
        b"fake_jpeg_bytes", mime_type="image/jpeg", prompt="Describe this diagram"
    )
    assert mock_session.send_client_content.called

    # 2. Send image realtime blob
    mock_session.reset_mock()
    await bridge.send_image(b"fake_png_bytes", mime_type="image/png")
    assert mock_session.send_realtime_input.called

    # 3. Send video frame
    mock_session.reset_mock()
    await bridge.send_video_frame(b"fake_frame_bytes", mime_type="image/jpeg")
    assert mock_session.send_realtime_input.called


@pytest.mark.asyncio
async def test_live_web_search_tool_execution() -> None:
    """Verify web_search tool executes and sends structured responses back to Gemini Live."""
    bridge = GeminiLiveBridge()
    mock_session = AsyncMock()
    bridge._active_session = mock_session

    fake_tool_call = MagicMock()
    fake_func_call = MagicMock()
    fake_func_call.id = "call_search_999"
    fake_func_call.name = "web_search"
    fake_func_call.args = {"query": "deepmind antigravity"}
    fake_tool_call.function_calls = [fake_func_call]

    with patch("jarvis.cognition.gemini_live.search_web", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [
            {
                "title": "Antigravity AI",
                "snippet": "Autonomous coding agent",
                "url": "https://deepmind.google",
            }
        ]
        await bridge._handle_tool_call(fake_tool_call)

        mock_search.assert_called_once_with(query="deepmind antigravity")
        mock_session.send_tool_response.assert_called_once()
        sent_responses = mock_session.send_tool_response.call_args[1]["function_responses"]
        assert len(sent_responses) == 1
        assert sent_responses[0].name == "web_search"
        assert sent_responses[0].response["status"] == "success"
        assert sent_responses[0].response["query"] == "deepmind antigravity"
        assert len(sent_responses[0].response["results"]) == 1
