"""Independent Emergency Stop kill-switch mechanism for the Computer Control Subsystem.

Provides an immediate, out-of-band fail-safe that halts all desktop interaction
even if the model, network WebSocket, or control loop is frozen or unresponsive.
"""

from __future__ import annotations

import threading
from typing import Any

from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class EmergencyStop:
    """Thread-safe singleton managing desktop physical control emergency halts."""

    _instance: EmergencyStop | None = None
    _lock = threading.Lock()
    _activated: bool = False
    _reason: str = ""
    _listeners: list[Any] = []

    def __new__(cls) -> EmergencyStop:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._activated = False
                cls._instance._reason = ""
                cls._instance._listeners = []
        return cls._instance

    @property
    def is_activated(self) -> bool:
        """Check whether emergency stop is currently active."""
        return self._activated

    @property
    def reason(self) -> str:
        """Reason provided for the emergency stop activation."""
        return self._reason

    def activate(self, reason: str = "Operator requested emergency stop") -> None:
        """Immediately trigger emergency stop, blocking all subsequent actions."""
        with self._lock:
            self._activated = True
            self._reason = reason
            logger.critical("emergency_stop_activated", reason=reason)

            # Notify listeners
            for listener in list(self._listeners):
                try:
                    listener(reason)
                except Exception as exc:
                    logger.warning("emergency_stop_listener_error", error=str(exc))

    def reset(self) -> None:
        """Reset emergency stop state after verified safe recovery."""
        with self._lock:
            self._activated = False
            self._reason = ""
            logger.info("emergency_stop_reset")

    def register_listener(self, listener: Any) -> None:
        """Register a callback invoked when emergency stop is activated."""
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def verify_clear(self) -> None:
        """Verify that emergency stop is NOT activated; fail closed if active."""
        if self._activated:
            raise RuntimeError(
                f"EMERGENCY_STOP_ACTIVATED: Desktop actions blocked ({self._reason or 'Emergency stop triggered'})."
            )


# Global accessors
def get_emergency_stop() -> EmergencyStop:
    """Obtain singleton instance of EmergencyStop."""
    return EmergencyStop()


def is_emergency_stop_active() -> bool:
    """Query current emergency stop status."""
    return get_emergency_stop().is_activated


def trigger_emergency_stop(reason: str = "Emergency stop triggered") -> None:
    """Trigger emergency stop immediately."""
    get_emergency_stop().activate(reason)
