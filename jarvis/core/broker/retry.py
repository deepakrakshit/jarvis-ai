"""JARVIS Retry Classification and Ambiguous-Outcome Evaluation.

Determines whether a failed or timed-out tool invocation may be safely retried,
requires external verification, or must be strictly halted to prevent duplicate
side effects (ARCHITECTURE.md Layer 14 & Failure Modes #3, #46).
"""

import asyncio
from typing import Any

from jarvis.core.broker.types import IdempotencyClass, RetryClassification

NON_TRANSIENT_EXCEPTIONS = (
    PermissionError,
    FileNotFoundError,
    FileExistsError,
    IsADirectoryError,
    NotADirectoryError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AttributeError,
)

TRANSIENT_EXCEPTIONS = (
    TimeoutError,
    asyncio.TimeoutError,
    ConnectionError,
    ConnectionResetError,
    ConnectionRefusedError,
    ConnectionAbortedError,
    BrokenPipeError,
)


class RetryClassifier:
    """Classifies execution errors into safe retry decisions."""

    @staticmethod
    def is_transient_error(error: Exception) -> bool:
        """Return True if exception represents a network or transient provider timeout."""
        if isinstance(error, NON_TRANSIENT_EXCEPTIONS):
            return False

        if isinstance(error, TRANSIENT_EXCEPTIONS):
            return True

        # Check for HTTP status codes if present on the exception object
        status_code: int | None = getattr(error, "status_code", None) or getattr(
            error, "status", None
        )
        if status_code is not None:
            # 429 (Rate Limit), 502 (Bad Gateway), 503 (Service Unavailable), 504 (Gateway Timeout)
            return status_code in (429, 502, 503, 504)

        # Check message strings for typical transient indicators
        msg = str(error).lower()
        transient_phrases = (
            "timeout",
            "timed out",
            "connection refused",
            "connection reset",
            "rate limit",
            "too many requests",
            "service unavailable",
            "gateway timeout",
        )
        return any(phrase in msg for phrase in transient_phrases)

    @classmethod
    def classify(
        cls,
        tool_id: str,
        idempotency_class: IdempotencyClass,
        error: Exception,
        dispatched_to_provider: bool = False,
        extra_context: dict[str, Any] | None = None,
    ) -> RetryClassification:
        """Classify a failure into a safe operational retry decision.

        Args:
            tool_id: Identifier of the invoked capability.
            idempotency_class: Side effect classification.
            error: The captured exception.
            dispatched_to_provider: True if the request payload was transmitted to the
                downstream service before the failure occurred.
            extra_context: Optional metadata for telemetry.

        Returns:
            RetryClassification: RETRYABLE_SAFE, AMBIGUOUS_OUTCOME,
            REQUIRES_VERIFICATION, or NON_RETRYABLE_FATAL.
        """
        is_transient = cls.is_transient_error(error)

        # 1. If error is not transient, it is a deterministic fatal error (e.g. 400, validation, permission)
        if not is_transient:
            return RetryClassification.NON_RETRYABLE_FATAL

        # 2. If the request was NOT yet dispatched to the provider (e.g. DNS failure, local lease conflict),
        # it is always safe to retry because no external side effect could have occurred.
        if not dispatched_to_provider:
            return RetryClassification.RETRYABLE_SAFE

        # 3. Request WAS dispatched and transient error occurred:
        # Check idempotency taxonomy
        if idempotency_class == IdempotencyClass.IDEMPOTENT:
            # GET / Read-only / Pure functions: unconditionally safe to retry
            return RetryClassification.RETRYABLE_SAFE

        if idempotency_class == IdempotencyClass.IDEMPOTENT_WITH_KEY:
            # Provider supports deduplication with identical logical_effect_id
            return RetryClassification.RETRYABLE_SAFE

        if idempotency_class == IdempotencyClass.NON_IDEMPOTENT:
            # CRITICAL FAILURE MODE #3 & #46 DEFENSE:
            # Request was accepted or sent across the network to a non-idempotent tool,
            # but response timed out or network dropped.
            # Outcome is AMBIGUOUS: re-trying blindly could duplicate mutations.
            return RetryClassification.AMBIGUOUS_OUTCOME

        if idempotency_class == IdempotencyClass.TRANSACTIONAL:
            # Transactional operations require SAGA compensation or rollback
            return RetryClassification.AMBIGUOUS_OUTCOME

        # Default fail-safe: UNKNOWN requires verification
        return RetryClassification.REQUIRES_VERIFICATION
