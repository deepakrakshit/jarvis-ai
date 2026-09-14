"""Unit tests for Lease and LeaseManager concurrency control."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from jarvis.core.broker.lease import Lease, LeaseManager
from jarvis.core.broker.types import LeaseAcquisitionError


def test_lease_is_valid() -> None:
    """Test lease expiration checks."""
    now = datetime.now(UTC)
    active = Lease(
        resource_id="res1",
        holder_id="worker1",
        acquired_at=now,
        expires_at=now + timedelta(seconds=10),
        ttl_seconds=10.0,
    )
    assert active.is_valid(now) is True
    assert active.is_valid(now + timedelta(seconds=15)) is False


def test_lease_acquire_and_release() -> None:
    """Test acquiring and releasing a lease."""
    manager = LeaseManager()
    lease = manager.acquire("res_test", "worker_a", ttl_seconds=30.0)
    assert lease.resource_id == "res_test"
    assert lease.holder_id == "worker_a"

    active = manager.get_active_lease("res_test")
    assert active is not None
    assert active.lease_id == lease.lease_id

    # Another worker cannot acquire while active
    with pytest.raises(LeaseAcquisitionError):
        manager.acquire("res_test", "worker_b", ttl_seconds=30.0)

    # Release lease
    released = manager.release(lease.lease_id, "worker_a")
    assert released is True

    # Now worker_b can acquire
    lease_b = manager.acquire("res_test", "worker_b", ttl_seconds=30.0)
    assert lease_b.holder_id == "worker_b"


def test_lease_same_holder_renewal() -> None:
    """Test re-acquisition and renewal by the same holder."""
    manager = LeaseManager()
    lease = manager.acquire("res_renew", "worker_1", ttl_seconds=10.0)
    initial_expiry = lease.expires_at

    # Explicit renewal
    renewed = manager.renew(lease.lease_id, "worker_1", additional_seconds=20.0)
    assert renewed.expires_at > initial_expiry

    # Re-acquire by same worker
    re_acquired = manager.acquire("res_renew", "worker_1", ttl_seconds=50.0)
    assert re_acquired.holder_id == "worker_1"


def test_lease_expired_allows_new_acquisition() -> None:
    """Test that expired leases do not block new acquisitions."""
    manager = LeaseManager()
    # Manually inject an already-expired lease
    past = datetime.now(UTC) - timedelta(seconds=10)
    expired_lease = Lease(
        lease_id=uuid4(),
        resource_id="res_exp",
        holder_id="worker_old",
        acquired_at=past - timedelta(seconds=10),
        expires_at=past,
        ttl_seconds=10.0,
    )
    manager._leases["res_exp"] = expired_lease

    # New acquisition should succeed because previous lease is expired
    new_lease = manager.acquire("res_exp", "worker_new", ttl_seconds=30.0)
    assert new_lease.holder_id == "worker_new"


@pytest.mark.asyncio
async def test_async_lease_acquire_and_release() -> None:
    """Test asynchronous lease acquisition and release."""
    manager = LeaseManager()
    lease = await manager.acquire_async("res_async", "worker_async", ttl_seconds=20.0)
    assert lease.holder_id == "worker_async"

    released = await manager.release_async(lease.lease_id, "worker_async")
    assert released is True
