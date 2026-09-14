"""JARVIS Correlation and Causation Tracing Context.

Enforces end-to-end trace correlation across all state transitions and system operations.
"""

from contextlib import AbstractContextManager
from uuid import uuid4

from pydantic import BaseModel, Field

from jarvis.core.logging import bind_correlation


class CorrelationContext(BaseModel):
    """Immutable correlation and causation metadata for distributed operations."""

    request_id: str = Field(default_factory=lambda: f"req-{uuid4()}")
    session_id: str = Field(default_factory=lambda: f"sess-{uuid4()}")
    task_id: str = Field(default_factory=lambda: f"task-{uuid4()}")
    parent_task_id: str | None = None
    causation_id: str | None = None
    agent_id: str | None = None
    attempt_id: int = Field(default=1, ge=1)
    logical_effect_id: str | None = None

    def create_child(self, child_task_id: str | None = None) -> "CorrelationContext":
        """Create a child correlation context preserving root request and session IDs."""
        return CorrelationContext(
            request_id=self.request_id,
            session_id=self.session_id,
            task_id=child_task_id or f"task-{uuid4()}",
            parent_task_id=self.task_id,
            causation_id=self.task_id,
            agent_id=self.agent_id,
            attempt_id=1,
            logical_effect_id=None,
        )

    def bind(self) -> AbstractContextManager[None]:
        """Bind this correlation context to active thread/asyncio context variables."""
        return bind_correlation(
            request_id=self.request_id,
            session_id=self.session_id,
            task_id=self.task_id,
            agent_id=self.agent_id,
            attempt_id=str(self.attempt_id),
            logical_effect_id=self.logical_effect_id,
        )
