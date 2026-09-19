"""Standing Intents Engine (Prospective Memory).

Adapts the standing intents contract from the core substrate to support
prospective triggers, recurring notifications, and contextual reminders.
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StandingIntent(BaseModel):
    """Prospective intent model waiting for event triggers."""

    id: str = Field(default_factory=lambda: f"INTENT-{uuid4().hex[:8].upper()}")
    description: str
    trigger_keywords: List[str] = Field(default_factory=list)
    scope: str = "conversation"  # "conversation", "channel", "anywhere"
    status: str = "armed"  # "pending", "armed", "fired", "done", "cancelled", "expired"
    expires_at: Optional[datetime] = None
    max_fires: int = 3
    fire_count: int = 0
    cooldown_seconds: int = 86400  # 24 hour default cooldown
    last_fired_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def is_match(self, text: str) -> bool:
        """Check if any trigger keywords are matched in input text."""
        if not self.trigger_keywords:
            return False
        normalized_text = text.lower()
        for kw in self.trigger_keywords:
            pattern = rf"\b{re.escape(kw.lower())}"
            if re.search(pattern, normalized_text):
                return True
        return False

    def can_fire(self, now: Optional[datetime] = None) -> bool:
        """Determine if this intent is currently eligible to fire."""
        current_time = now or utc_now()

        if self.status != "armed":
            return False

        if self.expires_at and current_time > self.expires_at:
            return False

        if self.fire_count >= self.max_fires:
            return False

        if self.last_fired_at:
            elapsed = (current_time - self.last_fired_at).total_seconds()
            if elapsed < self.cooldown_seconds:
                return False

        return True

    def record_fire(self, now: Optional[datetime] = None) -> None:
        """Update firing metadata and transition state if exhausted."""
        current_time = now or utc_now()
        self.fire_count += 1
        self.last_fired_at = current_time
        if self.fire_count >= self.max_fires:
            self.status = "done"
        else:
            self.status = "armed"


class StandingIntentManager:
    """Manages creation, evaluation, and lifecycle of prospective standing intents."""

    def __init__(self, database: Optional[DatabaseEngine] = None) -> None:
        self.db = database or db

    def register(self, intent: StandingIntent) -> StandingIntent:
        """Persist a new or updated standing intent."""
        intent_dict = {
            "id": intent.id,
            "description": intent.description,
            "trigger_keywords": intent.trigger_keywords,
            "scope": intent.scope,
            "status": intent.status,
            "expires_at": intent.expires_at.isoformat() if intent.expires_at else None,
            "max_fires": intent.max_fires,
            "fire_count": intent.fire_count,
            "cooldown_seconds": intent.cooldown_seconds,
            "last_fired_at": intent.last_fired_at.isoformat() if intent.last_fired_at else None,
            "created_at": intent.created_at.isoformat(),
            "session_id": intent.session_id,
            "metadata": intent.metadata,
        }
        self.db.save_standing_intent(intent_dict)
        logger.info(f"Registered standing intent {intent.id}: '{intent.description}'")
        return intent

    def evaluate(self, event_text: str, session_id: Optional[str] = None) -> List[StandingIntent]:
        """Evaluate incoming event/message against armed standing intents."""
        rows = self.db.get_standing_intents(status="armed", session_id=session_id)
        matched_intents: List[StandingIntent] = []
        now = utc_now()

        for row in rows:
            expires_at = (
                datetime.fromisoformat(row["expires_at"]) if row.get("expires_at") else None
            )
            last_fired = (
                datetime.fromisoformat(row["last_fired_at"]) if row.get("last_fired_at") else None
            )
            created = datetime.fromisoformat(row["created_at"]) if row.get("created_at") else now

            intent = StandingIntent(
                id=row["id"],
                description=row["description"],
                trigger_keywords=row["trigger_keywords"],
                scope=row["scope"],
                status=row["status"],
                expires_at=expires_at,
                max_fires=row["max_fires"],
                fire_count=row["fire_count"],
                cooldown_seconds=row["cooldown_seconds"],
                last_fired_at=last_fired,
                created_at=created,
                session_id=row["session_id"],
                metadata=row["metadata"],
            )

            # Check expiry
            if intent.expires_at and now > intent.expires_at:
                self.db.update_standing_intent(intent.id, status="expired")
                continue

            if intent.is_match(event_text) and intent.can_fire(now):
                intent.record_fire(now)
                # Persist updated status
                self.db.update_standing_intent(
                    intent.id,
                    status=intent.status,
                    fire_count=intent.fire_count,
                    last_fired_at=intent.last_fired_at.isoformat()
                    if intent.last_fired_at
                    else None,
                )
                matched_intents.append(intent)
                logger.info(f"Standing intent {intent.id} triggered: '{intent.description}'")

        return matched_intents

    def cancel(self, intent_id: str) -> None:
        """Cancel a standing intent."""
        self.db.update_standing_intent(intent_id, status="cancelled")
        logger.info(f"Cancelled standing intent {intent_id}")
