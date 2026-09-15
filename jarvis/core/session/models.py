"""JARVIS Session and Conversation Logging Models.

Defines the structured schemas for sessions.json and conversations.json,
including point-in-time system logs and execution audit records.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class SystemLogEntry(BaseModel):
    """An individual structured system log event for a session turn."""

    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    level: str = "INFO"
    component: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionRecord(BaseModel):
    """Audited execution record of an individual tool call within a turn."""

    tool_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    policy_decision: str = "ALLOW"
    risk_score: float = 0.0
    result: Any = None
    verified: bool = True
    duration_ms: float = 0.0


class ConversationTurn(BaseModel):
    """A single user-assistant exchange with full tool executions and system logs."""

    turn_id: UUID = Field(default_factory=uuid4)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    user_message: str
    assistant_response: str
    specialist: str
    intent: str
    needs_clarification: bool = False
    clarification_question: str | None = None
    tool_executions: list[ToolExecutionRecord] = Field(default_factory=list)
    system_logs: list[SystemLogEntry] = Field(default_factory=list)


class SessionRecord(BaseModel):
    """Metadata summary of an active or completed JARVIS session."""

    session_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: str = "ACTIVE"
    user_id: str = "default_user"
    turns_count: int = 0
    tasks_executed: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationRecord(BaseModel):
    """Complete history of all turns for a specific session."""

    session_id: str
    turns: list[ConversationTurn] = Field(default_factory=list)
