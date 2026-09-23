"""Scheduled Jobs and Heartbeat Data Models.

Defines schemas for periodic jobs, timing anchors, and proactive heartbeat reports.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduledJob(BaseModel):
    """Periodic job specification managed by JARVIS Cron Scheduler."""

    job_id: str = Field(default_factory=lambda: f"JOB-{uuid4().hex[:8].upper()}")
    name: str
    interval_seconds: int = Field(gt=0)
    raw_intent: str
    enabled: bool = True
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def is_due(self, now: Optional[datetime] = None) -> bool:
        """Evaluate if the job is eligible for execution."""
        if not self.enabled:
            return False
        current_time = now or utc_now()
        if self.next_run_at is None:
            return True
        return current_time >= self.next_run_at


class HeartbeatReport(BaseModel):
    """Immutable report of a proactive heartbeat awareness cycle."""

    timestamp: datetime = Field(default_factory=utc_now)
    status: str = "COMPLETED"
    standing_intents_checked: int = 0
    standing_intents_fired: int = 0
    jobs_checked: int = 0
    jobs_triggered: int = 0
    dispatched_tasks: List[str] = Field(default_factory=list)
    summary: str = ""
