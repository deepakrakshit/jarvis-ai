"""JARVIS Error Taxonomy and Exception Hierarchy.

Every failure in JARVIS maps to a deterministic, typed exception.
"""

from typing import Any


class JarvisError(Exception):
    """Base exception for all JARVIS errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} | Details: {self.details}"
        return self.message


# --- Configuration & Environment Errors ---


class ConfigurationError(JarvisError):
    """Raised when system configuration is invalid or missing."""


class SecretNotFoundError(JarvisError):
    """Raised when a requested secret is not found in the vault."""


# --- Security & Policy Errors ---


class SecurityViolationError(JarvisError):
    """Base exception for security boundary violations."""


class TrustElevationError(SecurityViolationError):
    """Raised when an untrusted input attempts to elevate authority."""


class PolicyViolationError(SecurityViolationError):
    """Raised when an operation violates centralized policy engine rules."""


class CapabilityFirewallError(SecurityViolationError):
    """Raised when a capability invocation violates the active firewall projection."""


class IFCViolationError(SecurityViolationError):
    """Raised when an operation violates Information-Flow Control (IFC) lattice rules."""


class SinkEnforcementError(IFCViolationError):
    """Raised when sensitive or untrusted data is routed to an unauthorized sink."""


class DataContaminationError(IFCViolationError):
    """Raised when data integrity or confidentiality taint is illegally discarded."""


class PromptInjectionDetectedError(SecurityViolationError):
    """Raised when heuristic or behavioral scanning detects prompt injection payloads."""


# --- State & Control Plane Errors ---


class StateTransitionError(JarvisError):
    """Raised when an invalid task state transition is attempted."""


class CheckpointError(JarvisError):
    """Raised when checkpoint serialization or retrieval fails."""


# --- Gateway & Resource Errors ---


class GatewayError(JarvisError):
    """Base exception for model gateway operations."""


class QuotaExceededError(GatewayError):
    """Raised when provider or local quota headroom is exhausted."""


class ModelProviderError(GatewayError):
    """Raised when an upstream model provider returns a permanent or transient error."""


class CircuitBreakerOpenError(GatewayError):
    """Raised when an invocation is rejected because a circuit breaker is OPEN."""


# --- Execution & Sandbox Errors ---


class SandboxExecutionError(JarvisError):
    """Raised when sandboxed execution fails or violates confinement."""


class SandboxTimeoutError(SandboxExecutionError):
    """Raised when execution exceeds the allocated wall-time timeout."""


class IdempotencyConflictError(JarvisError):
    """Raised when an operation conflicts with an existing idempotency lease or key."""


class VerificationFailureError(JarvisError):
    """Raised when external state verification fails to corroborate an effect."""
