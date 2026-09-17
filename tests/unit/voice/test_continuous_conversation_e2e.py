"""End-to-End multi-turn conversational simulation of the Realtime Voice Plane."""

import asyncio
from typing import Any

import pytest

from jarvis.core.gateway.mock_realtime import MockRealtimeAdapter
from jarvis.core.state.status import TaskStatus
from jarvis.core.voice.task_manager import BackgroundTaskManager
from jarvis.core.voice.tool_bridge import LiveToolBridge
from jarvis.core.voice.voice_agent import LiveVoiceAgent


@pytest.mark.asyncio
async def test_continuous_conversation_e2e_full_workflow() -> None:
    """Full end-to-end simulation of multi-turn voice interaction, background task, and barge-in."""
    adapter = MockRealtimeAdapter()
    task_manager = BackgroundTaskManager()

    executed_plans: list[str] = []

    async def simulated_work_executor(capability: str, title: str) -> dict[str, Any]:
        executed_plans.append(f"{capability}:{title}")
        # Truthful progress steps
        await asyncio.sleep(0.1)
        return {"result": f"Executed {title} with precision."}

    bridge = LiveToolBridge(
        task_manager=task_manager,
        work_executor=simulated_work_executor,
    )

    agent = LiveVoiceAgent(
        session_id="sess_e2e_001",
        adapter=adapter,
        task_manager=task_manager,
        tool_bridge=bridge,
    )

    # 1. User connects
    await agent.start()
    assert agent.session_manager.current_session is not None
    assert agent.session_manager.current_session.is_active is True

    # 2. Turn 1: User says good morning
    await agent.send_user_text("Good morning JARVIS.")
    adapter.queue_speech("Good morning Deepak. Systems are nominal and ready.")

    # 3. Turn 2: User commands a deep refactoring background task
    await agent.send_user_text("Please refactor our payment integration modules in the background.")
    adapter.queue_tool_call(
        name="jarvis_code",
        arguments={"instruction": "Refactor payment integration modules"},
    )

    # Allow receive loop to execute tool proposal and launch background task
    await asyncio.sleep(0.05)

    # Verify background task was dispatched
    tasks = task_manager.list_tasks(session_id="sess_e2e_001")
    assert len(tasks) == 1
    bg_task = tasks[0]
    assert bg_task.title == "Refactor payment integration modules"
    assert bg_task.is_running is True

    # 4. Turn 3: Unrelated conversation while task is actively running
    # User streams speech audio and asks about physics
    mic_audio = b"\x03\x00" * 320
    await agent.send_user_speech(mic_audio)
    await agent.send_user_text("While that's running, can you explain wave-particle duality?")

    adapter.queue_speech(
        "Wave-particle duality states that matter exhibits both wave and particle properties."
    )

    # 5. Turn 4: User interrupts / barge-in mid-speech
    await agent.interrupt()
    assert adapter.interrupt_count == 1

    # 6. Wait for background task completion
    await asyncio.sleep(0.2)
    assert bg_task.status == TaskStatus.COMPLETED
    assert len(executed_plans) == 1
    assert "Refactor payment integration modules" in executed_plans[0]

    # 7. Turn 5: User inquires about the status of the completed task
    cid2 = adapter.queue_tool_call(
        name="jarvis_get_task_status",
        arguments={"task_id": bg_task.task_id},
    )
    await asyncio.sleep(0.05)

    # Verify tool response was recorded
    status_responses = [r for r in adapter.sent_tool_responses if r.call_id == cid2]
    assert len(status_responses) == 1
    assert status_responses[0].response["status"] == TaskStatus.COMPLETED.value
    assert "Executed Refactor payment integration modules" in str(
        status_responses[0].response["result"]
    )

    # 8. User gracefully closes session
    await agent.stop()
    assert agent.session_manager.current_session is None
