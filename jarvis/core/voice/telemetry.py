"""JARVIS Voice Plane Observability and Structured Telemetry Subsystem.

Tracks fine-grained lifecycle events, audio streaming metrics, session durations,
reconnection counts, and resource consumption without logging raw audio or leaking credentials.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class VoiceResourceMetrics(BaseModel):
    """Cumulative resource consumption metrics for a voice session."""

    session_id: str
    total_audio_input_seconds: float = 0.0
    total_audio_output_seconds: float = 0.0
    total_text_tokens: int = 0
    total_audio_tokens: int = 0
    total_tool_calls: int = 0
    reconnect_count: int = 0
    compression_events: int = 0
    session_start_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_end_time: datetime | None = None

    @property
    def session_duration_seconds(self) -> float:
        """Calculate total session duration in seconds."""
        end = self.session_end_time or datetime.now(UTC)
        return max(0.0, (end - self.session_start_time).total_seconds())


class VoiceTelemetryLogger:
    """Structured telemetry emitter and metrics accumulator for realtime voice sessions."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.metrics = VoiceResourceMetrics(session_id=session_id)
        self.event_history: list[dict[str, Any]] = []

    def record_event(
        self,
        event_name: str,
        details: dict[str, Any] | None = None,
        level: str = "INFO",
    ) -> None:
        """Emit a structured lifecycle event conforming to Section 43 specifications."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": self.session_id,
            "event": event_name,
            "details": details or {},
        }
        self.event_history.append(entry)

        log_fn = getattr(logger, level.lower(), logger.info)
        log_fn(f"voice_telemetry_{event_name}", session_id=self.session_id, **(details or {}))

    def log_session_created(self, model_id: str) -> None:
        self.record_event("live_session_created", {"model_id": model_id})

    def log_session_connected(self, connection_id: str, model_id: str) -> None:
        self.record_event(
            "live_session_connected", {"connection_id": connection_id, "model_id": model_id}
        )

    def log_connection_replaced(self, old_conn_id: str, new_conn_id: str) -> None:
        self.metrics.reconnect_count += 1
        self.record_event(
            "live_connection_replaced",
            {
                "old_connection_id": old_conn_id,
                "new_connection_id": new_conn_id,
                "total_reconnects": self.metrics.reconnect_count,
            },
        )

    def log_session_resumed(self, handle: str) -> None:
        # Mask handle suffix for privacy
        masked_handle = f"{handle[:8]}...{handle[-4:]}" if len(handle) > 12 else "handle_masked"
        self.record_event("live_session_resumed", {"handle": masked_handle})

    def log_goaway_received(self, time_left_seconds: float | None) -> None:
        self.record_event(
            "live_goaway_received",
            {"time_left_seconds": time_left_seconds},
            level="WARNING",
        )

    def log_turn_started(self, is_user: bool = True) -> None:
        self.record_event("live_turn_started", {"turn_actor": "user" if is_user else "model"})

    def log_turn_completed(self, is_user: bool = True) -> None:
        self.record_event("live_turn_completed", {"turn_actor": "user" if is_user else "model"})

    def log_audio_input(self, duration_seconds: float) -> None:
        self.metrics.total_audio_input_seconds += max(0.0, duration_seconds)

    def log_audio_output(self, duration_seconds: float) -> None:
        self.metrics.total_audio_output_seconds += max(0.0, duration_seconds)

    def log_tool_call(self, tool_name: str, call_id: str, is_non_blocking: bool) -> None:
        self.metrics.total_tool_calls += 1
        self.record_event(
            "live_tool_call",
            {"tool_name": tool_name, "call_id": call_id, "is_non_blocking": is_non_blocking},
        )

    def log_tool_response(self, tool_name: str, call_id: str, status: str) -> None:
        self.record_event(
            "live_tool_response",
            {"tool_name": tool_name, "call_id": call_id, "status": status},
        )

    def log_interrupt(self) -> None:
        self.record_event("live_interrupt", {"action": "speech_truncated"})

    def log_task_attached(self, task_id: str, task_title: str) -> None:
        self.record_event("live_task_attached", {"task_id": task_id, "title": task_title})

    def log_task_progress(self, task_id: str, progress_message: str) -> None:
        self.record_event(
            "live_task_progress",
            {"task_id": task_id, "progress": progress_message},
        )

    def log_task_completed(self, task_id: str, success: bool) -> None:
        self.record_event(
            "live_task_completed",
            {"task_id": task_id, "success": success},
        )

    def log_task_failed(self, task_id: str, error: str) -> None:
        self.record_event(
            "live_task_failed",
            {"task_id": task_id, "error": error},
            level="ERROR",
        )

    def log_session_degraded(self, reason: str) -> None:
        self.record_event("live_session_degraded", {"reason": reason}, level="WARNING")

    def log_session_closed(self) -> None:
        self.metrics.session_end_time = datetime.now(UTC)
        self.record_event(
            "live_session_closed",
            {
                "duration_seconds": self.metrics.session_duration_seconds,
                "reconnects": self.metrics.reconnect_count,
                "total_tool_calls": self.metrics.total_tool_calls,
                "audio_in_sec": self.metrics.total_audio_input_seconds,
                "audio_out_sec": self.metrics.total_audio_output_seconds,
            },
        )
