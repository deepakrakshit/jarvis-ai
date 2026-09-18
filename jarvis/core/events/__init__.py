"""JARVIS Durable Event Plane Subsystem.

Provides enterprise-grade asynchronous event distribution, causation graph cycle and
depth tracking, distributed worker leases with fencing tokens, and Dead Letter Queue
quarantine (ARCHITECTURE.md Layer 12).
"""

from jarvis.core.events.bus import DurableEventBus, EventHandler
from jarvis.core.events.dlq import DeadLetterQueue, DLQRecord
from jarvis.core.events.lease import DurableLeaseManager, LeaseRecord, LeaseState
from jarvis.core.events.schemas import (
    CausationGraph,
    EventMessage,
    EventPriority,
)
from jarvis.core.exceptions import (
    CausalCycleError,
    CausalDepthExceededError,
    DLQMessageNotFoundError,
    EventPlaneError,
    LeaseFencingError,
    PoisonMessageError,
)

_DEFAULT_EVENT_BUS: DurableEventBus | None = None


def get_event_bus() -> DurableEventBus:
    """Retrieve or initialize the global singleton DurableEventBus."""
    global _DEFAULT_EVENT_BUS
    if _DEFAULT_EVENT_BUS is None:
        _DEFAULT_EVENT_BUS = DurableEventBus()
    return _DEFAULT_EVENT_BUS


__all__ = [
    "CausalCycleError",
    "CausalDepthExceededError",
    "CausationGraph",
    "DLQMessageNotFoundError",
    "DLQRecord",
    "DeadLetterQueue",
    "DurableEventBus",
    "DurableLeaseManager",
    "EventHandler",
    "EventMessage",
    "EventPlaneError",
    "EventPriority",
    "LeaseFencingError",
    "LeaseRecord",
    "LeaseState",
    "PoisonMessageError",
    "get_event_bus",
]
