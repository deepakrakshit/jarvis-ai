"""Unit tests verifying continuous conversational coexistence with active background execution."""

import asyncio

import pytest

from jarvis.core.gateway.mock_realtime import MockRealtimeAdapter
from jarvis.core.state.status import TaskStatus
from jarvis.core.voice.task_manager import BackgroundTaskManager
from jarvis.core.voice.tool_bridge import LiveToolBridge
from jarvis.core.voice.voice_agent import LiveVoiceAgent


@pytest.mark.asyncio
async def test_continuous_conversation_during_background_task() -> None:
    """Verify user can have live voice conversation while an autonomous task executes."""
    adapter = MockRealtimeAdapter()
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    agent = LiveVoiceAgent(
        session_id="sess_coexist_001",
        adapter=adapter,
        task_manager=task_manager,
        tool_bridge=bridge,
    )

    await agent.start()

    # Launch a simulated 1-second background task
    task_finished = asyncio.Event()

    async def long_running_worker() -> str:
        await asyncio.sleep(0.3)
        task_finished.set()
        return "Worker finished"

    task = await task_manager.submit_task(
        title="Analyze code repository",
        session_id="sess_coexist_001",
        coro_fn=long_running_worker,
    )

    assert task.is_running is True

    # User speaks / sends text while task is running
    await agent.send_user_text("What is the capital of France?")
    assert "What is the capital of France?" in adapter.sent_texts

    # User streams audio while task is running
    mic_chunk = b"\x10\x00" * 320
    await agent.send_user_speech(mic_chunk)
    assert mic_chunk in adapter.sent_audio

    # Wait for task completion
    await asyncio.wait_for(task_finished.wait(), timeout=2.0)
    assert task.status == TaskStatus.COMPLETED

    # Verify voice session is still completely alive and active
    assert agent.session_manager.current_session is not None
    assert agent.session_manager.current_session.is_active is True

    await agent.stop()


@pytest.mark.asyncio
async def test_milestone_progress_narration_and_throttling() -> None:
    """Verify truthful milestone notifications are passed to the voice model with throttling."""
    adapter = MockRealtimeAdapter()
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    agent = LiveVoiceAgent(
        session_id="sess_coexist_002",
        adapter=adapter,
        task_manager=task_manager,
        tool_bridge=bridge,
    )
    agent.proactive_throttle_seconds = 0.5  # Fast throttle for test

    await agent.start()

    task_step_1 = asyncio.Event()
    task_step_2 = asyncio.Event()

    async def multi_step_worker() -> dict[str, str]:
        await task_manager.update_progress(task.task_id, "Parsing dependencies", phase="ANALYSIS")
        task_step_1.set()
        # Immediate second update within throttle window
        await task_manager.update_progress(task.task_id, "Parsing AST", phase="PARSING")
        await asyncio.sleep(0.6)  # Exceed throttle window
        await task_manager.update_progress(task.task_id, "Building graphs", phase="GRAPHING")
        task_step_2.set()
        return {"result": "success"}

    task = await task_manager.submit_task(
        title="Multi-phase compilation",
        session_id="sess_coexist_002",
        coro_fn=multi_step_worker,
    )

    await asyncio.wait_for(task_step_2.wait(), timeout=3.0)
    await asyncio.sleep(0.1)

    # Inspect messages sent to voice model
    notifications = [t for t in adapter.sent_texts if "[System Progress Update" in t]
    assert len(notifications) >= 2
    # Verify intermediate rapid update was throttled
    assert not any("Parsing dependencies" in n for n in notifications)
    # Verify throttled window expiration allowed subsequent update
    assert any("Building graphs" in n for n in notifications)
    # Verify terminal completion notification bypasses throttle
    assert any("has completed successfully" in n for n in notifications)

    await agent.stop()


@pytest.mark.asyncio
async def test_conversational_task_cancellation_and_barge_in() -> None:
    """Verify task cancellation via voice tool and barge-in interruption."""
    adapter = MockRealtimeAdapter()
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    agent = LiveVoiceAgent(
        session_id="sess_coexist_003",
        adapter=adapter,
        task_manager=task_manager,
        tool_bridge=bridge,
    )

    await agent.start()

    task_started = asyncio.Event()
    cancelled_event = asyncio.Event()

    async def infinite_worker() -> None:
        task_started.set()
        try:
            while True:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            cancelled_event.set()
            raise

    task = await task_manager.submit_task(
        title="Infinite calculation",
        session_id="sess_coexist_003",
        coro_fn=infinite_worker,
    )

    # Ensure worker is actively running before requesting cancellation
    await asyncio.wait_for(task_started.wait(), timeout=2.0)

    # Model proposes jarvis_cancel_task tool call
    adapter.queue_tool_call(
        name="jarvis_cancel_task",
        arguments={"task_id": task.task_id},
    )

    # Wait for cancellation event
    await asyncio.wait_for(cancelled_event.wait(), timeout=2.0)

    assert task.status == TaskStatus.CANCELED

    # User interrupts spoken response
    await agent.interrupt()
    assert adapter.interrupt_count == 1

    await agent.stop()
