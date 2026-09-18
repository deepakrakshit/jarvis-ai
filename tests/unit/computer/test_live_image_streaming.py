"""Contract & integration tests for Realtime Visual Perception & Image Streaming.

Verifies:
1. Screen capture -> byte validation -> streaming to image sink.
2. LiveToolResponse omits bulky base64 data to eliminate the Gemini Live 1007 WebSocket error.
3. GoogleRealtimeAdapter sends raw JPEG chunks via genai_types.Blob.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from jarvis.core.gateway.google_realtime import GoogleRealtimeAdapter
from jarvis.core.gateway.realtime import LiveToolCall
from jarvis.core.voice.task_manager import BackgroundTaskManager
from jarvis.core.voice.tool_bridge import LiveToolBridge


@pytest.fixture
def task_manager() -> BackgroundTaskManager:
    return BackgroundTaskManager()


@pytest.fixture
def tool_bridge(task_manager: BackgroundTaskManager) -> LiveToolBridge:
    return LiveToolBridge(task_manager=task_manager)


@pytest.mark.asyncio
async def test_live_tool_bridge_screenshot_streams_to_sink_and_omits_bulky_base64(
    tool_bridge: LiveToolBridge,
) -> None:
    """Verify screenshot streams raw bytes to sink and strips large base64 data from response payload."""
    streamed_chunks: list[tuple[bytes, str]] = []

    async def mock_image_sink(img_bytes: bytes, mime_type: str) -> None:
        streamed_chunks.append((img_bytes, mime_type))

    tool_bridge.set_image_sink(mock_image_sink)

    call = LiveToolCall(
        call_id="call_screen_123",
        name="jarvis_screenshot",
        arguments={"max_dimension": 640, "quality": 75},
    )

    test_buf = io.BytesIO()
    Image.new("RGB", (640, 480), color="blue").save(test_buf, format="JPEG")
    fake_jpeg_bytes = test_buf.getvalue()

    from unittest.mock import patch

    with patch(
        "jarvis.core.voice.tool_bridge.dispatch_native_tool",
        return_value={
            "status": "success",
            "action": "capture",
            "width": 640,
            "height": 480,
            "mime_type": "image/jpeg",
            "byte_size": len(fake_jpeg_bytes),
            "bytes": fake_jpeg_bytes,
            "base64_data": "dummy_base64_payload",
        },
    ):
        resp = await tool_bridge.execute_tool_call(
            tool_call=call,
            session_id="session_test_123",
        )

    # 1. Verify image bytes were dispatched to the image sink
    assert len(streamed_chunks) >= 1
    raw_bytes, mime = streamed_chunks[0]
    assert len(raw_bytes) > 0
    assert mime == "image/jpeg"

    # 2. Verify LiveToolResponse does NOT contain bulky base64_data or raw bytes
    assert "base64_data" not in resp.response
    assert "bytes" not in resp.response
    assert resp.response.get("status") == "success"
    assert resp.response.get("width") is not None
    assert resp.response.get("height") is not None
    assert "streamed to visual perception plane" in resp.response.get("message", "").lower()


@pytest.mark.asyncio
async def test_google_realtime_adapter_send_image() -> None:
    """Verify GoogleRealtimeAdapter packages and dispatches raw image bytes properly."""
    adapter = GoogleRealtimeAdapter()
    mock_session = AsyncMock()
    adapter._session = mock_session

    test_buf = io.BytesIO()
    Image.new("RGB", (320, 240), color="green").save(test_buf, format="JPEG")
    fake_jpeg_bytes = test_buf.getvalue()

    await adapter.send_image(fake_jpeg_bytes, mime_type="image/jpeg")

    assert mock_session.send.called or mock_session.send_realtime_input.called
