"""Unit tests for RetryClassifier and ambiguous-outcome detection."""

from jarvis.core.broker.retry import RetryClassifier
from jarvis.core.broker.types import IdempotencyClass, RetryClassification


def test_fatal_errors_are_non_retryable() -> None:
    """Deterministic errors (validation, key, permission) must be classified as NON_RETRYABLE_FATAL."""
    err1 = ValueError("Invalid parameter value")
    err2 = PermissionError("Access denied")
    err3 = KeyError("missing_field")

    for err in (err1, err2, err3):
        res = RetryClassifier.classify(
            tool_id="test_tool",
            idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
            error=err,
            dispatched_to_provider=True,
        )
        assert res == RetryClassification.NON_RETRYABLE_FATAL


def test_transient_error_before_dispatch_is_safe_to_retry() -> None:
    """If timeout or connection error occurs before payload sent to provider, it is always RETRYABLE_SAFE."""
    err = ConnectionError("Could not resolve host")
    res = RetryClassifier.classify(
        tool_id="remote_api",
        idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
        error=err,
        dispatched_to_provider=False,
    )
    assert res == RetryClassification.RETRYABLE_SAFE


def test_transient_error_after_dispatch_on_idempotent_is_safe() -> None:
    """Timeouts on IDEMPOTENT or IDEMPOTENT_WITH_KEY tools can be safely retried."""
    err = TimeoutError("Request timed out")
    res_idempotent = RetryClassifier.classify(
        tool_id="search.query",
        idempotency_class=IdempotencyClass.IDEMPOTENT,
        error=err,
        dispatched_to_provider=True,
    )
    assert res_idempotent == RetryClassification.RETRYABLE_SAFE

    res_with_key = RetryClassifier.classify(
        tool_id="payment.charge",
        idempotency_class=IdempotencyClass.IDEMPOTENT_WITH_KEY,
        error=err,
        dispatched_to_provider=True,
    )
    assert res_with_key == RetryClassification.RETRYABLE_SAFE


def test_transient_error_after_dispatch_on_non_idempotent_is_ambiguous() -> None:
    """CRITICAL: Timeout after dispatch on NON_IDEMPOTENT tool must be AMBIGUOUS_OUTCOME."""
    err = TimeoutError("Timeout waiting for HTTP response")
    res = RetryClassifier.classify(
        tool_id="email.send",
        idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
        error=err,
        dispatched_to_provider=True,
    )
    assert res == RetryClassification.AMBIGUOUS_OUTCOME


def test_unknown_idempotency_requires_verification() -> None:
    """Failure on UNKNOWN idempotency class requires external verification."""
    err = TimeoutError("Read timeout")
    res = RetryClassifier.classify(
        tool_id="custom.tool",
        idempotency_class=IdempotencyClass.UNKNOWN,
        error=err,
        dispatched_to_provider=True,
    )
    assert res == RetryClassification.REQUIRES_VERIFICATION
