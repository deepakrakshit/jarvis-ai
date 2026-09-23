"""Tests for Dynamic Quota & Rate-Limit Manager."""

import pytest

from jarvis.cognition.quota_manager import ModelHealth, QuotaManager
from jarvis.contracts.model import ModelFamily


@pytest.fixture
def manager() -> QuotaManager:
    """Create a fresh isolated QuotaManager instance."""
    return QuotaManager()


@pytest.mark.asyncio
async def test_quota_manager_defaults(manager: QuotaManager) -> None:
    """Verify default quotas are initialized for all 6 approved models."""
    quotas = manager.get_all_quotas()
    assert len(quotas) == 6

    for family in ModelFamily:
        quota = manager.get_quota(family)
        assert quota.model_family == family
        assert quota.max_rpm > 0
        assert quota.max_tpm > 0
        assert manager.get_health(family) == ModelHealth.HEALTHY


@pytest.mark.asyncio
async def test_can_schedule_and_acquire(manager: QuotaManager) -> None:
    """Verify scheduling checks and concurrency tracking."""
    family = ModelFamily.GPT_OSS_120B

    assert await manager.can_schedule(family, estimated_tokens=100) is True

    await manager.acquire(family, estimated_tokens=200)
    quota = manager.get_quota(family)
    assert quota.current_concurrency == 1
    assert quota.current_rpm_used == 1

    # Release successfully
    await manager.release(
        model_family=family,
        tokens_used=180,
        latency_ms=120.0,
        success=True,
    )
    assert quota.current_concurrency == 0
    assert quota.recent_latency_ms == 120.0


@pytest.mark.asyncio
async def test_dynamic_header_absorption(manager: QuotaManager) -> None:
    """Verify dynamic update from provider HTTP response headers."""
    family = ModelFamily.QWEN_3_8_27B

    headers = {
        "x-ratelimit-remaining-requests": "42",
        "x-ratelimit-remaining-tokens": "5500",
        "x-ratelimit-reset-requests": "12.5s",
    }

    await manager.release(
        model_family=family,
        tokens_used=50,
        latency_ms=85.0,
        success=True,
        response_headers=headers,
    )

    quota = manager.get_quota(family)
    assert quota.remaining_requests == 42
    assert quota.remaining_tokens == 5500
    assert quota.reset_epoch_seconds == 12.5


@pytest.mark.asyncio
async def test_health_degradation_and_error_handling(manager: QuotaManager) -> None:
    """Verify health transitions on consecutive failures."""
    family = ModelFamily.GEMINI_3_1_FLASH_LITE

    # Record 2 failures -> DEGRADED
    await manager.release(family, 0, 50.0, success=False)
    await manager.release(family, 0, 50.0, success=False)
    assert manager.get_health(family) == ModelHealth.DEGRADED

    # Rate limit error -> immediate degradation
    await manager.record_error(family, "429 Too Many Requests: Rate limit exceeded")
    assert manager.get_health(family) == ModelHealth.DEGRADED
    assert manager.get_quota(family).remaining_requests == 0

    # 429 makes can_schedule return False
    assert await manager.can_schedule(family) is False

    # Successful call restores health
    await manager.release(family, 50, 40.0, success=True)
    assert manager.get_health(family) == ModelHealth.HEALTHY
