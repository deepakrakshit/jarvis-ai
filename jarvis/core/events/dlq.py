"""JARVIS Dead Letter Queue (DLQ) Subsystem.

Provides poison message quarantine, persistent diagnostic recording, and safe operator
replay for unroutable, malformed, or retry-exhausted events (ARCHITECTURE.md Layer 12).
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

from pydantic import BaseModel, Field

from jarvis.core.config import get_settings
from jarvis.core.events.schemas import EventMessage
from jarvis.core.exceptions import DLQMessageNotFoundError
from jarvis.core.logging import get_logger
from jarvis.core.telemetry import get_metrics

logger = get_logger(__name__)


class DLQRecord(BaseModel):
    """Immutable audit record representing a quarantined poison or failed message."""

    dlq_id: str = Field(default_factory=lambda: str(uuid4()))
    event: EventMessage
    reason: str
    error_trace: str | None = None
    quarantined_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    retry_count: int = 0
    status: str = "QUARANTINED"  # QUARANTINED, REPLAYED, PURGED


class DeadLetterQueue:
    """Persistent or in-memory Dead Letter Queue with replay and audit management."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path: Path | None = None
        if db_path is not None:
            self._db_path = Path(db_path) if isinstance(db_path, str) else db_path
        else:
            settings = get_settings()
            self._db_path = settings.DATA_DIR / "dlq.db"

        self._lock = Lock()
        self._memory_records: dict[str, DLQRecord] = {}

        if self._db_path:
            self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite tables for Dead Letter Queue storage."""
        if not self._db_path:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self._db_path), timeout=30.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dead_letter_queue (
                    dlq_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    error_trace TEXT,
                    quarantined_at TEXT NOT NULL,
                    retry_count INTEGER NOT NULL,
                    status TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_dlq_event_id
                ON dead_letter_queue(event_id);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_dlq_status
                ON dead_letter_queue(status);
                """
            )
            conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        if not self._db_path:
            raise RuntimeError("Database path not configured")
        conn = sqlite3.connect(str(self._db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def quarantine(
        self,
        event: EventMessage,
        reason: str,
        error_trace: str | None = None,
    ) -> DLQRecord:
        """Quarantine a failed, cyclic, or depth-exceeded event message."""
        record = DLQRecord(
            event=event,
            reason=reason,
            error_trace=error_trace,
            quarantined_at=datetime.now(UTC),
            retry_count=event.retry_count,
            status="QUARANTINED",
        )
        get_metrics().record_dlq_event("quarantine")

        with self._lock:
            if not self._db_path:
                self._memory_records[record.dlq_id] = record
                logger.error(
                    "event_quarantined_to_dlq",
                    dlq_id=record.dlq_id,
                    event_id=event.event_id,
                    reason=reason,
                )
                return record

            with self._get_connection() as conn:
                event_json = event.model_dump_json()
                conn.execute(
                    """
                    INSERT INTO dead_letter_queue (
                        dlq_id, event_id, event_type, event_json, reason,
                        error_trace, quarantined_at, retry_count, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        record.dlq_id,
                        event.event_id,
                        event.event_type,
                        event_json,
                        reason,
                        error_trace,
                        record.quarantined_at.isoformat(),
                        event.retry_count,
                        record.status,
                    ),
                )
                conn.commit()

        logger.error(
            "event_quarantined_to_dlq",
            dlq_id=record.dlq_id,
            event_id=event.event_id,
            reason=reason,
        )
        return record

    def get_record(self, dlq_id: str) -> DLQRecord | None:
        """Retrieve a DLQ record by its identifier."""
        with self._lock:
            if not self._db_path:
                return self._memory_records.get(dlq_id)

            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM dead_letter_queue WHERE dlq_id = ?;", (dlq_id,)
                ).fetchone()
                if not row:
                    return None
                event_data = json.loads(row["event_json"])
                event = EventMessage.model_validate(event_data)
                return DLQRecord(
                    dlq_id=row["dlq_id"],
                    event=event,
                    reason=row["reason"],
                    error_trace=row["error_trace"],
                    quarantined_at=datetime.fromisoformat(row["quarantined_at"]),
                    retry_count=row["retry_count"],
                    status=row["status"],
                )

    def list_records(
        self, limit: int = 100, offset: int = 0, status: str | None = "QUARANTINED"
    ) -> list[DLQRecord]:
        """List DLQ records filtered by status and paginated."""
        with self._lock:
            if not self._db_path:
                records = [
                    r for r in self._memory_records.values() if status is None or r.status == status
                ]
                return sorted(records, key=lambda x: x.quarantined_at, reverse=True)[
                    offset : offset + limit
                ]

            with self._get_connection() as conn:
                if status:
                    rows = conn.execute(
                        """
                        SELECT * FROM dead_letter_queue
                        WHERE status = ?
                        ORDER BY quarantined_at DESC
                        LIMIT ? OFFSET ?;
                        """,
                        (status, limit, offset),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM dead_letter_queue
                        ORDER BY quarantined_at DESC
                        LIMIT ? OFFSET ?;
                        """,
                        (limit, offset),
                    ).fetchall()

                result: list[DLQRecord] = []
                for row in rows:
                    event = EventMessage.model_validate(json.loads(row["event_json"]))
                    result.append(
                        DLQRecord(
                            dlq_id=row["dlq_id"],
                            event=event,
                            reason=row["reason"],
                            error_trace=row["error_trace"],
                            quarantined_at=datetime.fromisoformat(row["quarantined_at"]),
                            retry_count=row["retry_count"],
                            status=row["status"],
                        )
                    )
                return result

    def replay(self, dlq_id: str) -> EventMessage:
        """Mark a DLQ record as replayed and return a fresh EventMessage ready for re-dispatch."""
        get_metrics().record_dlq_event("replay")
        with self._lock:
            if not self._db_path:
                rec = self._memory_records.get(dlq_id)
                if not rec:
                    raise DLQMessageNotFoundError(f"DLQ message '{dlq_id}' not found.")
                self._memory_records[dlq_id] = rec.model_copy(update={"status": "REPLAYED"})
                # Reset retry count for redelivery
                replayed_event = rec.event.model_copy(update={"retry_count": 0})
                return replayed_event

            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM dead_letter_queue WHERE dlq_id = ?;", (dlq_id,)
                ).fetchone()
                if not row:
                    raise DLQMessageNotFoundError(f"DLQ message '{dlq_id}' not found.")

                conn.execute(
                    "UPDATE dead_letter_queue SET status = 'REPLAYED' WHERE dlq_id = ?;",
                    (dlq_id,),
                )
                conn.commit()

                event = EventMessage.model_validate(json.loads(row["event_json"]))
                replayed_event = event.model_copy(update={"retry_count": 0})
                return replayed_event

    def purge(self, dlq_id: str | None = None) -> int:
        """Purge specific or all records from the Dead Letter Queue."""
        with self._lock:
            if not self._db_path:
                if dlq_id:
                    if dlq_id in self._memory_records:
                        del self._memory_records[dlq_id]
                        return 1
                    return 0
                count = len(self._memory_records)
                self._memory_records.clear()
                return count

            with self._get_connection() as conn:
                if dlq_id:
                    cursor = conn.execute(
                        "DELETE FROM dead_letter_queue WHERE dlq_id = ?;", (dlq_id,)
                    )
                else:
                    cursor = conn.execute("DELETE FROM dead_letter_queue;")
                conn.commit()
                return cursor.rowcount

    def count(self, status: str = "QUARANTINED") -> int:
        """Return total count of messages matching status."""
        with self._lock:
            if not self._db_path:
                return sum(1 for r in self._memory_records.values() if r.status == status)

            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*) as cnt FROM dead_letter_queue WHERE status = ?;", (status,)
                )
                row = cursor.fetchone()
                return int(row["cnt"]) if row else 0
