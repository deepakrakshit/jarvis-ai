"""Chaos and fault injection tests for the Realtime Voice Plane."""

import asyncio
from unittest.mock import MagicMock

import pytest

from jarvis.core.gateway.mock_realtime import MockRealtimeAdapter
from jarvis.core.gateway.realtime import (
    LiveToolCall,
)
from jarvis.core.policy.decision import PolicyDecision, PolicyDecisionType
from jarvis.core.policy.engine import PolicyEngine
from jarvis.core.state.status import TaskStatus
from jarvis.core.voice.task_manager import BackgroundTaskManager
from jarvis.core.voice.tool_bridge import LiveToolBridge
from jarvis.core.voice.voice_agent import LiveVoiceAgent


@pytest.mark.asyncio
async def test_chaos_duplicate_tool_call_flood() -> None:
    """Verify high-velocity duplicate tool calls are deduplicated and suppressed."""
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    call = LiveToolCall(
        call_id="flood_call_001",
        name="jarvis_chat",
        arguments={"message": "System status check"},
    )

    # First call succeeds
    resp1 = await bridge.execute_tool_call(call, session_id="sess_chaos_001")
    assert resp1.response.get("status") == "delivered"

    # Subsequent identical flood calls are caught
    for _ in range(10):
        resp_dup = await bridge.execute_tool_call(call, session_id="sess_chaos_001")
        assert resp_dup.response.get("status") == "duplicate"
        assert resp_dup.scheduling == "SILENT"


@pytest.mark.asyncio
async def test_chaos_policy_blocks_dangerous_proposals() -> None:
    """Verify untrusted voice proposals attempting dangerous actions are stopped by policy."""
    task_manager = BackgroundTaskManager()
    mock_policy = MagicMock(spec=PolicyEngine)
    mock_policy.evaluate_invocation.return_value = PolicyDecision(
        decision=PolicyDecisionType.DENY,
        reason="Execution of unbounded system mutation blocked by security policy",
        risk_score=0.95,
    )

    bridge = LiveToolBridge(
        task_manager=task_manager,
        policy_engine=mock_policy,
    )

    call = LiveToolCall(
        call_id="malicious_call_001",
        name="jarvis_code",
        arguments={"instruction": "rm -rf /"},
    )

    resp = await bridge.execute_tool_call(call, session_id="sess_chaos_002")
    assert "error" in resp.response
    assert "Security policy blocked action" in resp.response["error"]

    # Verify no tasks were spawned
    assert len(task_manager.list_tasks()) == 0


@pytest.mark.asyncio
async def test_chaos_background_task_exception_resilience() -> None:
    """Verify unhandled exceptions in background workers do not crash the voice session."""
    adapter = MockRealtimeAdapter()
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    agent = LiveVoiceAgent(
        session_id="sess_chaos_003",
        adapter=adapter,
        task_manager=task_manager,
        tool_bridge=bridge,
    )

    await agent.start()

    async def explosive_worker() -> None:
        await asyncio.sleep(0.05)
        raise RuntimeError("Catastrophic disk corruption simulation")

    task = await task_manager.submit_task(
        title="Volatile operation",
        session_id="sess_chaos_003",
        coro_fn=explosive_worker,
    )

    # Wait for failure to process
    await asyncio.sleep(0.15)

    assert task.status == TaskStatus.FAILED
    assert task.error is not None
    assert "Catastrophic disk corruption" in task.error

    # Verify voice session is completely healthy and responsive
    assert agent.session_manager.current_session is not None
    assert agent.session_manager.current_session.is_active is True

    # User can continue speaking
    await agent.send_user_text("Are you still there JARVIS?")
    assert "Are you still there JARVIS?" in adapter.sent_texts

    await agent.stop()


@pytest.mark.asyncio
async def test_chaos_mid_conversation_goaway_rotation() -> None:
    """Verify GoAway received during active dialogue transparently preserves conversation."""
    adapter = MockRealtimeAdapter()
    task_manager = BackgroundTaskManager()
    bridge = LiveToolBridge(task_manager=task_manager)

    agent = LiveVoiceAgent(
        session_id="sess_chaos_004",
        adapter=adapter,
        task_manager=task_manager,
        tool_bridge=bridge,
    )

    await agent.start()

    # User starts talking
    await agent.send_user_text("Hello JARVIS")

    # Mid-stream provider emits GoAway signal
    adapter.queue_go_away(time_left_seconds=15.0)

    # Allow event loop to process GoAway
    await asyncio.sleep(0.1)

    # Verify session is still active and connection was rotated
    session = agent.session_manager.current_session
    assert session is not None
    assert session.is_active is True
    assert len(session.connection_history) == 2

    # Verify subsequent user speech continues without issue
    await agent.send_user_text("Did you catch that?")
    assert "Did you catch that?" in adapter.sent_texts

    await agent.stop()
