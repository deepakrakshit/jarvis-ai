"""Unit tests for RealtimeModelAdapter, LiveSessionConfig, and MockRealtimeAdapter."""

import pytest

from jarvis.core.gateway.mock_realtime import MockRealtimeAdapter
from jarvis.core.gateway.realtime import (
    LiveEventType,
    LiveInteractionStatus,
    LiveSessionConfig,
    LiveToolResponse,
)


@pytest.mark.asyncio
async def test_mock_adapter_lifecycle() -> None:
    """Verify mock adapter connect, send, receive, and close semantics."""
    adapter = MockRealtimeAdapter()
    config = LiveSessionConfig(model_id="gemini-3.8-live", voice_name="Puck")

    await adapter.connect(config)
    assert adapter.connected is True
    assert adapter.connect_count == 1

    # Send text and audio
    await adapter.send_text("Hello JARVIS")
    assert "Hello JARVIS" in adapter.sent_texts

    pcm_chunk = b"\x00\x01" * 100
    await adapter.send_audio(pcm_chunk)
    assert pcm_chunk in adapter.sent_audio

    # Test interruption
    await adapter.interrupt()
    assert adapter.interrupt_count == 1

    # Test tool response
    tool_resp = LiveToolResponse(
        call_id="call_001",
        name="jarvis_status",
        response={"status": "ok"},
    )
    await adapter.send_tool_response(tool_resp)
    assert tool_resp in adapter.sent_tool_responses

    await adapter.close()
    assert adapter.connected is False
    assert adapter.close_count == 1


@pytest.mark.asyncio
async def test_mock_adapter_event_streaming() -> None:
    """Verify asynchronous streaming of audio, text, and status events."""
    adapter = MockRealtimeAdapter()
    config = LiveSessionConfig(model_id="gemini-3.8-live")
    await adapter.connect(config)

    # Queue events
    adapter.queue_speech(text="Greeting from JARVIS")
    adapter.queue_status_change(LiveInteractionStatus.IN_PROGRESS)
    adapter.queue_status_change(LiveInteractionStatus.IDLE)
    await adapter.close()

    events = []
    async for event in adapter.receive_events():
        events.append(event)

    types = [e.event_type for e in events]
    assert LiveEventType.TEXT_DELTA in types
    assert LiveEventType.AUDIO_CHUNK in types
    assert LiveEventType.TURN_COMPLETE in types
    assert LiveEventType.STATUS_CHANGE in types


def test_live_session_config_voice_defaults() -> None:
    """Verify default voice identity in LiveSessionConfig defaults to Algenib."""
    config = LiveSessionConfig()
    assert config.voice_name == "Algenib"
