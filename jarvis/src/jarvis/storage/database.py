"""SQLite Persistent Storage Engine for JARVIS.

Provides atomic transactions, WAL concurrency mode, schema initialization,
and strongly-typed repository methods for Tasks, Sessions, Actions, and Audits.
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from jarvis.config import settings
from jarvis.contracts.action import ActionRequest, ActionResult
from jarvis.contracts.memory import MemoryRecord, MemoryType, ProvenanceSource, TrustLevel
from jarvis.contracts.task import Task, TaskPriority, TaskState, TaskType
from jarvis.storage.migrations import MIGRATIONS
from jarvis.telemetry import logger


class DatabaseEngine:
    """Manages SQLite connections, transactions, and entity repositories."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or settings.DATABASE_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Create and configure a SQLite connection with WAL mode."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager providing an atomic SQLite transaction."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception as err:
            conn.rollback()
            logger.error(f"Database transaction error: {err}")
            raise
        finally:
            conn.close()

    def _init_database(self) -> None:
        """Run all pending schema migrations."""
        with self.transaction() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                """
            )
            cursor.execute("SELECT MAX(version) FROM schema_version;")
            row = cursor.fetchone()
            current_version = row[0] if (row and row[0] is not None) else 0

            for index, migration in enumerate(MIGRATIONS, start=1):
                if index > current_version:
                    logger.info(f"Applying database migration version {index}...")
                    cursor.executescript(migration)
                    cursor.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (?, datetime('now'));",
                        (index,),
                    )

    # -------------------------------------------------------------------------
    # Session Repository
    # -------------------------------------------------------------------------

    def create_session(
        self,
        session_id: str,
        title: str = "JARVIS Session",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert or update a session."""
        with self.transaction() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO sessions (session_id, title, created_at, updated_at, metadata_json)
                VALUES (?, ?, datetime('now'), datetime('now'), ?);
                """,
                (session_id, title, json.dumps(metadata or {})),
            )

    # -------------------------------------------------------------------------
    # Task Repository
    # -------------------------------------------------------------------------

    def save_task(self, task: Task) -> None:
        """Persist or update a durable task record."""
        # Ensure session exists first
        self.create_session(task.session_id)
        with self.transaction() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO tasks (
                    task_id, session_id, parent_task_id, task_type, priority, state,
                    raw_intent, normalized_goal, input_payload_json, assigned_model,
                    assigned_agent, token_budget, retry_count, max_retries,
                    state_history_json, result_summary, verification_passed, error_message,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    task.task_id,
                    task.session_id,
                    task.parent_task_id,
                    task.task_type.value,
                    task.priority.value,
                    task.state.value,
                    task.raw_intent,
                    task.normalized_goal,
                    json.dumps(task.input_payload),
                    task.assigned_model,
                    task.assigned_agent,
                    task.token_budget,
                    task.retry_count,
                    task.max_retries,
                    json.dumps(task.state_history),
                    task.result_summary,
                    1
                    if task.verification_passed
                    else (0 if task.verification_passed is False else None),
                    task.error_message,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                    task.completed_at.isoformat() if task.completed_at else None,
                ),
            )

    def get_task(self, task_id: str) -> Optional[Task]:
        """Retrieve a task by ID."""
        with self.transaction() as cursor:
            cursor.execute("SELECT * FROM tasks WHERE task_id = ?;", (task_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return Task(
                task_id=row["task_id"],
                session_id=row["session_id"],
                parent_task_id=row["parent_task_id"],
                task_type=TaskType(row["task_type"]),
                priority=TaskPriority(row["priority"]),
                state=TaskState(row["state"]),
                raw_intent=row["raw_intent"],
                normalized_goal=row["normalized_goal"],
                input_payload=json.loads(row["input_payload_json"] or "{}"),
                assigned_model=row["assigned_model"],
                assigned_agent=row["assigned_agent"],
                token_budget=row["token_budget"],
                retry_count=row["retry_count"],
                max_retries=row["max_retries"],
                state_history=json.loads(row["state_history_json"] or "[]"),
                result_summary=row["result_summary"],
                verification_passed=(
                    bool(row["verification_passed"])
                    if row["verification_passed"] is not None
                    else None
                ),
                error_message=row["error_message"],
            )

    # -------------------------------------------------------------------------
    # Action Request & Result Repository
    # -------------------------------------------------------------------------

    def save_action_request(self, req: ActionRequest) -> None:
        """Persist an action execution request."""
        # Ensure parent task and session exist to satisfy foreign key constraints
        if not self.get_task(req.task_id):
            self.save_task(
                Task(
                    task_id=req.task_id,
                    session_id=req.session_id,
                    raw_intent=f"Action requested: {req.capability}",
                )
            )
        with self.transaction() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO action_requests (
                    action_id, task_id, session_id, correlation_id, actor, capability,
                    arguments_json, target, risk_tier, provenance, approval_required,
                    approval_id, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    req.action_id,
                    req.task_id,
                    req.session_id,
                    req.correlation_id,
                    req.actor,
                    req.capability,
                    json.dumps(req.arguments),
                    req.target.value,
                    req.risk_tier.value,
                    req.provenance,
                    1 if req.approval_required else 0,
                    req.approval_id,
                    req.timestamp.isoformat(),
                ),
            )

    def save_action_result(self, res: ActionResult) -> None:
        """Persist an action execution result."""
        with self.transaction() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO action_results (
                    action_id, task_id, status, execution_target, output_json,
                    error, exit_code, observation_json, verified, duration_ms,
                    timestamp, audit_logged
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    res.action_id,
                    res.task_id,
                    res.status.value,
                    res.execution_target.value,
                    json.dumps(res.output) if res.output is not None else None,
                    res.error,
                    res.exit_code,
                    json.dumps(res.observation),
                    1 if res.verified else 0,
                    res.duration_ms,
                    res.timestamp.isoformat(),
                    1 if res.audit_logged else 0,
                ),
            )

    # -------------------------------------------------------------------------
    # Audit Log Repository
    # -------------------------------------------------------------------------

    def log_audit_event(
        self,
        event_type: str,
        action_id: Optional[str] = None,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        capability: Optional[str] = None,
        verdict: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append an immutable audit entry."""
        with self.transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO audit_log (
                    timestamp, event_type, action_id, task_id, session_id,
                    capability, verdict, details_json
                ) VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    event_type,
                    action_id,
                    task_id,
                    session_id,
                    capability,
                    verdict,
                    json.dumps(details or {}),
                ),
            )

    # -------------------------------------------------------------------------
    # Memory Record Repository
    # -------------------------------------------------------------------------

    def save_memory(self, record: MemoryRecord) -> None:
        """Persist or update a memory record."""
        with self.transaction() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO memory_records (
                    record_id, memory_type, key, content, metadata_json,
                    provenance_source, trust_level, importance_score,
                    relevance_tags_json, session_id, task_id, created_at,
                    updated_at, accessed_at, access_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.record_id,
                    record.memory_type.value,
                    record.key,
                    record.content,
                    json.dumps(record.metadata),
                    record.provenance_source.value,
                    record.trust_level.value,
                    record.importance_score,
                    json.dumps(record.relevance_tags),
                    record.session_id,
                    record.task_id,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    record.accessed_at.isoformat(),
                    record.access_count,
                ),
            )

    def search_memories(
        self, query: str, memory_type: Optional[MemoryType] = None, limit: int = 10
    ) -> List[MemoryRecord]:
        """Search memories by keyword match and optional type filter."""
        with self.transaction() as cursor:
            sql = "SELECT * FROM memory_records WHERE content LIKE ?"
            params: List[Any] = [f"%{query}%"]
            if memory_type:
                sql += " AND memory_type = ?"
                params.append(memory_type.value)
            sql += " ORDER BY importance_score DESC LIMIT ?"
            params.append(limit)

            cursor.execute(sql, tuple(params))
            records: List[MemoryRecord] = []
            for row in cursor.fetchall():
                records.append(
                    MemoryRecord(
                        record_id=row["record_id"],
                        memory_type=MemoryType(row["memory_type"]),
                        key=row["key"],
                        content=row["content"],
                        metadata=json.loads(row["metadata_json"] or "{}"),
                        provenance_source=ProvenanceSource(row["provenance_source"]),
                        trust_level=TrustLevel(row["trust_level"]),
                        importance_score=row["importance_score"],
                        relevance_tags=json.loads(row["relevance_tags_json"] or "[]"),
                        session_id=row["session_id"],
                        task_id=row["task_id"],
                    )
                )
            return records

    def search_memories_fts(
        self, query: str, memory_type: Optional[MemoryType] = None, limit: int = 10
    ) -> List[MemoryRecord]:
        """Search memories using FTS5 full-text index with graceful fallback."""
        with self.transaction() as cursor:
            # First try FTS search
            clean_terms = [
                t.strip() for t in query.replace('"', " ").replace("'", " ").split() if t.strip()
            ]
            if not clean_terms:
                return self.search_memories(query, memory_type=memory_type, limit=limit)

            fts_query = " OR ".join(f'"{term}"*' for term in clean_terms)
            try:
                sql = """
                    SELECT m.* FROM memory_fts f
                    JOIN memory_records m ON f.record_id = m.record_id
                    WHERE memory_fts MATCH ?
                """
                params: List[Any] = [fts_query]
                if memory_type:
                    sql += " AND m.memory_type = ?"
                    params.append(memory_type.value)
                sql += " ORDER BY m.importance_score DESC LIMIT ?"
                params.append(limit)

                cursor.execute(sql, tuple(params))
                rows = cursor.fetchall()
                if rows:
                    return [
                        MemoryRecord(
                            record_id=row["record_id"],
                            memory_type=MemoryType(row["memory_type"]),
                            key=row["key"],
                            content=row["content"],
                            metadata=json.loads(row["metadata_json"] or "{}"),
                            provenance_source=ProvenanceSource(row["provenance_source"]),
                            trust_level=TrustLevel(row["trust_level"]),
                            importance_score=row["importance_score"],
                            relevance_tags=json.loads(row["relevance_tags_json"] or "[]"),
                            session_id=row["session_id"],
                            task_id=row["task_id"],
                        )
                        for row in rows
                    ]
            except Exception as fts_err:
                logger.warning(f"FTS query failed, falling back to LIKE: {fts_err}")

        # Fallback to standard LIKE matching
        return self.search_memories(query, memory_type=memory_type, limit=limit)

    def delete_memory(self, record_id: str) -> bool:
        """Delete a memory record by ID."""
        with self.transaction() as cursor:
            cursor.execute("DELETE FROM memory_records WHERE record_id = ?;", (record_id,))
            return cursor.rowcount > 0

    # -------------------------------------------------------------------------
    # Standing Intents Repository
    # -------------------------------------------------------------------------

    def save_standing_intent(self, intent_dict: Dict[str, Any]) -> None:
        """Insert or replace a standing intent record."""
        with self.transaction() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO standing_intents (
                    id, description, trigger_keywords_json, scope, status,
                    expires_at, max_fires, fire_count, cooldown_seconds,
                    last_fired_at, created_at, session_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    intent_dict["id"],
                    intent_dict["description"],
                    json.dumps(intent_dict.get("trigger_keywords", [])),
                    intent_dict.get("scope", "conversation"),
                    intent_dict.get("status", "armed"),
                    intent_dict.get("expires_at"),
                    intent_dict.get("max_fires", 3),
                    intent_dict.get("fire_count", 0),
                    intent_dict.get("cooldown_seconds", 86400),
                    intent_dict.get("last_fired_at"),
                    intent_dict["created_at"],
                    intent_dict.get("session_id"),
                    json.dumps(intent_dict.get("metadata", {})),
                ),
            )

    def get_standing_intents(
        self, status: Optional[str] = "armed", session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve standing intents filtered by status and optional session."""
        with self.transaction() as cursor:
            sql = "SELECT * FROM standing_intents WHERE 1=1"
            params: List[Any] = []
            if status:
                sql += " AND status = ?"
                params.append(status)
            if session_id:
                sql += " AND (session_id = ? OR session_id IS NULL OR scope = 'anywhere')"
                params.append(session_id)
            sql += " ORDER BY created_at ASC"

            cursor.execute(sql, tuple(params))
            results: List[Dict[str, Any]] = []
            for row in cursor.fetchall():
                results.append(
                    {
                        "id": row["id"],
                        "description": row["description"],
                        "trigger_keywords": json.loads(row["trigger_keywords_json"] or "[]"),
                        "scope": row["scope"],
                        "status": row["status"],
                        "expires_at": row["expires_at"],
                        "max_fires": row["max_fires"],
                        "fire_count": row["fire_count"],
                        "cooldown_seconds": row["cooldown_seconds"],
                        "last_fired_at": row["last_fired_at"],
                        "created_at": row["created_at"],
                        "session_id": row["session_id"],
                        "metadata": json.loads(row["metadata_json"] or "{}"),
                    }
                )
            return results

    def update_standing_intent(
        self,
        intent_id: str,
        status: Optional[str] = None,
        fire_count: Optional[int] = None,
        last_fired_at: Optional[str] = None,
    ) -> None:
        """Update mutable fields of a standing intent."""
        with self.transaction() as cursor:
            updates: List[str] = []
            params: List[Any] = []
            if status is not None:
                updates.append("status = ?")
                params.append(status)
            if fire_count is not None:
                updates.append("fire_count = ?")
                params.append(fire_count)
            if last_fired_at is not None:
                updates.append("last_fired_at = ?")
                params.append(last_fired_at)

            if not updates:
                return

            params.append(intent_id)
            sql = f"UPDATE standing_intents SET {', '.join(updates)} WHERE id = ?;"
            cursor.execute(sql, tuple(params))


# Default singleton instance
db = DatabaseEngine()
