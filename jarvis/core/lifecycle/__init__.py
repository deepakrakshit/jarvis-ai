"""JARVIS Component Lifecycle Subsystem.

Provides lifecycle state tracking, quarantine, and repair workflows
(ARCHITECTURE.md Layer 11).
"""

from jarvis.core.lifecycle.manager import LifecycleManager
from jarvis.core.lifecycle.types import (
    LIFECYCLE_TRANSITIONS,
    ComponentLifecycleState,
    ComponentRecord,
    ComponentType,
    InvalidLifecycleTransitionError,
)

__all__ = [
    "LIFECYCLE_TRANSITIONS",
    "ComponentLifecycleState",
    "ComponentRecord",
    "ComponentType",
    "InvalidLifecycleTransitionError",
    "LifecycleManager",
]
