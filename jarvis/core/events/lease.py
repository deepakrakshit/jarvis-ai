"""JARVIS Distributed Leases and Fencing Tokens.

Provides distributed/local mutual-exclusion leases ensuring single-worker execution,
strictly monotonically increasing fencing tokens to prevent split-brain execution,
and orphan worker recovery (ARCHITECTURE.md Layer 12 & Layer 14).
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from jarvis.core.broker.types import LeaseAcquisitionError
from jarvis.core.config import get_settings
from jarvis.core.exceptions import LeaseFencingError
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class LeaseState(StrEnum):
    """Lifecycle states of a distributed worker lease."""

    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    RELEASED = "RELEASED"
    FENCED = "FENCED"
    REVOKED = "REVOKED"


class LeaseRecord(BaseModel):
    """Immutable distributed lease record with monotonic fencing token."""

    lease_id: str = Field(default_factory=lambda: str(uuid4()))
    resource_id: str
    """Target resource, logical_effect_id, or task lock identifier."""

    holder_id: str
    """Worker ID, subagent ID, or runner process holding the lease."""

    fencing_token: int = Field(default=1, ge=1)
    """Strictly monotonic 64-bit counter protecting downstream storage against stale workers."""

    acquired_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    ttl_seconds: float = 30.0
    heartbeat_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    state: LeaseState = LeaseState.ACTIVE
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_valid(self, at_time: datetime | None = None) -> bool:
        """Return True if lease is active and has not expired."""
        now = at_time or datetime.now(UTC)
        return self.state == LeaseState.ACTIVE and now < self.expires_at


class DurableLeaseManager:
    """Thread-safe, async-safe, and process-safe lease manager with fencing token support."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path: Path | None = None
        if db_path is not None:
            self._db_path = Path(db_path) if isinstance(db_path, str) else db_path
        else:
            settings = get_settings()
            self._db_path = settings.DATA_DIR / "leases.db"

        self._sync_lock = Lock()
        self._async_lock = asyncio.Lock()
        # In-memory fallback if db_path is explicitly None or ":memory:"
        self._memory_leases: dict[str, LeaseRecord] = {}
        self._memory_counters: dict[str, int] = {}

        if self._db_path:
            self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite tables for distributed leases and fencing counters."""
        if not self._db_path:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self._db_path), timeout=30.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS distributed_leases (
                    lease_id TEXT PRIMARY KEY,
                    resource_id TEXT NOT NULL,
                    holder_id TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    ttl_seconds REAL NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_leases_res_state
                ON distributed_leases(resource_id, state);
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lease_fencing_counters (
                    resource_id TEXT PRIMARY KEY,
                    current_token INTEGER NOT NULL
                );
                """
            )
            conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        if not self._db_path:
            raise RuntimeError("Database path not configured")
        conn = sqlite3.connect(str(self._db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def acquire(
        self,
        resource_id: str,
        holder_id: str,
        ttl_seconds: float = 30.0,
        metadata: dict[str, Any] | None = None,
    ) -> LeaseRecord:
        """Acquire a mutual-exclusion lease on resource_id with an incremented fencing token."""
        with self._sync_lock:
            now = datetime.now(UTC)

            if not self._db_path:
                # In-memory path
                existing = self._memory_leases.get(resource_id)
                if existing and existing.is_valid(now):
                    if existing.holder_id == holder_id:
                        renewed = existing.model_copy(
                            update={
                                "expires_at": now + timedelta(seconds=ttl_seconds),
                                "heartbeat_at": now,
                                "ttl_seconds": ttl_seconds,
                            }
                        )
                        self._memory_leases[resource_id] = renewed
                        return renewed
                    raise LeaseAcquisitionError(
                        resource_id=resource_id,
                        holder_id=holder_id,
                        message=(
                            f"Resource '{resource_id}' is currently leased by holder "
                            f"'{existing.holder_id}' until {existing.expires_at.isoformat()}."
                        ),
                    )

                counter = self._memory_counters.get(resource_id, 0) + 1
                self._memory_counters[resource_id] = counter

                if existing:
                    self._memory_leases[resource_id] = existing.model_copy(
                        update={"state": LeaseState.EXPIRED}
                    )

                new_lease = LeaseRecord(
                    resource_id=resource_id,
                    holder_id=holder_id,
                    fencing_token=counter,
                    acquired_at=now,
                    expires_at=now + timedelta(seconds=ttl_seconds),
                    ttl_seconds=ttl_seconds,
                    state=LeaseState.ACTIVE,
                    metadata=metadata or {},
                )
                self._memory_leases[resource_id] = new_lease
                return new_lease

            # SQLite persistent path
            with self._get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE;")
                # Look for existing active lease
                cursor = conn.execute(
                    """
                    SELECT * FROM distributed_leases
                    WHERE resource_id = ? AND state = 'ACTIVE'
                    ORDER BY acquired_at DESC LIMIT 1;
                    """,
                    (resource_id,),
                )
                row = cursor.fetchone()
                if row:
                    expires_at = datetime.fromisoformat(row["expires_at"])
                    if now < expires_at:
                        if row["holder_id"] == holder_id:
                            # Re-entrant renewal by same holder
                            new_expiry = (now + timedelta(seconds=ttl_seconds)).isoformat()
                            heartbeat_iso = now.isoformat()
                            conn.execute(
                                """
                                UPDATE distributed_leases
                                SET expires_at = ?, heartbeat_at = ?, ttl_seconds = ?
                                WHERE lease_id = ?;
                                """,
                                (new_expiry, heartbeat_iso, ttl_seconds, row["lease_id"]),
                            )
                            conn.commit()
                            return LeaseRecord(
                                lease_id=row["lease_id"],
                                resource_id=row["resource_id"],
                                holder_id=row["holder_id"],
                                fencing_token=row["fencing_token"],
                                acquired_at=datetime.fromisoformat(row["acquired_at"]),
                                expires_at=now + timedelta(seconds=ttl_seconds),
                                ttl_seconds=ttl_seconds,
                                heartbeat_at=now,
                                state=LeaseState.ACTIVE,
                                metadata=json.loads(row["metadata"]),
                            )
                        raise LeaseAcquisitionError(
                            resource_id=resource_id,
                            holder_id=holder_id,
                            message=(
                                f"Resource '{resource_id}' is currently leased by holder "
                                f"'{row['holder_id']}' until {row['expires_at']}."
                            ),
                        )
                    # Mark expired lease as EXPIRED
                    conn.execute(
                        "UPDATE distributed_leases SET state = 'EXPIRED' WHERE lease_id = ?;",
                        (row["lease_id"],),
                    )

                # Increment monotonic counter
                conn.execute(
                    """
                    INSERT INTO lease_fencing_counters (resource_id, current_token)
                    VALUES (?, 1)
                    ON CONFLICT(resource_id) DO UPDATE SET current_token = current_token + 1;
                    """,
                    (resource_id,),
                )
                token_row = conn.execute(
                    "SELECT current_token FROM lease_fencing_counters WHERE resource_id = ?;",
                    (resource_id,),
                ).fetchone()
                token = int(token_row["current_token"]) if token_row else 1

                new_id = str(uuid4())
                expires_at_dt = now + timedelta(seconds=ttl_seconds)
                meta_json = json.dumps(metadata or {})
                conn.execute(
                    """
                    INSERT INTO distributed_leases (
                        lease_id, resource_id, holder_id, fencing_token,
                        acquired_at, expires_at, ttl_seconds, heartbeat_at,
                        state, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        new_id,
                        resource_id,
                        holder_id,
                        token,
                        now.isoformat(),
                        expires_at_dt.isoformat(),
                        ttl_seconds,
                        now.isoformat(),
                        LeaseState.ACTIVE.value,
                        meta_json,
                    ),
                )
                conn.commit()

                return LeaseRecord(
                    lease_id=new_id,
                    resource_id=resource_id,
                    holder_id=holder_id,
                    fencing_token=token,
                    acquired_at=now,
                    expires_at=expires_at_dt,
                    ttl_seconds=ttl_seconds,
                    heartbeat_at=now,
                    state=LeaseState.ACTIVE,
                    metadata=metadata or {},
                )

    async def acquire_async(
        self,
        resource_id: str,
        holder_id: str,
        ttl_seconds: float = 30.0,
        metadata: dict[str, Any] | None = None,
    ) -> LeaseRecord:
        """Acquire a lease asynchronously."""
        async with self._async_lock:
            return self.acquire(resource_id, holder_id, ttl_seconds, metadata)

    def heartbeat(
        self, lease_id: str, holder_id: str, extension_seconds: float | None = None
    ) -> LeaseRecord:
        """Renew active lease heartbeat and extend its expiration window."""
        with self._sync_lock:
            now = datetime.now(UTC)

            if not self._db_path:
                for rid, lease in self._memory_leases.items():
                    if lease.lease_id == lease_id:
                        if lease.holder_id != holder_id:
                            raise LeaseAcquisitionError(
                                resource_id=rid,
                                holder_id=holder_id,
                                message=f"Cannot heartbeat lease held by '{lease.holder_id}'.",
                            )
                        if not lease.is_valid(now):
                            raise LeaseAcquisitionError(
                                resource_id=rid,
                                holder_id=holder_id,
                                message="Cannot heartbeat expired lease.",
                            )
                        ext = (
                            extension_seconds
                            if extension_seconds is not None
                            else lease.ttl_seconds
                        )
                        renewed = lease.model_copy(
                            update={
                                "expires_at": now + timedelta(seconds=ext),
                                "heartbeat_at": now,
                            }
                        )
                        self._memory_leases[rid] = renewed
                        return renewed
                raise LeaseAcquisitionError(
                    resource_id="unknown",
                    holder_id=holder_id,
                    message=f"Lease '{lease_id}' not found.",
                )

            with self._get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE;")
                row = conn.execute(
                    "SELECT * FROM distributed_leases WHERE lease_id = ?;", (lease_id,)
                ).fetchone()
                if not row:
                    raise LeaseAcquisitionError(
                        resource_id="unknown",
                        holder_id=holder_id,
                        message=f"Lease '{lease_id}' not found.",
                    )
                if row["holder_id"] != holder_id:
                    raise LeaseAcquisitionError(
                        resource_id=row["resource_id"],
                        holder_id=holder_id,
                        message=f"Cannot heartbeat lease held by '{row['holder_id']}'.",
                    )
                expires_at = datetime.fromisoformat(row["expires_at"])
                if row["state"] != LeaseState.ACTIVE.value or now >= expires_at:
                    raise LeaseAcquisitionError(
                        resource_id=row["resource_id"],
                        holder_id=holder_id,
                        message="Cannot heartbeat expired or inactive lease.",
                    )

                ext = (
                    extension_seconds
                    if extension_seconds is not None
                    else float(row["ttl_seconds"])
                )
                new_expiry = now + timedelta(seconds=ext)
                conn.execute(
                    """
                    UPDATE distributed_leases
                    SET expires_at = ?, heartbeat_at = ?
                    WHERE lease_id = ?;
                    """,
                    (new_expiry.isoformat(), now.isoformat(), lease_id),
                )
                conn.commit()

                return LeaseRecord(
                    lease_id=row["lease_id"],
                    resource_id=row["resource_id"],
                    holder_id=row["holder_id"],
                    fencing_token=row["fencing_token"],
                    acquired_at=datetime.fromisoformat(row["acquired_at"]),
                    expires_at=new_expiry,
                    ttl_seconds=ext,
                    heartbeat_at=now,
                    state=LeaseState.ACTIVE,
                    metadata=json.loads(row["metadata"]),
                )

    async def heartbeat_async(
        self, lease_id: str, holder_id: str, extension_seconds: float | None = None
    ) -> LeaseRecord:
        """Renew active lease heartbeat asynchronously."""
        async with self._async_lock:
            return self.heartbeat(lease_id, holder_id, extension_seconds)

    def release(self, lease_id: str, holder_id: str) -> bool:
        """Release an active lease."""
        with self._sync_lock:
            if not self._db_path:
                for rid, lease in list(self._memory_leases.items()):
                    if lease.lease_id == lease_id:
                        if lease.holder_id == holder_id:
                            self._memory_leases[rid] = lease.model_copy(
                                update={"state": LeaseState.RELEASED}
                            )
                            return True
                        return False
                return False

            with self._get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE;")
                row = conn.execute(
                    "SELECT holder_id FROM distributed_leases WHERE lease_id = ?;", (lease_id,)
                ).fetchone()
                if not row or row["holder_id"] != holder_id:
                    return False
                conn.execute(
                    "UPDATE distributed_leases SET state = 'RELEASED' WHERE lease_id = ?;",
                    (lease_id,),
                )
                conn.commit()
                return True

    async def release_async(self, lease_id: str, holder_id: str) -> bool:
        """Release an active lease asynchronously."""
        async with self._async_lock:
            return self.release(lease_id, holder_id)

    def validate_fencing_token(self, resource_id: str, fencing_token: int) -> bool:
        """Validate whether the supplied fencing token is current (rejects stale workers)."""
        with self._sync_lock:
            if not self._db_path:
                active = self._memory_leases.get(resource_id)
                if not active or not active.is_valid():
                    current = self._memory_counters.get(resource_id, 0)
                    return fencing_token >= current if current > 0 else True
                return fencing_token == active.fencing_token

            with self._get_connection() as conn:
                counter_row = conn.execute(
                    "SELECT current_token FROM lease_fencing_counters WHERE resource_id = ?;",
                    (resource_id,),
                ).fetchone()
                if not counter_row:
                    return True
                current_token = int(counter_row["current_token"])
                # Fencing token must match or exceed current token; strictly rejects older tokens
                return fencing_token >= current_token

    def verify_fencing_or_raise(self, resource_id: str, fencing_token: int) -> None:
        """Raise LeaseFencingError if fencing token has been superseded."""
        if not self.validate_fencing_token(resource_id, fencing_token):
            raise LeaseFencingError(
                f"Fencing token {fencing_token} is stale for resource '{resource_id}'. Operation rejected.",
                {"resource_id": resource_id, "stale_fencing_token": fencing_token},
            )

    def reclaim_orphaned_leases(self, older_than_seconds: float | None = None) -> list[str]:
        """Detect and transition expired or abandoned leases to EXPIRED."""
        reclaimed: list[str] = []
        with self._sync_lock:
            now = datetime.now(UTC)

            if not self._db_path:
                for rid, lease in list(self._memory_leases.items()):
                    if lease.state == LeaseState.ACTIVE:
                        is_expired = now >= lease.expires_at
                        if older_than_seconds:
                            is_abandoned = (
                                now - lease.heartbeat_at
                            ).total_seconds() > older_than_seconds
                        else:
                            is_abandoned = False
                        if is_expired or is_abandoned:
                            self._memory_leases[rid] = lease.model_copy(
                                update={"state": LeaseState.EXPIRED}
                            )
                            reclaimed.append(rid)
                return reclaimed

            with self._get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE;")
                rows = conn.execute(
                    "SELECT lease_id, resource_id, expires_at, heartbeat_at FROM distributed_leases WHERE state = 'ACTIVE';"
                ).fetchall()
                for r in rows:
                    exp = datetime.fromisoformat(r["expires_at"])
                    hb = datetime.fromisoformat(r["heartbeat_at"])
                    is_expired = now >= exp
                    is_abandoned = (
                        (now - hb).total_seconds() > older_than_seconds
                        if older_than_seconds
                        else False
                    )
                    if is_expired or is_abandoned:
                        conn.execute(
                            "UPDATE distributed_leases SET state = 'EXPIRED' WHERE lease_id = ?;",
                            (r["lease_id"],),
                        )
                        reclaimed.append(r["resource_id"])
                conn.commit()
        return reclaimed

    def get_active_lease(self, resource_id: str) -> LeaseRecord | None:
        """Retrieve current active lease for resource_id if valid."""
        with self._sync_lock:
            now = datetime.now(UTC)
            if not self._db_path:
                lease = self._memory_leases.get(resource_id)
                if lease and lease.is_valid(now):
                    return lease
                return None

            with self._get_connection() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM distributed_leases
                    WHERE resource_id = ? AND state = 'ACTIVE'
                    ORDER BY acquired_at DESC LIMIT 1;
                    """,
                    (resource_id,),
                ).fetchone()
                if not row:
                    return None
                expires_at = datetime.fromisoformat(row["expires_at"])
                if now >= expires_at:
                    return None
                return LeaseRecord(
                    lease_id=row["lease_id"],
                    resource_id=row["resource_id"],
                    holder_id=row["holder_id"],
                    fencing_token=row["fencing_token"],
                    acquired_at=datetime.fromisoformat(row["acquired_at"]),
                    expires_at=expires_at,
                    ttl_seconds=row["ttl_seconds"],
                    heartbeat_at=datetime.fromisoformat(row["heartbeat_at"]),
                    state=LeaseState.ACTIVE,
                    metadata=json.loads(row["metadata"]),
                )
