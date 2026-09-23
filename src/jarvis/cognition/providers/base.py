"""Base Abstract Model Provider Interface for JARVIS Cognition.

Enforces provider independence and strict contract adherence.
"""

from abc import ABC, abstractmethod

from jarvis.contracts.model import (
    ModelFamily,
    ModelInvocationRequest,
    ModelInvocationResponse,
    ModelProvider,
)


class BaseModelProvider(ABC):
    """Abstract provider capable of executing model invocation requests."""

    @property
    @abstractmethod
    def provider_type(self) -> ModelProvider:
        """The underlying infrastructure provider type."""
        ...

    @abstractmethod
    def supports(self, model_family: ModelFamily) -> bool:
        """Check if this provider supports the given model family."""
        ...

    @abstractmethod
    async def invoke(self, request: ModelInvocationRequest) -> ModelInvocationResponse:
        """Execute a normalized invocation request and return the response."""
        ...
