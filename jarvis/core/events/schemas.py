"""JARVIS Event Plane Schemas and Causation Graph.

Defines strongly-typed event messages, priority classifications, and causal ancestry
graphs with cycle detection and depth boundary defense (ARCHITECTURE.md Layer 12).
"""

from datetime import UTC, datetime
from enum import IntEnum
from threading import Lock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from jarvis.core.exceptions import CausalCycleError, CausalDepthExceededError


class EventPriority(IntEnum):
    """Event dispatch priority tiers. Lower integer values indicate higher priority."""

    CONTROL = 0  # High-priority control plane signals (voice interrupt, abort, lease revocation)
    NORMAL = 1  # Standard agent commands, model requests, and tool executions
    BACKGROUND = 2  # Low-priority async tasks (memory compaction, indexing, audits)


class EventMessage(BaseModel):
    """Structured, causally-tracked event message for distributed execution and tracing."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str = Field(default_factory=lambda: str(uuid4()))
    causation_id: str | None = None
    depth: int = Field(default=0, ge=0)
    source: str = "jarvis:core"
    lease_id: str | None = None
    priority: int = Field(default=EventPriority.NORMAL, ge=0, le=2)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    idempotency_key: str | None = None
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=0)
    ttl_seconds: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def create_child(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        source: str | None = None,
        priority: int | None = None,
        idempotency_key: str | None = None,
    ) -> "EventMessage":
        """Derive a causally-connected child event propagating trace context and incrementing depth."""
        return EventMessage(
            event_type=event_type,
            payload=payload or {},
            correlation_id=self.correlation_id,
            causation_id=self.event_id,
            depth=self.depth + 1,
            source=source or self.source,
            priority=self.priority if priority is None else priority,
            idempotency_key=idempotency_key,
            max_retries=self.max_retries,
        )


class CausationGraph:
    """Thread-safe causal ancestry graph providing cycle detection and depth bounding."""

    def __init__(self, max_depth: int = 5) -> None:
        self.max_depth = max_depth
        self._parents: dict[str, str | None] = {}
        self._lock = Lock()

    def record_event(self, event: EventMessage, max_depth_override: int | None = None) -> None:
        """Record an event in the graph, enforcing depth limits and detecting cycles."""
        limit = max_depth_override if max_depth_override is not None else self.max_depth
        if event.depth > limit:
            raise CausalDepthExceededError(
                f"Causal depth {event.depth} exceeds allowable limit of {limit} for event {event.event_id}",
                {"event_id": event.event_id, "depth": event.depth, "limit": limit},
            )

        with self._lock:
            if event.causation_id:
                # Detect causal cycles: trace parents backward
                curr: str | None = event.causation_id
                visited: set[str] = {event.event_id}
                while curr is not None:
                    if curr in visited:
                        raise CausalCycleError(
                            f"Causal cycle detected: event {event.event_id} is an ancestor of itself via {curr}",
                            {"event_id": event.event_id, "cycle_at": curr},
                        )
                    visited.add(curr)
                    curr = self._parents.get(curr)

            self._parents[event.event_id] = event.causation_id

    def get_lineage(self, event_id: str) -> list[str]:
        """Return the causal lineage chain from the root origin down to the specified event."""
        with self._lock:
            chain: list[str] = []
            curr: str | None = event_id
            visited: set[str] = set()
            while curr is not None:
                if curr in visited:
                    break
                visited.add(curr)
                chain.append(curr)
                curr = self._parents.get(curr)
            return list(reversed(chain))

    def get_root_cause(self, event_id: str) -> str:
        """Resolve the origin event identifier in this event's causal lineage."""
        lineage = self.get_lineage(event_id)
        return lineage[0] if lineage else event_id

    def prune(self, keep_last_n: int = 10000) -> None:
        """Prune older entries to bound memory usage."""
        with self._lock:
            if len(self._parents) > keep_last_n:
                keys = list(self._parents.keys())
                to_remove = keys[: len(keys) - keep_last_n]
                for k in to_remove:
                    del self._parents[k]
