"""Authoritative Quota & Rate-Limit Manager for JARVIS.

Core Invariant: Quotas are a runtime resource.
Reads live provider response headers dynamically to maintain real-time
RPM, TPM, RPD, latency EMA, error rates, and health status across the
strict 6-model runtime allowlist.
"""

import asyncio
import re
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from jarvis.contracts.model import ModelFamily, ModelProvider, ModelQuota
from jarvis.telemetry import logger


def utc_now() -> datetime:
    """Return timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


class ModelHealth(str, Enum):
    """Health classification for runtime models."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class QuotaManager:
    """Dynamic tracker and scheduler for model quotas, rate limits, and health."""

    def __init__(self) -> None:
        self._quotas: Dict[ModelFamily, ModelQuota] = {}
        self._health: Dict[ModelFamily, ModelHealth] = {}
        self._consecutive_errors: Dict[ModelFamily, int] = {}
        self._lock = asyncio.Lock()
        self._initialize_defaults()

    def _initialize_defaults(self) -> None:
        """Initialize dynamic quota entries for the 6 approved model families."""
        defaults = [
            (ModelFamily.GEMINI_3_8_LIVE, ModelProvider.GOOGLE_GENAI, 60, 60000, 2000),
            (ModelFamily.GEMINI_3_1_FLASH_LITE, ModelProvider.GOOGLE_GENAI, 15, 1000000, 1500),
            (ModelFamily.GEMINI_3_5_FLASH_LITE, ModelProvider.GOOGLE_GENAI, 15, 1000000, 1500),
            (ModelFamily.GEMMA_4_31B, ModelProvider.GOOGLE_GENAI, 15, 1000000, 1500),
            (ModelFamily.GPT_OSS_120B, ModelProvider.GROQ, 30, 8000, 1000),
            (ModelFamily.QWEN_3_8_27B, ModelProvider.GROQ, 30, 8000, 1000),
        ]
        for family, provider, rpm, tpm, rpd in defaults:
            self._quotas[family] = ModelQuota(
                model_family=family,
                provider=provider,
                max_rpm=rpm,
                max_tpm=tpm,
                max_rpd=rpd,
                remaining_requests=rpm,
                remaining_tokens=tpm,
            )
            self._health[family] = ModelHealth.HEALTHY
            self._consecutive_errors[family] = 0

    def reset(self) -> None:
        """Reset all quotas and health metrics to default initial states."""
        self._initialize_defaults()

    def get_quota(self, model_family: ModelFamily) -> ModelQuota:
        """Retrieve the current quota state for a model family."""
        return self._quotas[model_family]

    def get_all_quotas(self) -> Dict[ModelFamily, ModelQuota]:
        """Retrieve all model family quota records."""
        return dict(self._quotas)

    def get_health(self, model_family: ModelFamily) -> ModelHealth:
        """Retrieve the operational health for a model family."""
        return self._health.get(model_family, ModelHealth.HEALTHY)

    async def can_schedule(
        self,
        model_family: ModelFamily,
        estimated_tokens: int = 500,
        max_latency_ms: Optional[float] = None,
    ) -> bool:
        """Determine if a task should be scheduled against this model family."""
        async with self._lock:
            quota = self._quotas.get(model_family)
            if not quota:
                return False

            # Check health
            health = self._health.get(model_family, ModelHealth.HEALTHY)
            if health == ModelHealth.UNAVAILABLE:
                return False

            # Check rate limits & quota availability
            if quota.remaining_requests <= 0:
                now_ts = time.time()
                if quota.reset_epoch_seconds > 0 and now_ts >= quota.reset_epoch_seconds:
                    quota.remaining_requests = quota.max_rpm
                    quota.remaining_tokens = quota.max_tpm
                    quota.reset_epoch_seconds = 0.0
                    self._health[model_family] = ModelHealth.HEALTHY
                else:
                    logger.warning(
                        f"Quota exhausted: {model_family.value} has 0 remaining requests"
                    )
                    return False

            if quota.remaining_tokens < estimated_tokens:
                logger.warning(
                    f"Token quota exhausted: {model_family.value} requires ~{estimated_tokens} tokens, has {quota.remaining_tokens}"
                )
                return False

            if quota.current_concurrency >= quota.max_concurrency:
                logger.warning(
                    f"Concurrency limit reached: {model_family.value} at {quota.current_concurrency}/{quota.max_concurrency}"
                )
                return False

            # Check latency budget if specified
            if max_latency_ms is not None and quota.recent_latency_ms > max_latency_ms:
                logger.warning(
                    f"Latency budget exceeded for {model_family.value}: {quota.recent_latency_ms:.1f}ms > {max_latency_ms:.1f}ms"
                )
                return False

            return True

    async def acquire(self, model_family: ModelFamily, estimated_tokens: int = 500) -> None:
        """Reserve quota and increment active concurrency before execution."""
        async with self._lock:
            quota = self._quotas[model_family]
            quota.current_concurrency += 1
            quota.current_rpm_used += 1
            quota.current_tpm_used += estimated_tokens
            quota.remaining_requests = max(0, quota.remaining_requests - 1)
            quota.remaining_tokens = max(0, quota.remaining_tokens - estimated_tokens)
            quota.last_checked_at = utc_now()

    async def release(
        self,
        model_family: ModelFamily,
        tokens_used: int,
        latency_ms: float,
        success: bool,
        response_headers: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Release concurrency, update EMA metrics, and absorb live provider headers."""
        async with self._lock:
            quota = self._quotas[model_family]
            quota.current_concurrency = max(0, quota.current_concurrency - 1)
            quota.last_checked_at = utc_now()

            # Latency Exponential Moving Average (EMA: alpha = 0.2)
            if quota.recent_latency_ms == 0.0:
                quota.recent_latency_ms = latency_ms
            else:
                quota.recent_latency_ms = (0.8 * quota.recent_latency_ms) + (0.2 * latency_ms)

            # Error tracking and health transitions
            if success:
                self._consecutive_errors[model_family] = 0
                self._health[model_family] = ModelHealth.HEALTHY
                quota.provider_healthy = True
            else:
                self._consecutive_errors[model_family] += 1
                err_count = self._consecutive_errors[model_family]
                if err_count >= 5:
                    self._health[model_family] = ModelHealth.UNAVAILABLE
                    quota.provider_healthy = False
                    logger.error(
                        f"Model {model_family.value} marked UNAVAILABLE ({err_count} consecutive errors)"
                    )
                elif err_count >= 2:
                    self._health[model_family] = ModelHealth.DEGRADED
                    logger.warning(
                        f"Model {model_family.value} marked DEGRADED ({err_count} consecutive errors)"
                    )

            # Absorb dynamic rate-limit headers (e.g. from Groq or proxies)
            if response_headers:
                self._update_from_headers(quota, response_headers)

    def _update_from_headers(self, quota: ModelQuota, headers: Dict[str, Any]) -> None:
        """Dynamically update quota counters from live HTTP response headers."""
        normalized = {k.lower(): str(v) for k, v in headers.items()}

        # Check remaining requests
        for key in ("x-ratelimit-remaining-requests", "ratelimit-remaining-requests"):
            if key in normalized:
                try:
                    quota.remaining_requests = int(normalized[key])
                except ValueError:
                    pass

        # Check remaining tokens
        for key in ("x-ratelimit-remaining-tokens", "ratelimit-remaining-tokens"):
            if key in normalized:
                try:
                    quota.remaining_tokens = int(normalized[key])
                except ValueError:
                    pass

        # Check reset seconds / timestamp
        for key in ("x-ratelimit-reset-requests", "ratelimit-reset-requests"):
            if key in normalized:
                try:
                    val = normalized[key]
                    if val.endswith("s"):
                        quota.reset_epoch_seconds = float(val[:-1])
                    else:
                        quota.reset_epoch_seconds = float(val)
                except ValueError:
                    pass

    async def record_error(self, model_family: ModelFamily, error_message: str) -> None:
        """Explicitly record a provider execution failure."""
        async with self._lock:
            self._consecutive_errors[model_family] += 1
            err_count = self._consecutive_errors[model_family]
            if (
                "429" in error_message
                or "rate_limit" in error_message.lower()
                or "resource_exhausted" in error_message.lower()
            ):
                # Rapid backoff on rate limit or quota exhaustion
                self._health[model_family] = ModelHealth.DEGRADED
                self._quotas[model_family].remaining_requests = 0

                # Extract explicit retry delay if reported by provider
                retry_delay = 60.0
                match = re.search(
                    r"retry\s+(?:in|delay[\'\":\s]+)\s*([0-9\.]+)\s*s?",
                    error_message,
                    re.I,
                )
                if match:
                    try:
                        retry_delay = float(match.group(1))
                    except ValueError:
                        retry_delay = 60.0

                effective_delay = max(30.0, retry_delay)
                self._quotas[model_family].reset_epoch_seconds = time.time() + effective_delay
                logger.warning(
                    f"Rate limit / Quota exhaustion detected for {model_family.value}: entered {effective_delay:.1f}s backoff"
                )
            elif err_count >= 3:
                self._health[model_family] = ModelHealth.DEGRADED


# Global quota manager singleton
quota_manager = QuotaManager()
