"""Canonical Task Contracts for JARVIS Control Plane.

Implements the explicit, durable Task State Machine and typed schemas.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


class TaskState(str, Enum):
    """Authoritative durable task states."""

    CREATED = "CREATED"
    NORMALIZING = "NORMALIZING"
    CLASSIFIED = "CLASSIFIED"
    CONTEXT_READY = "CONTEXT_READY"
    PLANNED = "PLANNED"
    POLICY_CHECK = "POLICY_CHECK"
    AUTHORIZED = "AUTHORIZED"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    OBSERVING = "OBSERVING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"

    # Alternative / Terminal States
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    BLOCKED = "BLOCKED"
    WAITING = "WAITING"
    RETRYING = "RETRYING"
    RECOVERING = "RECOVERING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class TaskPriority(str, Enum):
    """Task priority classification."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TaskType(str, Enum):
    """Task execution category."""

    CONVERSATION = "CONVERSATION"
    RESEARCH = "RESEARCH"
    CODING = "CODING"
    WINDOWS_CONTROL = "WINDOWS_CONTROL"
    BROWSER_AUTOMATION = "BROWSER_AUTOMATION"
    BACKGROUND_WORKER = "BACKGROUND_WORKER"
    SYSTEM_MAINTENANCE = "SYSTEM_MAINTENANCE"


class Task(BaseModel):
    """Durable representation of a JARVIS task."""

    task_id: str = Field(default_factory=lambda: f"TASK-{uuid4().hex[:12].upper()}")
    session_id: str
    parent_task_id: Optional[str] = None
    task_type: TaskType = TaskType.CONVERSATION
    priority: TaskPriority = TaskPriority.NORMAL
    state: TaskState = TaskState.CREATED

    # Goal & Input
    raw_intent: str
    normalized_goal: Optional[str] = None
    input_payload: Dict[str, Any] = Field(default_factory=dict)

    # Execution Metadata
    assigned_model: Optional[str] = None
    assigned_agent: Optional[str] = None
    token_budget: int = Field(default=8000)
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=3)

    # State History & Audit
    state_history: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None

    # Results & Observations
    result_summary: Optional[str] = None
    verification_passed: Optional[bool] = None
    error_message: Optional[str] = None

    def transition_to(self, new_state: TaskState, reason: str = "") -> None:
        """Atomically transition task state with recorded audit history."""
        now = utc_now()
        transition_record = {
            "from_state": self.state.value,
            "to_state": new_state.value,
            "reason": reason,
            "timestamp": now.isoformat(),
        }
        self.state = new_state
        self.state_history.append(transition_record)
        self.updated_at = now
        if new_state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
            self.completed_at = now
