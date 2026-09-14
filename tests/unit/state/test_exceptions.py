"""Tests for JARVIS Exception Taxonomy."""

from jarvis.core.exceptions import (
    CheckpointError,
    CircuitBreakerOpenError,
    ConfigurationError,
    JarvisError,
    PolicyViolationError,
    QuotaExceededError,
    SandboxTimeoutError,
    SecretNotFoundError,
    SecurityViolationError,
    StateTransitionError,
    TrustElevationError,
    VerificationFailureError,
)


def test_exception_hierarchy() -> None:
    """Verify inheritance relationships across the exception tree."""
    assert issubclass(ConfigurationError, JarvisError)
    assert issubclass(SecretNotFoundError, JarvisError)
    assert issubclass(SecurityViolationError, JarvisError)
    assert issubclass(TrustElevationError, SecurityViolationError)
    assert issubclass(PolicyViolationError, SecurityViolationError)
    assert issubclass(StateTransitionError, JarvisError)
    assert issubclass(CheckpointError, JarvisError)
    assert issubclass(QuotaExceededError, JarvisError)
    assert issubclass(CircuitBreakerOpenError, JarvisError)
    assert issubclass(SandboxTimeoutError, JarvisError)
    assert issubclass(VerificationFailureError, JarvisError)


def test_exception_string_formatting() -> None:
    """Verify error messages with and without structured details."""
    err_simple = JarvisError("Simple error")
    assert str(err_simple) == "Simple error"

    err_detailed = JarvisError("Detailed error", details={"key": "val", "code": 403})
    assert "Detailed error" in str(err_detailed)
    assert "'key': 'val'" in str(err_detailed)
    assert err_detailed.details["code"] == 403
