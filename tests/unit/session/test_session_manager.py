"""Unit tests for SessionManager JSON persistence."""

from pathlib import Path

from jarvis.core.session.models import (
    ConversationTurn,
    SystemLogEntry,
    ToolExecutionRecord,
)
from jarvis.core.session.session_manager import SessionManager


def test_session_creation_and_persistence(tmp_path: Path) -> None:
    """Test creating sessions and verifying sessions.json and conversations.json."""
    sess_file = tmp_path / "sessions.json"
    conv_file = tmp_path / "conversations.json"

    manager = SessionManager(sessions_path=sess_file, conversations_path=conv_file)
    session = manager.create_session(user_id="test_user", session_id="sess_abc123")

    assert session.session_id == "sess_abc123"
    assert session.user_id == "test_user"
    assert session.status == "ACTIVE"

    # Verify files created and populated
    assert sess_file.exists()
    assert conv_file.exists()

    loaded = manager.get_session("sess_abc123")
    assert loaded is not None
    assert loaded.session_id == "sess_abc123"


def test_turn_logging_with_tool_audits_and_system_logs(tmp_path: Path) -> None:
    """Test recording a full turn with tool audits and system logs."""
    sess_file = tmp_path / "sessions.json"
    conv_file = tmp_path / "conversations.json"

    manager = SessionManager(sessions_path=sess_file, conversations_path=conv_file)
    manager.create_session(session_id="sess_turn_test")

    turn = ConversationTurn(
        user_message="what is 2 + 2?",
        assistant_response="Calculated: 2 + 2 = 4",
        specialist="analysis",
        intent="Evaluate math",
        tool_executions=[
            ToolExecutionRecord(
                tool_id="native:calc:evaluate",
                arguments={"expression": "2 + 2"},
                result={"result": 4},
                policy_decision="ALLOW",
                risk_score=0.0,
                verified=True,
                duration_ms=5.2,
            )
        ],
        system_logs=[
            SystemLogEntry(component="gateway", message="Received user message"),
            SystemLogEntry(component="policy_engine", message="Policy decision ALLOW"),
            SystemLogEntry(component="action_broker", message="Executed tool successfully"),
        ],
    )

    manager.add_turn("sess_turn_test", turn)

    # Verify conversation retrieved
    conv = manager.get_conversation("sess_turn_test")
    assert conv is not None
    assert len(conv.turns) == 1
    assert conv.turns[0].user_message == "what is 2 + 2?"
    assert len(conv.turns[0].tool_executions) == 1
    assert conv.turns[0].tool_executions[0].tool_id == "native:calc:evaluate"
    assert len(conv.turns[0].system_logs) == 3

    # Verify session summary updated
    sess = manager.get_session("sess_turn_test")
    assert sess is not None
    assert sess.turns_count == 1
    assert sess.tasks_executed == 1

    # Close session
    manager.close_session("sess_turn_test")
    closed = manager.get_session("sess_turn_test")
    assert closed is not None
    assert closed.status == "COMPLETED"
