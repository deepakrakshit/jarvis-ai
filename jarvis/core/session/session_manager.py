"""JARVIS Session and Conversation Logging Manager.

Persists session metadata to sessions.json and complete multi-turn conversation logs
with fine-grained system logs to conversations.json.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from jarvis.core.logging import get_logger
from jarvis.core.session.models import (
    ConversationRecord,
    ConversationTurn,
    SessionRecord,
)

logger = get_logger(__name__)


class SessionManager:
    """Thread-safe session manager maintaining sessions.json and conversations.json."""

    def __init__(
        self,
        sessions_path: Path | str | None = None,
        conversations_path: Path | str | None = None,
    ) -> None:
        self.sessions_path = Path(sessions_path or "sessions.json").resolve()
        self.conversations_path = Path(conversations_path or "conversations.json").resolve()
        self._lock = Lock()
        self._ensure_files()

    def _ensure_files(self) -> None:
        """Ensure json files exist with valid json arrays."""
        with self._lock:
            if not self.sessions_path.exists():
                self.sessions_path.parent.mkdir(parents=True, exist_ok=True)
                self.sessions_path.write_text("[]", encoding="utf-8")
            if not self.conversations_path.exists():
                self.conversations_path.parent.mkdir(parents=True, exist_ok=True)
                self.conversations_path.write_text("[]", encoding="utf-8")

    def _read_sessions(self) -> list[dict[str, Any]]:
        try:
            content = self.sessions_path.read_text(encoding="utf-8")
            return json.loads(content) if content.strip() else []
        except Exception:
            return []

    def _write_sessions(self, sessions: list[dict[str, Any]]) -> None:
        temp_file = self.sessions_path.with_suffix(".tmp")
        temp_file.write_text(json.dumps(sessions, indent=2), encoding="utf-8")
        temp_file.replace(self.sessions_path)

    def _read_conversations(self) -> list[dict[str, Any]]:
        try:
            content = self.conversations_path.read_text(encoding="utf-8")
            return json.loads(content) if content.strip() else []
        except Exception:
            return []

    def _write_conversations(self, conversations: list[dict[str, Any]]) -> None:
        temp_file = self.conversations_path.with_suffix(".tmp")
        temp_file.write_text(json.dumps(conversations, indent=2), encoding="utf-8")
        temp_file.replace(self.conversations_path)

    def create_session(
        self,
        user_id: str = "default_user",
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        """Create and persist a new session record."""
        sid = session_id or f"sess_{uuid4().hex[:16]}"
        now = datetime.now(UTC).isoformat()
        record = SessionRecord(
            session_id=sid,
            created_at=now,
            updated_at=now,
            status="ACTIVE",
            user_id=user_id,
            turns_count=0,
            tasks_executed=0,
            metadata=metadata or {},
        )

        with self._lock:
            sessions = self._read_sessions()
            sessions.append(record.model_dump())
            self._write_sessions(sessions)

            # Initialize empty conversation record
            convs = self._read_conversations()
            convs.append({"session_id": sid, "turns": []})
            self._write_conversations(convs)

        logger.info("session_created", session_id=sid, user_id=user_id)
        return record

    def add_turn(self, session_id: str, turn: ConversationTurn) -> None:
        """Append a completed turn (with tool audits & system logs) to a session."""
        with self._lock:
            # 1. Update conversations.json
            convs = self._read_conversations()
            found = False
            for conv in convs:
                if conv.get("session_id") == session_id:
                    conv.setdefault("turns", []).append(turn.model_dump(mode="json"))
                    found = True
                    break
            if not found:
                convs.append({"session_id": session_id, "turns": [turn.model_dump(mode="json")]})
            self._write_conversations(convs)

            # 2. Update sessions.json
            sessions = self._read_sessions()
            now = datetime.now(UTC).isoformat()
            has_tasks = len(turn.tool_executions) > 0
            for sess in sessions:
                if sess.get("session_id") == session_id:
                    sess["updated_at"] = now
                    sess["turns_count"] = sess.get("turns_count", 0) + 1
                    if has_tasks:
                        sess["tasks_executed"] = sess.get("tasks_executed", 0) + len(
                            turn.tool_executions
                        )
                    break
            self._write_sessions(sessions)

        logger.info("conversation_turn_logged", session_id=session_id, turn_id=str(turn.turn_id))

    def get_session(self, session_id: str) -> SessionRecord | None:
        """Get session metadata by ID."""
        with self._lock:
            for s in self._read_sessions():
                if s.get("session_id") == session_id:
                    return SessionRecord(**s)
            return None

    def get_conversation(self, session_id: str) -> ConversationRecord | None:
        """Get full conversation history for a session."""
        with self._lock:
            for c in self._read_conversations():
                if c.get("session_id") == session_id:
                    return ConversationRecord(**c)
            return None

    def list_sessions(self) -> list[SessionRecord]:
        """List all registered sessions."""
        with self._lock:
            return [SessionRecord(**s) for s in self._read_sessions()]

    def close_session(self, session_id: str) -> None:
        """Mark session as COMPLETED."""
        with self._lock:
            sessions = self._read_sessions()
            now = datetime.now(UTC).isoformat()
            for s in sessions:
                if s.get("session_id") == session_id:
                    s["status"] = "COMPLETED"
                    s["updated_at"] = now
                    break
            self._write_sessions(sessions)
        logger.info("session_closed", session_id=session_id)
