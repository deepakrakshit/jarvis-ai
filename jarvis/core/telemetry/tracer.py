"""JARVIS Distributed Tracer, Context Propagation, and Exporter Architecture.

Implements ARCHITECTURE.md Layer 19: Dual-layer span lifecycle, hierarchical context
propagation across async boundaries, privacy redaction prior to export, and OTLP serialization.
"""

import contextvars
import functools
import traceback
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, TypeVar

from jarvis.core.config import get_settings
from jarvis.core.telemetry.metrics import get_metrics
from jarvis.core.telemetry.redaction import PrivacyScrubber, get_scrubber
from jarvis.core.telemetry.schemas import (
    SpanKind,
    SpanStatus,
    TelemetryLayer,
    TelemetrySpan,
)

F = TypeVar("F", bound=Callable[..., Any])

# Context variable tracking current in-flight span in active async task / thread
ctx_active_span: contextvars.ContextVar[TelemetrySpan | None] = contextvars.ContextVar(
    "active_telemetry_span", default=None
)


class SpanExporter(ABC):
    """Abstract interface for span publication backends."""

    @abstractmethod
    def export(self, span: TelemetrySpan) -> None:
        """Export a single finalized span."""
        pass

    def export_batch(self, spans: list[TelemetrySpan]) -> None:
        """Export a batch of finalized spans."""
        for span in spans:
            self.export(span)


class InMemorySpanExporter(SpanExporter):
    """Thread-safe in-memory span buffer for test assertions and local debugging."""

    def __init__(self, max_spans: int = 10000) -> None:
        self.max_spans = max_spans
        self._spans: list[TelemetrySpan] = []
        self._lock = Lock()

    def export(self, span: TelemetrySpan) -> None:
        """Store span in buffer."""
        with self._lock:
            self._spans.append(span)
            if len(self._spans) > self.max_spans:
                self._spans = self._spans[-self.max_spans :]

    def get_spans(
        self,
        name: str | None = None,
        layer: TelemetryLayer | None = None,
        status: SpanStatus | None = None,
    ) -> list[TelemetrySpan]:
        """Query captured spans matching optional filters."""
        with self._lock:
            res: list[TelemetrySpan] = []
            for s in self._spans:
                if name is not None and s.name != name:
                    continue
                if layer is not None and s.layer != layer:
                    continue
                if status is not None and s.status != status:
                    continue
                res.append(s)
            return list(res)

    def clear(self) -> None:
        """Clear all stored spans."""
        with self._lock:
            self._spans.clear()


class FileSpanExporter(SpanExporter):
    """Appends sanitized OTLP-compliant JSON records to a persistent JSONL log file."""

    def __init__(self, file_path: Path | str | None = None) -> None:
        if file_path:
            self.file_path = Path(file_path)
        else:
            settings = get_settings()
            self.file_path = settings.DATA_DIR / "telemetry_spans.jsonl"

        self._lock = Lock()
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, span: TelemetrySpan) -> None:
        """Append span JSON to file."""
        from jarvis.core.serialization import canonical_json_dumps

        data = span.to_otlp_dict()
        line = canonical_json_dumps(data) + "\n"
        with self._lock, open(self.file_path, "a", encoding="utf-8") as f:
            f.write(line)


class Tracer:
    """The central OpenTelemetry-compatible tracer with privacy redaction and dual-layer routing."""

    def __init__(
        self,
        scrubber: PrivacyScrubber | None = None,
        exporters: list[SpanExporter] | None = None,
    ) -> None:
        self.scrubber = scrubber or get_scrubber()
        self._exporters: list[SpanExporter] = exporters or []
        self._lock = Lock()

    def add_exporter(self, exporter: SpanExporter) -> None:
        """Register an exporter backend."""
        with self._lock:
            if exporter not in self._exporters:
                self._exporters.append(exporter)

    def remove_exporter(self, exporter: SpanExporter) -> bool:
        """Remove a registered exporter backend."""
        with self._lock:
            if exporter in self._exporters:
                self._exporters.remove(exporter)
                return True
            return False

    def start_span(
        self,
        name: str,
        layer: TelemetryLayer = TelemetryLayer.CONTROL_PLANE,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, Any] | None = None,
        parent_span: TelemetrySpan | None = None,
        set_active: bool = True,
    ) -> TelemetrySpan:
        """Construct and initialize a new span, linking to active parent context."""
        parent = parent_span or ctx_active_span.get()

        clean_attrs = self.scrubber.scrub_attributes(attributes or {})
        span = TelemetrySpan(
            name=name,
            layer=layer,
            kind=kind,
            start_time=datetime.now(UTC),
            attributes=clean_attrs,
        )

        if parent:
            span.trace_id = parent.trace_id
            span.parent_span_id = parent.span_id

        if set_active:
            ctx_active_span.set(span)
        return span

    def end_span(
        self,
        span: TelemetrySpan,
        status: SpanStatus = SpanStatus.OK,
        error: Exception | None = None,
        message: str | None = None,
    ) -> None:
        """Finalize span, record operational latency, sanitize attributes, and export."""
        msg = message or (str(error) if error else None)
        if msg:
            msg = self.scrubber.scrub_text(msg)

        span.finish(status=status, message=msg)

        # Sanitize attributes one final time before distribution
        span.attributes = self.scrubber.scrub_attributes(span.attributes)

        # Record latency into global OperationalMetrics
        if span.duration_ms is not None:
            cat = f"{span.layer.value.lower()}:{span.name}"
            get_metrics().record_latency(cat, span.duration_ms)

        # Dispatch to exporters
        with self._lock:
            for exporter in self._exporters:
                with suppress(Exception):
                    exporter.export(span)

    @asynccontextmanager
    async def span(
        self,
        name: str,
        layer: TelemetryLayer = TelemetryLayer.CONTROL_PLANE,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, Any] | None = None,
    ) -> AsyncGenerator[TelemetrySpan, None]:
        """Async context manager handling span lifecycle and error recording."""
        parent = ctx_active_span.get()
        s = self.start_span(
            name=name,
            layer=layer,
            kind=kind,
            attributes=attributes,
            parent_span=parent,
            set_active=False,
        )
        token = ctx_active_span.set(s)
        try:
            yield s
            self.end_span(s, status=SpanStatus.OK)
        except Exception as exc:
            s.set_attribute("error.type", exc.__class__.__name__)
            s.set_attribute("error.stack", self.scrubber.scrub_text(traceback.format_exc()))
            self.end_span(s, status=SpanStatus.ERROR, error=exc)
            raise
        finally:
            ctx_active_span.reset(token)

    @contextmanager
    def sync_span(
        self,
        name: str,
        layer: TelemetryLayer = TelemetryLayer.CONTROL_PLANE,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[TelemetrySpan, None, None]:
        """Synchronous context manager handling span lifecycle."""
        parent = ctx_active_span.get()
        s = self.start_span(
            name=name,
            layer=layer,
            kind=kind,
            attributes=attributes,
            parent_span=parent,
            set_active=False,
        )
        token = ctx_active_span.set(s)
        try:
            yield s
            self.end_span(s, status=SpanStatus.OK)
        except Exception as exc:
            s.set_attribute("error.type", exc.__class__.__name__)
            s.set_attribute("error.stack", self.scrubber.scrub_text(traceback.format_exc()))
            self.end_span(s, status=SpanStatus.ERROR, error=exc)
            raise
        finally:
            ctx_active_span.reset(token)

    def trace(
        self,
        name: str | None = None,
        layer: TelemetryLayer = TelemetryLayer.CONTROL_PLANE,
    ) -> Callable[[F], F]:
        """Decorator for tracing coroutines or synchronous functions."""

        def decorator(func: F) -> F:
            span_name = name or func.__name__

            import asyncio

            if asyncio.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    async with self.span(name=span_name, layer=layer):
                        return await func(*args, **kwargs)

                return async_wrapper  # type: ignore[return-value]
            else:

                @functools.wraps(func)
                def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                    with self.sync_span(name=span_name, layer=layer):
                        return func(*args, **kwargs)

                return sync_wrapper  # type: ignore[return-value]

        return decorator


_DEFAULT_TRACER: Tracer | None = None


def get_tracer() -> Tracer:
    """Retrieve or initialize the default Tracer singleton."""
    global _DEFAULT_TRACER
    if _DEFAULT_TRACER is None:
        _DEFAULT_TRACER = Tracer()
    return _DEFAULT_TRACER
