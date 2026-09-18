"""Regression tests for JSON serialization boundaries in Voice and Realtime Telemetry.

Validates that raw Python UUIDs, datetimes, paths, and complex structures serialize safely:
- Test F: UUID-bearing Live event serializes without error
- Test G: UUID-bearing tool result serializes without error
- Test H: UUID-bearing telemetry span export serializes without error
- Test I: Malformed tool result handled cleanly in receive loop without terminating session
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.core.gateway.realtime import (
    LiveEvent,
    LiveEventType,
    LiveToolCall,
    LiveToolResponse,
)
from jarvis.core.serialization import canonical_json_dumps
from jarvis.core.telemetry.schemas import TelemetrySpan
from jarvis.core.telemetry.tracer import FileSpanExporter


def test_test_f_uuid_bearing_live_event_serializes_without_error() -> None:
    """Test F: LiveEvent with nested raw UUIDs and datetimes in payload serializes cleanly."""
    raw_uuid = uuid4()
    nested_payload = {
        "proposal_id": raw_uuid,
        "task_id": uuid4(),
        "created_at": datetime.now(UTC),
        "target_path": Path("C:/Windows/notepad.exe"),
        "sub_uuids": [uuid4(), uuid4()],
    }

    event = LiveEvent(
        event_type=LiveEventType.TOOL_CALL,
        payload=nested_payload,
    )

    # Validate that Pydantic model validator converted UUIDs to strings
    assert isinstance(event.payload["proposal_id"], str)
    assert event.payload["proposal_id"] == str(raw_uuid)

    # Validate json.dumps succeeds without TypeError
    dumped = canonical_json_dumps(event.model_dump())
    assert isinstance(dumped, str)
    parsed = json.loads(dumped)
    assert parsed["payload"]["proposal_id"] == str(raw_uuid)


def test_test_g_uuid_bearing_tool_result_serializes_without_error() -> None:
    """Test G: LiveToolResponse with UUIDs in its response dict serializes cleanly."""
    raw_uuid = uuid4()
    raw_session = uuid4()

    response = LiveToolResponse(
        call_id="call_test_123",
        name="jarvis_launch_app",
        response={
            "status": "success",
            "task_id": raw_uuid,
            "session_id": raw_session,
            "metadata": {
                "auth_token": uuid4(),
                "nested_path": Path("C:/Users"),
            },
        },
    )

    assert isinstance(response.response["task_id"], str)
    assert response.response["task_id"] == str(raw_uuid)

    # Standard json.dumps test
    serialized = json.dumps(response.response)
    assert str(raw_uuid) in serialized


def test_test_h_uuid_bearing_telemetry_span_export_serializes_without_error(tmp_path: Path) -> None:
    """Test H: Telemetry span attributes bearing raw UUIDs serialize via FileSpanExporter."""
    span = TelemetrySpan(
        name="test_voice_turn",
        attributes={
            "user_uuid": uuid4(),
            "session_id": uuid4(),
            "target_path": Path("C:/logs/jarvis.log"),
            "timestamp": datetime.now(UTC),
        },
    )

    out_file = tmp_path / "telemetry_spans.jsonl"
    exporter = FileSpanExporter(file_path=out_file)
    exporter.export(span)

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "test_voice_turn" in content


@pytest.mark.asyncio
async def test_test_i_malformed_tool_result_handled_cleanly_in_receive_loop() -> None:
    """Test I: An unhandled exception during tool execution produces a safe error response without crash."""
    from unittest.mock import AsyncMock, MagicMock

    from jarvis.core.voice.voice_agent import LiveVoiceAgent

    mock_adapter = MagicMock()
    mock_bridge = MagicMock()
    mock_task_mgr = MagicMock()

    # Configure tool_bridge to raise a TypeError during tool execution
    mock_bridge.execute_tool_call = AsyncMock(
        side_effect=TypeError("Object of type UUID is not JSON serializable")
    )
    mock_adapter.send_tool_response = AsyncMock()

    agent = LiveVoiceAgent(
        session_id="test_session_123",
        adapter=mock_adapter,
        task_manager=mock_task_mgr,
        tool_bridge=mock_bridge,
    )

    # Simulate an incoming tool call event
    bad_tool_call = LiveToolCall(
        call_id="call_error_test",
        name="jarvis_focus_window",
        arguments={"window_title": "chrome"},
    )
    event = LiveEvent(
        event_type=LiveEventType.TOOL_CALL,
        payload=bad_tool_call,
    )

    async def mock_event_stream() -> AsyncIterator[LiveEvent]:
        yield event

    mock_adapter.receive_events.return_value = mock_event_stream()

    # Run the receive loop; it should handle the TypeError gracefully without aborting
    await agent._run_receive_loop()

    # Verify fallback response was sent to the adapter
    mock_adapter.send_tool_response.assert_called_once()
    sent_response = mock_adapter.send_tool_response.call_args[0][0]
    assert sent_response.name == "jarvis_focus_window"
    assert sent_response.response["status"] == "error"
    assert sent_response.response["reason_code"] == "EXECUTION_ERROR"
    assert "Object of type UUID is not JSON serializable" in sent_response.response["error"]
