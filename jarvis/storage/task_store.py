"""JARVIS Durable Task Store and Event Ledger.

Manages relational persistence for tasks, events, checkpoints, errors, artifacts, and cancellations.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from jarvis.core.logging import get_logger
from jarvis.core.state.state import JarvisState
from jarvis.core.state.status import TaskStatus
from jarvis.core.state.task import JarvisTask, TaskBudget, TaskEvent
from jarvis.storage.db import DatabaseManager, get_db

logger = get_logger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    parent_task_id TEXT,
    title TEXT NOT NULL,
    input_prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    budget_max_seconds REAL NOT NULL,
    budget_max_steps INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    result_json TEXT,
    error_json TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS task_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    payload_json TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id);

CREATE TABLE IF NOT EXISTS task_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    step_count INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_checkpoints_task ON task_checkpoints(task_id);

CREATE TABLE IF NOT EXISTS task_errors (
    error_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    error_type TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT,
    step_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_errors_task ON task_errors(task_id);

CREATE TABLE IF NOT EXISTS task_artifacts (
    artifact_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    uri_or_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_cancellation (
    cancellation_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    propagated_to_children INTEGER NOT NULL DEFAULT 0,
    canceled_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);
"""


class TaskStore:
    """Provides relational persistence for tasks, checkpoints, and event logs."""

    def __init__(self, db: DatabaseManager | None = None) -> None:
        self.db = db or get_db()
        self.init_schema()

    def init_schema(self) -> None:
        """Initialize required database tables and indices."""
        self.db.execute_script(SCHEMA_SQL)

    def save_task(self, task: JarvisTask) -> None:
        """Insert or update a task record."""
        query = """
        INSERT INTO tasks (
            task_id, user_id, session_id, parent_task_id, title, input_prompt,
            status, budget_max_seconds, budget_max_steps, created_at, updated_at,
            completed_at, result_json, error_json, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            status = excluded.status,
            updated_at = excluded.updated_at,
            completed_at = excluded.completed_at,
            result_json = excluded.result_json,
            error_json = excluded.error_json,
            metadata_json = excluded.metadata_json;
        """
        params = (
            task.task_id,
            task.user_id,
            task.session_id,
            task.parent_task_id,
            task.title,
            task.input_prompt,
            task.status.value,
            task.budget.max_wall_time_seconds,
            task.budget.max_graph_steps,
            task.created_at.isoformat(),
            task.updated_at.isoformat(),
            task.completed_at.isoformat() if task.completed_at else None,
            json.dumps(task.result) if task.result else None,
            json.dumps(task.error) if task.error else None,
            json.dumps(task.metadata),
        )
        with self.db.get_connection() as conn:
            conn.execute(query, params)

    def get_task(self, task_id: str) -> JarvisTask | None:
        """Retrieve a task by ID."""
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?;", (task_id,)).fetchone()
            if not row:
                return None

            budget = TaskBudget(
                max_wall_time_seconds=row["budget_max_seconds"],
                max_graph_steps=row["budget_max_steps"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            return JarvisTask(
                task_id=row["task_id"],
                user_id=row["user_id"],
                session_id=row["session_id"],
                parent_task_id=row["parent_task_id"],
                title=row["title"],
                input_prompt=row["input_prompt"],
                status=TaskStatus(row["status"]),
                budget=budget,
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                completed_at=datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None,
                result=json.loads(row["result_json"]) if row["result_json"] else None,
                error=json.loads(row["error_json"]) if row["error_json"] else None,
                metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            )

    def record_event(self, event: TaskEvent) -> None:
        """Persist an immutable task event."""
        query = """
        INSERT INTO task_events (
            event_id, task_id, event_type, from_status, to_status,
            payload_json, correlation_id, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            event.event_id,
            event.task_id,
            event.event_type,
            event.from_status.value if event.from_status else None,
            event.to_status.value if event.to_status else None,
            json.dumps(event.payload),
            event.correlation_id,
            event.timestamp.isoformat(),
        )
        with self.db.get_connection() as conn:
            conn.execute(query, params)

    def get_task_events(self, task_id: str) -> list[TaskEvent]:
        """Fetch all events for a given task ordered chronologically."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM task_events WHERE task_id = ? ORDER BY timestamp ASC;",
                (task_id,),
            ).fetchall()
            events = []
            for r in rows:
                events.append(
                    TaskEvent(
                        event_id=r["event_id"],
                        task_id=r["task_id"],
                        event_type=r["event_type"],
                        from_status=TaskStatus(r["from_status"]) if r["from_status"] else None,
                        to_status=TaskStatus(r["to_status"]) if r["to_status"] else None,
                        payload=json.loads(r["payload_json"]),
                        correlation_id=r["correlation_id"],
                        timestamp=datetime.fromisoformat(r["timestamp"]),
                    )
                )
            return events

    def save_checkpoint(
        self, task_id: str, node_name: str, step_count: int, state: JarvisState
    ) -> str:
        """Atomically persist a state checkpoint for durable recovery."""
        checkpoint_id = f"chk-{uuid4()}"
        query = """
        INSERT INTO task_checkpoints (
            checkpoint_id, task_id, node_name, step_count, state_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?);
        """
        # Serialize state safely
        serializable_state = {
            "task_id": state.get("task_id"),
            "current_status": state.get("current_status", TaskStatus.CREATED).value,
            "step_count": step_count,
            "messages": state.get("messages", []),
            "proposed_intent": state.get("proposed_intent"),
            "observations": state.get("observations", []),
            "is_canceled": state.get("is_canceled", False),
            "cancellation_reason": state.get("cancellation_reason"),
            "errors": state.get("errors", []),
            "artifacts": state.get("artifacts", []),
            "metadata": state.get("metadata", {}),
        }
        params = (
            checkpoint_id,
            task_id,
            node_name,
            step_count,
            json.dumps(serializable_state),
            datetime.now(UTC).isoformat(),
        )
        with self.db.get_connection() as conn:
            conn.execute(query, params)
        logger.debug("checkpoint_saved", task_id=task_id, node=node_name, step=step_count)
        return checkpoint_id

    def get_latest_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        """Fetch the most recent checkpoint for resumption."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM task_checkpoints WHERE task_id = ? ORDER BY step_count DESC, created_at DESC LIMIT 1;",
                (task_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "checkpoint_id": row["checkpoint_id"],
                "task_id": row["task_id"],
                "node_name": row["node_name"],
                "step_count": row["step_count"],
                "state": json.loads(row["state_json"]),
                "created_at": row["created_at"],
            }

    def record_error(
        self,
        task_id: str,
        error_type: str,
        message: str,
        step_count: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record a structured execution error."""
        error_id = f"err-{uuid4()}"
        query = """
        INSERT INTO task_errors (
            error_id, task_id, error_type, message, details_json, step_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            error_id,
            task_id,
            error_type,
            message,
            json.dumps(details or {}),
            step_count,
            datetime.now(UTC).isoformat(),
        )
        with self.db.get_connection() as conn:
            conn.execute(query, params)

    def record_cancellation(self, task_id: str, reason: str) -> None:
        """Record an explicit cancellation signal for a task."""
        cancellation_id = f"cnl-{uuid4()}"
        query = """
        INSERT INTO task_cancellation (
            cancellation_id, task_id, reason, propagated_to_children, canceled_at
        ) VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            reason = excluded.reason;
        """
        params = (
            cancellation_id,
            task_id,
            reason,
            datetime.now(UTC).isoformat(),
        )
        with self.db.get_connection() as conn:
            conn.execute(query, params)

    def is_task_canceled(self, task_id: str) -> tuple[bool, str | None]:
        """Check if a cancellation request has been recorded for this task."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT reason FROM task_cancellation WHERE task_id = ?;",
                (task_id,),
            ).fetchone()
            if row:
                return True, row["reason"]
            return False, None
