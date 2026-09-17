"""JARVIS Model Quota Manager and Lease Engine.

Manages RPM, RPD, TPM, and Grounding quota limits across isolated domains.
Features serialized per-model async locking, mathematically safe quarantine thresholds,
two-phase reservation leases, and permanent orphan retention.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from jarvis.core.exceptions import QuotaExceededError
from jarvis.core.gateway.interfaces import ProviderRateLimitHeaders
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class QuotaDomain(StrEnum):
    """Isolated quota domains preventing cross-domain fungibility."""

    GENERATION = "GENERATION"
    """LLM text, reasoning, and tool calling generation."""

    EMBEDDING = "EMBEDDING"
    """Vector embeddings generation."""

    SEARCH_GROUNDING = "SEARCH_GROUNDING"
    """Google Search grounding operations (isolated 500/day allowance)."""

    LIVE_AUDIO = "LIVE_AUDIO"
    """Dedicated realtime bidirectional voice and audio streaming domain."""


class QuotaMeterType(StrEnum):
    """Typed representation of provider quota accounting semantics.

    Prevents representing 'unlimited' as float('inf') or mathematical infinity.
    """

    LIMITED = "LIMITED"
    """Enforces strict discrete RPM, RPD, or TPM limits."""

    UNMETERED_REPORTED = "UNMETERED_REPORTED"
    """Provider reports unmetered/unlimited capacity; system measures actual usage without infinite assumptions."""

    UNKNOWN = "UNKNOWN"
    """Quota limit is unmeasured or dynamically inferred from HTTP response headers."""


class LeaseState(StrEnum):
    """Lifecycle state of a two-phase quota reservation lease."""

    RESERVED = "RESERVED"
    """Tokens and request slot tentatively reserved prior to API dispatch."""

    RECONCILED = "RECONCILED"
    """Execution succeeded; actual tokens reconciled with provider usage."""

    FAILED = "FAILED"
    """Execution failed or aborted; marked as orphaned for quarantine tracking."""

    EXPIRED = "EXPIRED"
    """Lease exceeded timeout window without reconciliation."""


class QuotaLease(BaseModel):
    """Immutable record representing an allocated quota reservation lease."""

    lease_id: str = Field(default_factory=lambda: f"lease_{uuid.uuid4().hex[:12]}")
    model_id: str
    domain: QuotaDomain
    estimated_tokens: int = Field(default=100, ge=1)
    actual_tokens: int | None = None
    state: LeaseState = LeaseState.RESERVED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reconciled_at: datetime | None = None
    error_reason: str | None = None


class ModelQuotaManager:
    """Thread-safe and async-safe quota tracker for a specific model."""

    def __init__(
        self,
        model_id: str,
        rpm_limit: int | None = None,
        rpd_limit: int | None = None,
        tpm_limit: int | None = None,
        meter_type: QuotaMeterType = QuotaMeterType.LIMITED,
    ) -> None:
        self.model_id = model_id
        self.rpm_limit = rpm_limit
        self.rpd_limit = rpd_limit
        self.tpm_limit = tpm_limit
        self.meter_type = meter_type

        # Live counters
        self.requests_this_minute = 0
        self.tokens_this_minute = 0
        self.requests_today = 0
        self.minute_window_start = datetime.now(UTC)

        # Live audio specific tracking metrics
        self.active_live_sessions = 0
        self.total_audio_seconds = 0.0
        self.live_audio_tokens = 0

        # Leases and quarantine state
        self.active_leases: dict[str, QuotaLease] = {}
        self.orphaned_leases: list[QuotaLease] = []
        self.is_quarantined = False

        # CRITICAL: Serialized per-model lock for all quota mutations
        self._lock = asyncio.Lock()

    def register_live_session_start(self) -> None:
        """Track initiation of an active bidirectional live audio session."""
        self.active_live_sessions += 1

    def register_live_session_end(self, duration_seconds: float = 0.0) -> None:
        """Track termination of a live audio session and accumulate total duration."""
        self.active_live_sessions = max(0, self.active_live_sessions - 1)
        self.total_audio_seconds += max(0.0, duration_seconds)

    def record_live_audio_usage(self, audio_seconds: float, tokens: int = 0) -> None:
        """Record real-time audio chunk metrics and token usage."""
        self.total_audio_seconds += max(0.0, audio_seconds)
        self.live_audio_tokens += max(0, tokens)

    def _reset_minute_window_if_needed(self) -> None:
        """Reset 60-second sliding/minute window counters."""
        now = datetime.now(UTC)
        if (now - self.minute_window_start).total_seconds() >= 60.0:
            self.requests_this_minute = 0
            self.tokens_this_minute = 0
            self.minute_window_start = now

    def check_quarantine(self) -> bool:
        """Evaluate mathematically safe quarantine threshold.

        Prevents division-by-zero or premature quarantine on low RPM models (e.g. RPM=1).
        """
        if len(self.orphaned_leases) >= 5 or (
            self.rpm_limit is not None
            and self.rpm_limit > 0
            and (len(self.orphaned_leases) * 2) >= self.rpm_limit
        ):
            self.is_quarantined = True
        return self.is_quarantined

    async def reserve_lease(
        self,
        estimated_tokens: int = 100,
        domain: QuotaDomain = QuotaDomain.GENERATION,
    ) -> QuotaLease:
        """Reserve a two-phase quota lease.

        Serialized through the per-model lock to prevent race conditions.
        """
        async with self._lock:
            self._reset_minute_window_if_needed()

            if self.check_quarantine():
                logger.error(
                    "model_quarantined", model_id=self.model_id, orphans=len(self.orphaned_leases)
                )
                raise QuotaExceededError(
                    f"Model '{self.model_id}' is QUARANTINED due to {len(self.orphaned_leases)} "
                    f"orphaned/failed leases. Failover required."
                )

            # Enforce limits only for metered configurations (unmetered models measure usage without hard blocking)
            if self.meter_type != QuotaMeterType.UNMETERED_REPORTED:
                # Check RPM limit
                if self.rpm_limit is not None and self.requests_this_minute >= self.rpm_limit:
                    logger.warning(
                        "model_rpm_exhausted", model_id=self.model_id, rpm=self.requests_this_minute
                    )
                    raise QuotaExceededError(
                        f"Model '{self.model_id}' RPM limit ({self.rpm_limit}) exhausted."
                    )

                # Check TPM limit
                if (
                    self.tpm_limit is not None
                    and (self.tokens_this_minute + estimated_tokens) > self.tpm_limit
                ):
                    logger.warning(
                        "model_tpm_exhausted", model_id=self.model_id, tpm=self.tokens_this_minute
                    )
                    raise QuotaExceededError(
                        f"Model '{self.model_id}' TPM limit ({self.tpm_limit}) exhausted."
                    )

                # Check RPD limit
                if self.rpd_limit is not None and self.requests_today >= self.rpd_limit:
                    logger.warning(
                        "model_rpd_exhausted", model_id=self.model_id, rpd=self.requests_today
                    )
                    raise QuotaExceededError(
                        f"Model '{self.model_id}' RPD limit ({self.rpd_limit}) exhausted."
                    )

            # Tentatively reserve capacity
            self.requests_this_minute += 1
            self.tokens_this_minute += estimated_tokens
            self.requests_today += 1

            lease = QuotaLease(
                model_id=self.model_id,
                domain=domain,
                estimated_tokens=estimated_tokens,
                state=LeaseState.RESERVED,
            )
            self.active_leases[lease.lease_id] = lease
            return lease

    async def reconcile_lease(self, lease_id: str, actual_tokens: int) -> None:
        """Reconcile tentative reservation with actual token count returned by provider."""
        async with self._lock:
            lease = self.active_leases.pop(lease_id, None)
            if not lease:
                logger.warning("lease_reconcile_missing", lease_id=lease_id, model_id=self.model_id)
                return

            delta = actual_tokens - lease.estimated_tokens
            self.tokens_this_minute += delta

            lease.actual_tokens = actual_tokens
            lease.state = LeaseState.RECONCILED
            lease.reconciled_at = datetime.now(UTC)

    async def fail_lease(self, lease_id: str, reason: str | None = None) -> None:
        """Mark a lease as FAILED and retain as an orphan (orphans are never deleted)."""
        async with self._lock:
            lease = self.active_leases.pop(lease_id, None)
            if not lease:
                return

            lease.state = LeaseState.FAILED
            lease.error_reason = reason
            lease.reconciled_at = datetime.now(UTC)

            # Retain in permanent orphaned list for audit and quarantine analysis
            self.orphaned_leases.append(lease)
            self.check_quarantine()

    async def update_from_headers(self, headers: ProviderRateLimitHeaders) -> None:
        """Synchronize local tracking state with upstream provider HTTP headers."""
        async with self._lock:
            if headers.remaining_requests_minute is not None and self.rpm_limit is not None:
                self.requests_this_minute = max(
                    0, self.rpm_limit - headers.remaining_requests_minute
                )
            if headers.remaining_tokens_minute is not None and self.tpm_limit is not None:
                self.tokens_this_minute = max(0, self.tpm_limit - headers.remaining_tokens_minute)
            if headers.remaining_requests_day is not None and self.rpd_limit is not None:
                self.requests_today = max(0, self.rpd_limit - headers.remaining_requests_day)
