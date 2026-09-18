"""JARVIS Verifier Base Interface.

Defines the contract for pluggable verification strategies across external state,
execution runtime, semantic integrity, and evidence validation.
"""

from abc import ABC, abstractmethod

from jarvis.core.verification.types import (
    VerificationRequest,
    VerificationResult,
    VerifierType,
)


class BaseVerifier(ABC):
    """Abstract base class for all capability and state verifiers."""

    @property
    @abstractmethod
    def verifier_type(self) -> VerifierType:
        """Categorical classification of this verifier."""
        ...

    @abstractmethod
    async def verify(self, request: VerificationRequest) -> VerificationResult:
        """Execute verification check against the target system or observation."""
        ...
