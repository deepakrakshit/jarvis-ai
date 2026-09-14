"""JARVIS Core Task Domain Model and Budget Primitives.

Defines immutable task creation events, operational budgets, and strict state progression.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from jarvis.core.state.correlation import CorrelationContext
from jarvis.core.state.status import (
    TaskStatus,
    is_terminal_status,
    validate_transition,
)


class TaskBudget(BaseModel):
    """Operational resource and execution limits allocated to a task."""

    max_wall_time_seconds: float = Field(default=600.0, ge=1.0)
    max_graph_steps: int = Field(default=50, ge=1, le=500)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None

    def start(self) -> None:
        """Mark task execution start timestamp."""
        if self.started_at is None:
            self.started_at = datetime.now(UTC)

    def is_wall_time_exhausted(self) -> bool:
        """Check if elapsed wall-clock time exceeds budget limit."""
        if self.started_at is None:
            return False
        elapsed = (datetime.now(UTC) - self.started_at).total_seconds()
        return elapsed >= self.max_wall_time_seconds

    def is_step_budget_exhausted(self, current_steps: int) -> bool:
        """Check if total executed graph steps have reached or exceeded the ceiling."""
        return current_steps >= self.max_graph_steps


class TaskEvent(BaseModel):
    """Immutable audit record of a state transition or control plane event."""

    event_id: str = Field(default_factory=lambda: f"evt-{uuid4()}")
    task_id: str
    event_type: str
    from_status: TaskStatus | None = None
    to_status: TaskStatus | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class JarvisTask(BaseModel):
    """Canonical domain representation of a task in JARVIS v1.0.0."""

    task_id: str = Field(default_factory=lambda: f"task-{uuid4()}")
    user_id: str = "default-user"
    session_id: str = Field(default_factory=lambda: f"sess-{uuid4()}")
    parent_task_id: str | None = None
    title: str = "Untitled Task"
    input_prompt: str
    status: TaskStatus = TaskStatus.CREATED
    budget: TaskBudget = Field(default_factory=TaskBudget)
    correlation: CorrelationContext = Field(default_factory=CorrelationContext)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        # Synchronize correlation task_id and session_id with task fields
        if "correlation" not in data:
            self.correlation = CorrelationContext(
                task_id=self.task_id,
                session_id=self.session_id,
                parent_task_id=self.parent_task_id,
            )

    def transition_to(
        self,
        new_status: TaskStatus,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> TaskEvent:
        """Advance the task status validating legality and minting an immutable TaskEvent."""
        validate_transition(self.status, new_status)
        old_status = self.status
        now = datetime.now(UTC)

        self.status = new_status
        self.updated_at = now

        if self.status == TaskStatus.EXECUTING and self.budget.started_at is None:
            self.budget.start()

        if is_terminal_status(new_status) and self.completed_at is None:
            self.completed_at = now

        event_payload = dict(payload or {})
        if reason:
            event_payload["reason"] = reason

        return TaskEvent(
            task_id=self.task_id,
            event_type=f"TASK_STATUS_{new_status.value}",
            from_status=old_status,
            to_status=new_status,
            payload=event_payload,
            correlation_id=self.correlation.task_id,
            timestamp=now,
        )

    def is_terminal(self) -> bool:
        """Return True if current task status is in a terminal state."""
        return is_terminal_status(self.status)
