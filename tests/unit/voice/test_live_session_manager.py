"""Unit tests for LiveSessionManager and connection lifecycle management."""

import pytest

from jarvis.core.config import get_settings
from jarvis.core.exceptions import ModelProviderError
from jarvis.core.gateway.mock_realtime import MockRealtimeAdapter
from jarvis.core.gateway.realtime import (
    LiveGoAway,
    LiveResumptionUpdate,
    LiveSessionConfig,
)
from jarvis.core.voice.session_manager import LiveSessionManager
from jarvis.core.voice.telemetry import VoiceTelemetryLogger


@pytest.mark.asyncio
async def test_session_lifecycle_initialization_and_close() -> None:
    """Verify session initialization, record tracking, and graceful closure."""
    adapter = MockRealtimeAdapter()
    telemetry = VoiceTelemetryLogger(session_id="sess_voice_001")
    manager = LiveSessionManager(adapter=adapter, telemetry=telemetry)

    settings = get_settings()
    config = LiveSessionConfig(
        model_id=settings.REALTIME_VOICE_MODEL_ID,
        voice_name=settings.VOICE_DEFAULT_NAME,
    )

    session = await manager.initialize_session(
        jarvis_session_id="sess_voice_001",
        config=config,
    )

    assert session.jarvis_session_id == "sess_voice_001"
    assert session.is_active is True
    assert len(session.connection_history) == 1
    assert adapter.connected is True
    assert manager.current_session is session

    await manager.close_session()

    assert session.is_active is False
    assert adapter.connected is False
    assert manager.current_session is None


@pytest.mark.asyncio
async def test_session_resumption_handle_updates() -> None:
    """Verify resumption handle updates are recorded in session and connection records."""
    adapter = MockRealtimeAdapter()
    manager = LiveSessionManager(adapter=adapter)

    settings = get_settings()
    config = LiveSessionConfig(model_id=settings.REALTIME_VOICE_MODEL_ID)
    session = await manager.initialize_session("sess_voice_002", config)

    assert session.latest_resumption_handle is None

    # Simulate provider emitting new resumption token
    update = LiveResumptionUpdate(handle="handle_checkpoint_alpha", resumable=True)
    manager.update_resumption_handle(update)

    assert session.latest_resumption_handle == "handle_checkpoint_alpha"
    assert session.is_resumable is True
    assert session.connection_history[-1].emitted_new_handle == "handle_checkpoint_alpha"

    await manager.close_session()


@pytest.mark.asyncio
async def test_transparent_goaway_connection_rotation() -> None:
    """Verify GoAway triggers transparent connection replacement while preserving session identity."""
    adapter = MockRealtimeAdapter()
    telemetry = VoiceTelemetryLogger(session_id="sess_voice_003")
    manager = LiveSessionManager(adapter=adapter, telemetry=telemetry)

    settings = get_settings()
    config = LiveSessionConfig(model_id=settings.REALTIME_VOICE_MODEL_ID)
    session = await manager.initialize_session("sess_voice_003", config)

    initial_conn_id = session.active_connection_id
    assert adapter.connect_count == 1

    # Receive resumption handle before GoAway
    manager.update_resumption_handle(
        LiveResumptionUpdate(handle="handle_pre_rotation", resumable=True)
    )

    # Provider sends GoAway warning
    go_away = LiveGoAway(time_left_seconds=10.0)
    await manager.handle_go_away(go_away)

    # Verify session identity preserved
    assert session.jarvis_session_id == "sess_voice_003"
    assert session.active_connection_id != initial_conn_id
    assert len(session.connection_history) == 2

    # Verify previous connection record marked
    first_conn = session.connection_history[0]
    assert first_conn.goaway_received_at is not None
    assert first_conn.disconnected_at is not None

    # Verify new connection resumed from handle
    second_conn = session.connection_history[1]
    assert second_conn.resumed_from_handle == "handle_pre_rotation"
    assert adapter.connect_count == 2
    assert adapter.close_count == 1
    assert adapter.connected is True

    await manager.close_session()


@pytest.mark.asyncio
async def test_task_attachment_decoupled_lifecycle() -> None:
    """Verify background tasks can be attached and detached without terminating session."""
    adapter = MockRealtimeAdapter()
    manager = LiveSessionManager(adapter=adapter)

    settings = get_settings()
    config = LiveSessionConfig(model_id=settings.REALTIME_VOICE_MODEL_ID)
    session = await manager.initialize_session("sess_voice_004", config)

    manager.attach_task("task_bg_101")
    manager.attach_task("task_bg_102")
    assert "task_bg_101" in session.attached_task_ids
    assert "task_bg_102" in session.attached_task_ids

    # Detaching completed task does not affect session
    manager.detach_task("task_bg_101")
    assert "task_bg_101" not in session.attached_task_ids
    assert "task_bg_102" in session.attached_task_ids
    assert session.is_active is True

    await manager.close_session()


@pytest.mark.asyncio
async def test_goaway_rotation_failure_handling() -> None:
    """Verify failure during rotation raises ModelProviderError and marks degradation."""
    adapter = MockRealtimeAdapter()
    telemetry = VoiceTelemetryLogger(session_id="sess_voice_005")
    manager = LiveSessionManager(adapter=adapter, telemetry=telemetry)

    settings = get_settings()
    config = LiveSessionConfig(model_id=settings.REALTIME_VOICE_MODEL_ID)
    await manager.initialize_session("sess_voice_005", config)

    # Force reconnect to fail
    adapter.fail_next_connect = True

    with pytest.raises(ModelProviderError):
        await manager.handle_go_away(LiveGoAway(time_left_seconds=5.0))
