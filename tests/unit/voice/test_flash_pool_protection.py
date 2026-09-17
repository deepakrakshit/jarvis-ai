"""Unit tests verifying Flash Pool protection against depletion by ordinary voice turns."""

import pytest

from jarvis.core.gateway.mock_realtime import MockRealtimeAdapter
from jarvis.core.gateway.quota import QuotaMeterType
from jarvis.core.gateway.router import POOL_A_FLASH_QUALITY, ModelGateway
from jarvis.core.voice.task_manager import BackgroundTaskManager
from jarvis.core.voice.tool_bridge import LiveToolBridge
from jarvis.core.voice.voice_agent import LiveVoiceAgent


@pytest.mark.asyncio
async def test_twenty_voice_turns_never_consume_flash_pool() -> None:
    """Verify 20+ continuous voice interactions consume zero requests from Pool A Flash models."""
    gateway = ModelGateway()

    # Pre-initialize quota managers for all Flash Pool A models
    flash_managers = {model: gateway.get_quota_manager(model) for model in POOL_A_FLASH_QUALITY}
    flash_25 = gateway.get_quota_manager("gemini-2.5-flash")
    flash_managers["gemini-2.5-flash"] = flash_25

    # Confirm baseline initial state has zero usage
    for model_id, qm in flash_managers.items():
        assert qm.requests_today == 0, f"Initial count for {model_id} must be 0"

    adapter = MockRealtimeAdapter(default_model_id="gemini-3.8-live")
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    agent = LiveVoiceAgent(
        session_id="sess_flash_protect_001",
        adapter=adapter,
        task_manager=task_manager,
        tool_bridge=bridge,
        model_id="gemini-3.8-live",
    )

    await agent.start()

    # Simulate 25 continuous ordinary voice turns
    # Alternating text turns, audio input streams, status checks, and casual questions
    for turn_idx in range(1, 26):
        # 1. Spoken audio chunk from microphone
        mic_pcm = b"\x05\x00" * 320  # 20ms of audio
        await agent.send_user_speech(mic_pcm, end_of_turn=False)

        # 2. User utterance
        await agent.send_user_text(f"Voice turn #{turn_idx}: How are things running?")

        # 3. Simulate model conversational reply
        adapter.queue_speech(f"Reply for turn #{turn_idx}: Everything is operating smoothly.")

    # Execute voice tool queries through the LiveToolBridge
    adapter.queue_tool_call(
        name="jarvis_system_status",
        arguments={"include_tasks": True},
    )

    # Allow receive loop to process tool calls and events
    import asyncio

    await asyncio.sleep(0.15)

    # Rigorous Invariant Verification:
    # Under NO circumstances should ordinary voice turns burn scarce Flash Pool A requests.
    for model_id, qm in flash_managers.items():
        assert qm.requests_today == 0, (
            f"VIOLATION: Voice interaction consumed {qm.requests_today} requests from {model_id}"
        )
        assert len(qm.active_leases) == 0

    # Verify voice telemetry recorded the interactions
    turn_events = [e for e in agent.telemetry.event_history if e["event"] == "live_turn_started"]
    assert len(turn_events) >= 25
    assert agent.telemetry.metrics.total_audio_input_seconds > 0.0
    assert agent.telemetry.metrics.total_tool_calls >= 1

    # Verify Live model meter type is unmetered/reported, not restricted Flash RPD
    live_qm = gateway.get_quota_manager("gemini-3.8-live")
    assert live_qm.meter_type == QuotaMeterType.UNMETERED_REPORTED

    await agent.stop()
