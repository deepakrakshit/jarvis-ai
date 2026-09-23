"""Capability Handler Registry for JARVIS Action Broker.

Maps canonical capabilities to validated execution handlers.
"""

from typing import Any, Callable, Dict, Optional

from jarvis.contracts.action import ActionRequest


class CapabilityRegistry:
    """Central registry of executable capability handlers."""

    def __init__(self) -> None:
        self._handlers: Dict[str, Callable[[ActionRequest], Any]] = {}

    def register(self, capability: str, handler: Callable[[ActionRequest], Any]) -> None:
        """Register a handler for a canonical capability."""
        self._handlers[capability] = handler

    def get_handler(self, capability: str) -> Optional[Callable[[ActionRequest], Any]]:
        """Retrieve registered handler for a capability."""
        return self._handlers.get(capability)

    def is_registered(self, capability: str) -> bool:
        """Check if capability handler is registered."""
        return capability in self._handlers


# Global singleton instance
capability_registry = CapabilityRegistry()
