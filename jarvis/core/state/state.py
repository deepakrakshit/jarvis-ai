"""JARVIS LangGraph Control Plane State Definition.

Defines the typed execution state schema traversed by LangGraph nodes and checkpointers.
"""

from typing import Any, TypedDict

from jarvis.core.state.status import TaskStatus
from jarvis.core.state.task import JarvisTask


class JarvisState(TypedDict, total=False):
    """Execution state schema passed across LangGraph nodes."""

    task_id: str
    task: JarvisTask
    current_status: TaskStatus
    step_count: int
    messages: list[dict[str, Any]]
    proposed_intent: dict[str, Any] | None
    observations: list[dict[str, Any]]
    is_canceled: bool
    cancellation_reason: str | None
    errors: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    metadata: dict[str, Any]
