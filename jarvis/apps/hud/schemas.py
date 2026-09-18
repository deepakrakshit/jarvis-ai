"""JARVIS Holographic HUD State Schemas.

Defines the authoritative data models for HUD state projection,
independent Control Plane task synchronization, and spoken HITL approval nonces.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class HUDSystemMode(StrEnum):
    """Operational mode of the JARVIS Control Plane reflected on the HUD."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    EXECUTING = "EXECUTING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ERROR = "ERROR"
    QUARANTINED = "QUARANTINED"


class HUDVoiceStatus(StrEnum):
    """Realtime Voice Plane connection status decoupled from task execution."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    BARGE_IN = "BARGE_IN"
    ROLLOVER = "ROLLOVER"


class HUDTaskItem(BaseModel):
    """A background or foreground task monitored on the HUD surface."""

    task_id: str
    description: str
    status: str = "RUNNING"
    progress_percent: float = 0.0
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    specialist: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HUDApprovalItem(BaseModel):
    """A pending Human-in-the-Loop approval item with spoken verification nonce."""

    approval_id: str
    task_id: str
    tool_id: str
    risk_class: str
    arguments_summary: str
    proposal_digest: str
    nonce: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None


class HUDTelemetrySummary(BaseModel):
    """Aggregated operational metrics for the HUD diagnostics panel."""

    active_leases_count: int = 0
    p95_latency_ms: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    dlq_dead_letter_count: int = 0


class HUDState(BaseModel):
    """Authoritative snapshot of the JARVIS HUD surface state."""

    session_id: str
    system_mode: HUDSystemMode = HUDSystemMode.IDLE
    voice_status: HUDVoiceStatus = HUDVoiceStatus.DISCONNECTED
    active_specialist: str | None = None
    current_intent: str | None = None
    last_transcript: str | None = None
    last_response: str | None = None
    active_tasks: list[HUDTaskItem] = Field(default_factory=list)
    pending_approvals: list[HUDApprovalItem] = Field(default_factory=list)
    telemetry: HUDTelemetrySummary = Field(default_factory=HUDTelemetrySummary)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
