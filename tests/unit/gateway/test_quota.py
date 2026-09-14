"""Tests for ModelQuotaManager, safe quarantine math, and Groq duration parser."""

import pytest

from jarvis.core.exceptions import QuotaExceededError
from jarvis.core.gateway.groq_adapter import parse_groq_reset_duration
from jarvis.core.gateway.quota import (
    LeaseState,
    ModelQuotaManager,
    QuotaDomain,
    QuotaLease,
)


def _assert_lease_state(lease: QuotaLease, expected: LeaseState) -> None:
    assert lease.state == expected


@pytest.mark.asyncio
async def test_lease_reservation_and_reconciliation() -> None:
    """Verify two-phase reservation lease transition to RECONCILED."""
    manager = ModelQuotaManager(model_id="test-model", rpm_limit=10, tpm_limit=5000)

    # 1. Tentative reservation
    lease = await manager.reserve_lease(estimated_tokens=200, domain=QuotaDomain.GENERATION)
    _assert_lease_state(lease, LeaseState.RESERVED)
    assert lease.estimated_tokens == 200
    assert manager.requests_this_minute == 1
    assert manager.tokens_this_minute == 200

    # 2. Reconcile with actual token usage
    await manager.reconcile_lease(lease.lease_id, actual_tokens=150)
    _assert_lease_state(lease, LeaseState.RECONCILED)
    assert lease.actual_tokens == 150
    # tokens_this_minute adjusted from 200 to 150
    assert manager.tokens_this_minute == 150


@pytest.mark.asyncio
async def test_lease_failure_and_orphan_retention() -> None:
    """Verify that failed leases are retained in orphaned_leases list (never deleted)."""
    manager = ModelQuotaManager(model_id="test-model", rpm_limit=10)

    lease = await manager.reserve_lease(estimated_tokens=100)
    assert len(manager.active_leases) == 1
    assert len(manager.orphaned_leases) == 0

    # Mark as failed
    await manager.fail_lease(lease.lease_id, reason="Provider timeout")
    assert len(manager.active_leases) == 0
    assert len(manager.orphaned_leases) == 1

    orphan = manager.orphaned_leases[0]
    assert orphan.state == LeaseState.FAILED
    assert orphan.error_reason == "Provider timeout"


@pytest.mark.asyncio
async def test_quarantine_mathematically_safe_threshold_for_low_rpm() -> None:
    """CRITICAL FIX: Verify quarantine condition does not trigger prematurely on low RPM limits.

    Formula: len(orphans) >= 5 or (rpm_limit > 0 and (len(orphans) * 2) >= rpm_limit)
    For rpm_limit = 1: 0 orphans must NOT quarantine (0 * 2 >= 1 is False).
    For rpm_limit = 5: 1 orphan must NOT quarantine (1 * 2 >= 5 is False).
    """
    # Test RPM = 1
    manager_rpm1 = ModelQuotaManager(model_id="rpm-1-model", rpm_limit=1)
    assert manager_rpm1.check_quarantine() is False

    # Fail 1 lease
    lease1 = await manager_rpm1.reserve_lease(estimated_tokens=50)
    await manager_rpm1.fail_lease(lease1.lease_id, reason="Fail 1")
    # For rpm=1: 1 orphan -> (1 * 2 >= 1) is True -> triggers quarantine!
    assert manager_rpm1.check_quarantine() is True

    # Test RPM = 5
    manager_rpm5 = ModelQuotaManager(model_id="rpm-5-model", rpm_limit=5)
    assert manager_rpm5.check_quarantine() is False

    # 1 orphan: (1 * 2 >= 5) is False -> NOT quarantined
    l1 = await manager_rpm5.reserve_lease()
    await manager_rpm5.fail_lease(l1.lease_id)
    assert manager_rpm5.check_quarantine() is False

    # 2 orphans: (2 * 2 >= 5) is False -> NOT quarantined
    l2 = await manager_rpm5.reserve_lease()
    await manager_rpm5.fail_lease(l2.lease_id)
    assert manager_rpm5.check_quarantine() is False

    # 3 orphans: (3 * 2 >= 5) is True -> Quarantined
    l3 = await manager_rpm5.reserve_lease()
    await manager_rpm5.fail_lease(l3.lease_id)
    assert manager_rpm5.check_quarantine() is True


@pytest.mark.asyncio
async def test_quarantined_model_rejects_further_leases() -> None:
    """A quarantined model immediately raises QuotaExceededError."""
    manager = ModelQuotaManager(model_id="test-model", rpm_limit=2)
    l1 = await manager.reserve_lease()
    await manager.fail_lease(l1.lease_id)

    assert manager.is_quarantined is True
    with pytest.raises(QuotaExceededError) as exc:
        await manager.reserve_lease()
    assert "is QUARANTINED" in str(exc.value)


def test_parse_groq_reset_duration_valid() -> None:
    """Verify parsing of valid Groq duration formats."""
    assert parse_groq_reset_duration("15s") == 15.0
    assert parse_groq_reset_duration("2m30s") == 150.0
    assert parse_groq_reset_duration("1h") == 3600.0
    assert parse_groq_reset_duration("500ms") == 0.5
    assert parse_groq_reset_duration("1h2m3s4ms") == 3600.0 + 120.0 + 3.0 + 0.004
    assert parse_groq_reset_duration("42") == 42.0


def test_parse_groq_reset_duration_fails_closed_on_malformed() -> None:
    """Verify that malformed Groq duration formats fail closed by raising ValueError."""
    with pytest.raises(ValueError):
        parse_groq_reset_duration("")

    with pytest.raises(ValueError):
        parse_groq_reset_duration("invalid_duration")

    with pytest.raises(ValueError):
        parse_groq_reset_duration("15x")

    with pytest.raises(ValueError):
        parse_groq_reset_duration("2m-30s")
