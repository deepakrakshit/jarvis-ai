"""JARVIS Action Broker Leases and Concurrent Deduplication Locks.

Provides distributed/local mutual-exclusion leases ensuring single-worker execution
and preventing duplicate side effects across subagents and concurrent workers
(ARCHITECTURE.md Layer 14 & Layer 18).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from jarvis.core.broker.types import LeaseAcquisitionError


class Lease(BaseModel):
    """Immutable distributed lease record."""

    lease_id: UUID = Field(default_factory=uuid4)
    resource_id: str
    """Target resource, logical_effect_id, or task lock identifier."""

    holder_id: str
    """Worker ID, subagent ID, or runner process holding the lease."""

    acquired_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    ttl_seconds: float = 30.0

    def is_valid(self, at_time: datetime | None = None) -> bool:
        """Return True if lease has not expired."""
        now = at_time or datetime.now(UTC)
        return now < self.expires_at


class LeaseManager:
    """Thread-safe and async-safe lease manager for deduplicating actions."""

    def __init__(self) -> None:
        self._leases: dict[str, Lease] = {}
        self._sync_lock = Lock()
        self._async_lock = asyncio.Lock()

    def _purge_expired(self, now: datetime | None = None) -> None:
        current_time = now or datetime.now(UTC)
        expired = [rid for rid, lease in self._leases.items() if not lease.is_valid(current_time)]
        for rid in expired:
            del self._leases[rid]

    def acquire(self, resource_id: str, holder_id: str, ttl_seconds: float = 30.0) -> Lease:
        """Acquire a synchronous lease on resource_id for holder_id."""
        with self._sync_lock:
            now = datetime.now(UTC)
            self._purge_expired(now)

            existing = self._leases.get(resource_id)
            if existing and existing.is_valid(now):
                if existing.holder_id == holder_id:
                    # Same holder re-acquiring / extending
                    expires_at = now + timedelta(seconds=ttl_seconds)
                    renewed = Lease(
                        lease_id=existing.lease_id,
                        resource_id=resource_id,
                        holder_id=holder_id,
                        acquired_at=existing.acquired_at,
                        expires_at=expires_at,
                        ttl_seconds=ttl_seconds,
                    )
                    self._leases[resource_id] = renewed
                    return renewed
                raise LeaseAcquisitionError(
                    resource_id=resource_id,
                    holder_id=holder_id,
                    message=(
                        f"Resource '{resource_id}' is currently leased by holder "
                        f"'{existing.holder_id}' until {existing.expires_at.isoformat()}."
                    ),
                )

            expires_at = now + timedelta(seconds=ttl_seconds)
            lease = Lease(
                resource_id=resource_id,
                holder_id=holder_id,
                acquired_at=now,
                expires_at=expires_at,
                ttl_seconds=ttl_seconds,
            )
            self._leases[resource_id] = lease
            return lease

    async def acquire_async(
        self, resource_id: str, holder_id: str, ttl_seconds: float = 30.0
    ) -> Lease:
        """Acquire an asynchronous lease on resource_id for holder_id."""
        async with self._async_lock:
            return self.acquire(resource_id, holder_id, ttl_seconds)

    def release(self, lease_id: UUID, holder_id: str) -> bool:
        """Release a lease if held by holder_id."""
        with self._sync_lock:
            for rid, lease in list(self._leases.items()):
                if lease.lease_id == lease_id:
                    if lease.holder_id == holder_id:
                        del self._leases[rid]
                        return True
                    return False
            return False

    async def release_async(self, lease_id: UUID, holder_id: str) -> bool:
        """Release a lease asynchronously."""
        async with self._async_lock:
            return self.release(lease_id, holder_id)

    def renew(self, lease_id: UUID, holder_id: str, additional_seconds: float) -> Lease:
        """Extend an active lease duration."""
        with self._sync_lock:
            now = datetime.now(UTC)
            for rid, lease in self._leases.items():
                if lease.lease_id == lease_id:
                    if lease.holder_id != holder_id:
                        raise LeaseAcquisitionError(
                            resource_id=rid,
                            holder_id=holder_id,
                            message=f"Cannot renew lease held by '{lease.holder_id}'.",
                        )
                    if not lease.is_valid(now):
                        raise LeaseAcquisitionError(
                            resource_id=rid,
                            holder_id=holder_id,
                            message="Cannot renew expired lease.",
                        )
                    expires_at = lease.expires_at + timedelta(seconds=additional_seconds)
                    renewed = Lease(
                        lease_id=lease.lease_id,
                        resource_id=rid,
                        holder_id=holder_id,
                        acquired_at=lease.acquired_at,
                        expires_at=expires_at,
                        ttl_seconds=lease.ttl_seconds + additional_seconds,
                    )
                    self._leases[rid] = renewed
                    return renewed
            raise LeaseAcquisitionError(
                resource_id=str(lease_id),
                holder_id=holder_id,
                message=f"Lease {lease_id} not found.",
            )

    def get_active_lease(self, resource_id: str) -> Lease | None:
        """Retrieve the currently active lease for resource_id, if valid."""
        with self._sync_lock:
            now = datetime.now(UTC)
            lease = self._leases.get(resource_id)
            if lease and lease.is_valid(now):
                return lease
            return None
