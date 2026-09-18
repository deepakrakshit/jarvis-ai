"""JARVIS Dual-Layer Observability Schemas and OTLP Data Models.

Defines Control Plane vs. Data Plane span classifications, OpenTelemetry-compatible
span entities, trace context propagation, and event models (ARCHITECTURE.md Layer 19).
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class TelemetryLayer(StrEnum):
    """Dual-layer execution classification distinguishing Control Plane from Data Plane."""

    CONTROL_PLANE = "CONTROL_PLANE"
    """Metadata-driven orchestration flow (lifecycle, task state, policy checks, broker dispatch, leases, verification)."""

    DATA_PLANE = "DATA_PLANE"
    """High-bandwidth payload execution (model inference, tool payloads, sandbox execution, artifact transfers, memory records)."""


class SpanKind(StrEnum):
    """OpenTelemetry-compatible span classification."""

    INTERNAL = "INTERNAL"
    SERVER = "SERVER"
    CLIENT = "CLIENT"
    PRODUCER = "PRODUCER"
    CONSUMER = "CONSUMER"


class SpanStatus(StrEnum):
    """Span completion status code."""

    UNSET = "UNSET"
    OK = "OK"
    ERROR = "ERROR"


class SpanEvent(BaseModel):
    """Timestamped diagnostic annotation attached to an execution span."""

    name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    attributes: dict[str, Any] = Field(default_factory=dict)

    def to_otlp_dict(self) -> dict[str, Any]:
        """Serialize event to OpenTelemetry-compliant dictionary representation."""
        return {
            "name": self.name,
            "time_unix_nano": int(self.timestamp.timestamp() * 1e9),
            "attributes": self.attributes,
        }


class TelemetrySpan(BaseModel):
    """OpenTelemetry-compatible distributed trace span supporting dual-layer bifurcation."""

    span_id: str = Field(default_factory=lambda: uuid4().hex[:16])
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    parent_span_id: str | None = None
    name: str
    layer: TelemetryLayer = TelemetryLayer.CONTROL_PLANE
    kind: SpanKind = SpanKind.INTERNAL
    start_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    end_time: datetime | None = None
    duration_ms: float | None = None
    status: SpanStatus = SpanStatus.UNSET
    status_message: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[SpanEvent] = Field(default_factory=list)

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Attach a point-in-time event to this span."""
        self.events.append(SpanEvent(name=name, attributes=attributes or {}))

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a single attribute on the span."""
        self.attributes[key] = value

    def finish(self, status: SpanStatus = SpanStatus.OK, message: str | None = None) -> None:
        """Mark span as finalized and calculate elapsed duration."""
        if self.end_time is None:
            self.end_time = datetime.now(UTC)
            self.duration_ms = max(0.0, (self.end_time - self.start_time).total_seconds() * 1000.0)
        self.status = status
        self.status_message = message

    def to_otlp_dict(self) -> dict[str, Any]:
        """Export span to standard OpenTelemetry JSON-compatible format."""
        start_nano = int(self.start_time.timestamp() * 1e9)
        end_nano = (
            int(self.end_time.timestamp() * 1e9)
            if self.end_time
            else int(datetime.now(UTC).timestamp() * 1e9)
        )
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "kind": self.kind.value,
            "start_time_unix_nano": start_nano,
            "end_time_unix_nano": end_nano,
            "duration_ms": self.duration_ms,
            "layer": self.layer.value,
            "status": {
                "code": self.status.value,
                "message": self.status_message or "",
            },
            "attributes": self.attributes,
            "events": [ev.to_otlp_dict() for ev in self.events],
        }
