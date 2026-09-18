"""JARVIS Chaos Fault Injector Subsystem.

Provides controlled, deterministic fault injection across model gateways,
action brokers, distributed leases, and external verification witnesses.
"""

from collections.abc import Generator
from contextlib import contextmanager
from threading import RLock
from typing import Any

from jarvis.chaos.schemas import ChaosFault, ChaosFaultType
from jarvis.core.broker.types import LeaseAcquisitionError
from jarvis.core.exceptions import (
    ModelProviderError,
    QuotaExceededError,
)
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class ChaosFaultInjector:
    """Thread-safe controller managing active chaos fault rules."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._active_faults: list[ChaosFault] = []

    def add_fault(self, fault: ChaosFault) -> None:
        """Register a new active fault."""
        with self._lock:
            self._active_faults.append(fault)
            logger.info(
                "chaos_fault_registered",
                fault_type=fault.fault_type.value,
                target=fault.target_component,
            )

    def clear_faults(self) -> None:
        """Clear all active faults."""
        with self._lock:
            self._active_faults.clear()

    @contextmanager
    def inject(
        self,
        fault_type: ChaosFaultType,
        target_component: str,
        trigger_limit: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> Generator[ChaosFault, None, None]:
        """Context manager temporarily activating a synthetic chaos fault."""
        fault = ChaosFault(
            fault_type=fault_type,
            target_component=target_component,
            trigger_limit=trigger_limit,
            metadata=metadata or {},
        )
        self.add_fault(fault)
        try:
            yield fault
        finally:
            with self._lock:
                if fault in self._active_faults:
                    self._active_faults.remove(fault)

    def trigger_if_active(
        self, component: str, expected_type: ChaosFaultType | None = None
    ) -> None:
        """Check active rules and raise appropriate exception if matching fault triggers."""
        with self._lock:
            for fault in list(self._active_faults):
                if fault.should_trigger(component):
                    if expected_type is not None and fault.fault_type != expected_type:
                        continue

                    fault.record_triggered()
                    logger.warning(
                        "chaos_fault_triggered",
                        fault_type=fault.fault_type.value,
                        component=component,
                        trigger_count=fault.trigger_count,
                    )

                    if fault.fault_type == ChaosFaultType.MODEL_RATE_LIMIT:
                        raise QuotaExceededError(
                            f"Chaos Injection: Rate limit quota exceeded (429) for {component}"
                        )
                    elif fault.fault_type == ChaosFaultType.MODEL_TIMEOUT:
                        raise TimeoutError(
                            f"Chaos Injection: Network connection timed out for {component}"
                        )
                    elif fault.fault_type == ChaosFaultType.MODEL_OUTAGE:
                        raise ModelProviderError(
                            f"Chaos Injection: 503 Service Unavailable outage for {component}"
                        )
                    elif fault.fault_type == ChaosFaultType.BROKER_AMBIGUOUS_DROP:
                        raise ConnectionResetError(
                            f"Chaos Injection: Socket disconnected during mutating effect in {component}"
                        )
                    elif fault.fault_type == ChaosFaultType.LEASE_STALE_FENCING:
                        raise LeaseAcquisitionError(
                            resource_id=component,
                            holder_id="chaos_holder",
                            message=f"Chaos Injection: Stale fencing token rejected in {component}",
                        )
                    elif fault.fault_type == ChaosFaultType.RESOURCE_HASH_MISMATCH:
                        raise ValueError(
                            f"Chaos Injection: External resource state hash modified in {component}"
                        )


_DEFAULT_INJECTOR: ChaosFaultInjector | None = None


def get_fault_injector() -> ChaosFaultInjector:
    """Retrieve or initialize global ChaosFaultInjector singleton."""
    global _DEFAULT_INJECTOR
    if _DEFAULT_INJECTOR is None:
        _DEFAULT_INJECTOR = ChaosFaultInjector()
    return _DEFAULT_INJECTOR
