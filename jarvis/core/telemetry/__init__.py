"""JARVIS Dual-Layer Observability and Telemetry Subsystem.

Provides OpenTelemetry-compatible tracing across Control Plane and Data Plane,
automated regex and entropy-based credential/PII redaction, and operational metrics
accounting (ARCHITECTURE.md Layer 19).
"""

from jarvis.core.telemetry.metrics import OperationalMetrics, get_metrics
from jarvis.core.telemetry.redaction import PrivacyScrubber, get_scrubber
from jarvis.core.telemetry.schemas import (
    SpanEvent,
    SpanKind,
    SpanStatus,
    TelemetryLayer,
    TelemetrySpan,
)
from jarvis.core.telemetry.tracer import (
    FileSpanExporter,
    InMemorySpanExporter,
    SpanExporter,
    Tracer,
    ctx_active_span,
    get_tracer,
)

__all__ = [
    "FileSpanExporter",
    "InMemorySpanExporter",
    "OperationalMetrics",
    "PrivacyScrubber",
    "SpanEvent",
    "SpanExporter",
    "SpanKind",
    "SpanStatus",
    "TelemetryLayer",
    "TelemetrySpan",
    "Tracer",
    "ctx_active_span",
    "get_metrics",
    "get_scrubber",
    "get_tracer",
]
