"""Integration test executing real tasks and questions with full session JSON logging."""

from pathlib import Path

import pytest

from jarvis.core.policy.decision import AutonomyLevel
from jarvis.core.session.session_manager import SessionManager
from jarvis.orchestrator import JarvisOrchestrator


@pytest.mark.asyncio
async def test_orchestrator_executes_real_tasks_and_records_logs(tmp_path: Path) -> None:
    """Test executing real tasks and clarifying questions, verifying full audit records."""
    sess_file = tmp_path / "sessions.json"
    conv_file = tmp_path / "conversations.json"

    session_mgr = SessionManager(sessions_path=sess_file, conversations_path=conv_file)
    session = session_mgr.create_session(user_id="test_operator", session_id="sess_live_001")

    orchestrator = JarvisOrchestrator(
        workspace_root=Path.cwd(),
        autonomy_level=AutonomyLevel.AUTO_BOUNDED_MUTATION,
        session_manager=session_mgr,
    )

    events_observed: list[str] = []

    def on_progress(comp: str, msg: str) -> None:
        events_observed.append(f"[{comp}] {msg}")

    # --- Turn 1: Real Task: Calculation ---
    res1 = await orchestrator.interact(
        session_id=session.session_id,
        user_message="calculate sqrt(144) * 5",
        on_progress=on_progress,
    )
    assert "60.0" in res1
    assert any("[action_broker]" in ev for ev in events_observed)

    # --- Turn 2: Real Task: Time & Clock Query ---
    res2 = await orchestrator.interact(
        session_id=session.session_id,
        user_message="what is the current time and date?",
        on_progress=on_progress,
    )
    assert "Current Time" in res2
    assert "UTC" in res2

    # --- Turn 3: Real Task: File Inspection ---
    res3 = await orchestrator.interact(
        session_id=session.session_id,
        user_message="read file pyproject.toml",
        on_progress=on_progress,
    )
    assert "pyproject.toml" in res3
    assert "jarvis" in res3.lower()

    # --- Turn 4: Ambiguous Request -> Asking Question ---
    res4 = await orchestrator.interact(
        session_id=session.session_id,
        user_message="can you read file?",
        on_progress=on_progress,
    )
    assert "Which file would you like me to read?" in res4

    # --- Turn 5: Personal Note ---
    res5 = await orchestrator.interact(
        session_id=session.session_id,
        user_message="remind me to review the security policy audit tomorrow",
        on_progress=on_progress,
    )
    assert "review the security policy audit" in res5

    # --- Verify Full JSON Persistence & Structure ---
    session_mgr.close_session(session.session_id)

    # 1. Inspect sessions.json
    saved_session = session_mgr.get_session(session.session_id)
    assert saved_session is not None
    assert saved_session.status == "COMPLETED"
    assert saved_session.turns_count == 5
    assert saved_session.tasks_executed == 3  # Turns 1, 2, 3 executed tools

    # 2. Inspect conversations.json
    saved_conv = session_mgr.get_conversation(session.session_id)
    assert saved_conv is not None
    assert len(saved_conv.turns) == 5

    # Check Turn 1 audit
    turn1 = saved_conv.turns[0]
    assert turn1.specialist == "analysis"
    assert len(turn1.tool_executions) == 1
    assert turn1.tool_executions[0].tool_id == "native:calc:evaluate"
    assert turn1.tool_executions[0].verified is True
    assert len(turn1.system_logs) >= 5

    # Check Turn 4 question audit
    turn4 = saved_conv.turns[3]
    assert turn4.needs_clarification is True
    assert (
        turn4.clarification_question
        == "Which file would you like me to read? Please specify the file name or relative path."
    )
    assert len(turn4.tool_executions) == 0

    # Prove system logs exist for every turn to verify what was actually performed
    for turn in saved_conv.turns:
        assert len(turn.system_logs) > 0
        components = [log.component for log in turn.system_logs]
        assert "gateway" in components
        assert "router" in components
        assert "specialist" in components
