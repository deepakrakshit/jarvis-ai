"""JARVIS Governed Memory Plane Storage Engine.

Implements SQLite-backed transactional storage supporting Optimistic Concurrency
Control (OCC), immutable supersession revision chains, point-in-time temporal
validity queries, and epistemic calibration.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from jarvis.core.config import get_settings
from jarvis.core.exceptions import (
    MemoryConcurrencyConflictError,
    MemoryNotFoundError,
)
from jarvis.core.memory.epistemic import EpistemicGovernor
from jarvis.core.memory.schemas import (
    EpistemicStatus,
    MemoryCategory,
    MemoryQuery,
    MemoryRecord,
    MemoryWriteProposal,
    is_epistemic_at_least,
)
from jarvis.core.trust.taxonomy import TrustLevel


class MemoryStore:
    """Transactional SQLite-backed memory store with OCC and temporal governance."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path:
            self.db_path = Path(db_path).resolve()
        else:
            self.db_path = (get_settings().DATA_DIR / "memory.db").resolve()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create a connection with WAL mode and row factory enabled."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        """Initialize database tables and performance indexes."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding_ref TEXT,
                    epistemic_status TEXT NOT NULL,
                    source_trust_level TEXT NOT NULL,
                    source_uri TEXT,
                    created_at TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_until TEXT,
                    superseded_by TEXT,
                    supersedes_id TEXT,
                    version INTEGER NOT NULL,
                    acl TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_scope_key
                ON memories (tenant_id, user_id, key, version);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_temporal
                ON memories (tenant_id, user_id, valid_from, valid_until);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_active
                ON memories (tenant_id, user_id, key)
                WHERE superseded_by IS NULL;
                """
            )
            conn.commit()

    def write(
        self,
        proposal: MemoryWriteProposal,
        actor_id: str = "agent",
        has_verification_witness: bool = False,
    ) -> MemoryRecord:
        """Commit a memory write proposal under optimistic concurrency control."""
        # 1. Epistemic governance validation
        calibrated_status = EpistemicGovernor.calibrate_proposal(
            status=proposal.epistemic_status,
            source_trust=proposal.source_trust_level,
            actor_id=actor_id,
            has_verification_witness=has_verification_witness,
            metadata=proposal.metadata,
        )

        now = datetime.now(UTC)
        now_iso = now.isoformat()
        valid_from_iso = proposal.valid_from.isoformat() if proposal.valid_from else now_iso
        valid_until_iso = proposal.valid_until.isoformat() if proposal.valid_until else None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")

            # 2. Check for active existing record for this key within scope
            cursor.execute(
                """
                SELECT * FROM memories
                WHERE tenant_id = ? AND user_id = ? AND key = ? AND superseded_by IS NULL
                LIMIT 1
                """,
                (proposal.tenant_id, proposal.user_id, proposal.key),
            )
            existing_row = cursor.fetchone()

            if existing_row:
                current_version = existing_row["version"]
                current_id = existing_row["memory_id"]

                # Concurrency check
                if (
                    proposal.expected_version is None
                    or proposal.expected_version != current_version
                ):
                    conn.rollback()
                    raise MemoryConcurrencyConflictError(
                        f"Concurrency conflict on key '{proposal.key}': expected version "
                        f"{proposal.expected_version}, but current active version is {current_version}."
                    )

                new_version = current_version + 1
                new_memory_id = f"mem-{uuid4()}"

                # Mark existing active record as superseded
                cursor.execute(
                    """
                    UPDATE memories
                    SET superseded_by = ?, valid_until = ?
                    WHERE memory_id = ?
                    """,
                    (new_memory_id, now_iso, current_id),
                )
                supersedes_id = current_id
            else:
                if proposal.expected_version is not None and proposal.expected_version != 0:
                    conn.rollback()
                    raise MemoryConcurrencyConflictError(
                        f"Concurrency conflict on key '{proposal.key}': expected version "
                        f"{proposal.expected_version}, but record does not exist."
                    )

                new_version = 1
                new_memory_id = f"mem-{uuid4()}"
                supersedes_id = None

            # 3. Insert new revision record
            acl_json = json.dumps([proposal.user_id])
            meta_json = json.dumps(proposal.metadata, default=str)

            cursor.execute(
                """
                INSERT INTO memories (
                    memory_id, tenant_id, user_id, category, key, content,
                    embedding_ref, epistemic_status, source_trust_level, source_uri,
                    created_at, valid_from, valid_until, superseded_by, supersedes_id,
                    version, acl, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    new_memory_id,
                    proposal.tenant_id,
                    proposal.user_id,
                    proposal.category.value,
                    proposal.key,
                    proposal.content,
                    None,
                    calibrated_status.value,
                    proposal.source_trust_level.value,
                    proposal.source_uri,
                    now_iso,
                    valid_from_iso,
                    valid_until_iso,
                    supersedes_id,
                    new_version,
                    acl_json,
                    meta_json,
                ),
            )
            conn.commit()

        return MemoryRecord(
            memory_id=new_memory_id,
            tenant_id=proposal.tenant_id,
            user_id=proposal.user_id,
            category=proposal.category,
            key=proposal.key,
            content=proposal.content,
            epistemic_status=calibrated_status,
            source_trust_level=proposal.source_trust_level,
            source_uri=proposal.source_uri,
            created_at=now,
            valid_from=proposal.valid_from or now,
            valid_until=proposal.valid_until,
            superseded_by=None,
            supersedes_id=supersedes_id,
            version=new_version,
            acl=[proposal.user_id],
            metadata=proposal.metadata,
        )

    def query(self, query: MemoryQuery) -> list[MemoryRecord]:
        """Execute scoped point-in-time and categorical query over memories."""
        sql = "SELECT * FROM memories WHERE tenant_id = ? AND user_id = ?"
        params: list[Any] = [query.tenant_id, query.user_id]

        if query.category is not None:
            sql += " AND category = ?"
            params.append(query.category.value)

        if query.key is not None:
            sql += " AND key = ?"
            params.append(query.key)

        # Temporal point-in-time filtering
        if query.as_of is not None:
            as_of_iso = query.as_of.isoformat()
            sql += " AND valid_from <= ? AND (valid_until IS NULL OR valid_until > ?)"
            params.extend([as_of_iso, as_of_iso])
        elif not query.include_superseded:
            sql += " AND superseded_by IS NULL"

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(query.limit)

        results: list[MemoryRecord] = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            for row in cursor.fetchall():
                rec = self._row_to_record(row)
                if query.min_epistemic_status and not is_epistemic_at_least(
                    rec.epistemic_status, query.min_epistemic_status
                ):
                    continue
                results.append(rec)

        return results

    def get(self, memory_id: str) -> MemoryRecord | None:
        """Fetch a discrete memory record by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM memories WHERE memory_id = ?", (memory_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def get_history(
        self,
        key: str,
        tenant_id: str = "default",
        user_id: str = "default_user",
    ) -> list[MemoryRecord]:
        """Retrieve complete immutable revision history for a memory key."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM memories
                WHERE tenant_id = ? AND user_id = ? AND key = ?
                ORDER BY version ASC
                """,
                (tenant_id, user_id, key),
            )
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def tombstone(
        self,
        key: str,
        tenant_id: str = "default",
        user_id: str = "default_user",
        expected_version: int | None = None,
    ) -> bool:
        """Retire a memory record under optimistic concurrency control."""
        now_iso = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                """
                SELECT * FROM memories
                WHERE tenant_id = ? AND user_id = ? AND key = ? AND superseded_by IS NULL
                LIMIT 1
                """,
                (tenant_id, user_id, key),
            )
            existing = cursor.fetchone()
            if not existing:
                conn.rollback()
                raise MemoryNotFoundError(f"Active memory key '{key}' not found to tombstone.")

            current_version = existing["version"]
            if expected_version is not None and expected_version != current_version:
                conn.rollback()
                raise MemoryConcurrencyConflictError(
                    f"Concurrency conflict on tombstone of '{key}': expected version {expected_version}, "
                    f"but active version is {current_version}."
                )

            cursor.execute(
                """
                UPDATE memories
                SET superseded_by = 'TOMBSTONE', valid_until = ?
                WHERE memory_id = ?
                """,
                (now_iso, existing["memory_id"]),
            )
            conn.commit()
            return True

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        """Hydrate SQLite Row into strongly-typed MemoryRecord."""
        acl = json.loads(row["acl"]) if row["acl"] else []
        meta = json.loads(row["metadata"]) if row["metadata"] else {}

        return MemoryRecord(
            memory_id=row["memory_id"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            category=MemoryCategory(row["category"]),
            key=row["key"],
            content=row["content"],
            embedding_ref=row["embedding_ref"],
            epistemic_status=EpistemicStatus(row["epistemic_status"]),
            source_trust_level=TrustLevel(row["source_trust_level"]),
            source_uri=row["source_uri"],
            created_at=datetime.fromisoformat(row["created_at"]),
            valid_from=datetime.fromisoformat(row["valid_from"]),
            valid_until=datetime.fromisoformat(row["valid_until"]) if row["valid_until"] else None,
            superseded_by=row["superseded_by"],
            supersedes_id=row["supersedes_id"],
            version=row["version"],
            acl=acl,
            metadata=meta,
        )
