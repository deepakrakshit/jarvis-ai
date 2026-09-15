"""JARVIS Component Lifecycle Manager.

Monitors, validates, and orchestrates the health state machine for all components
including tools, specialists, models, and skills (ARCHITECTURE.md Layer 11).
"""

from threading import Lock

from jarvis.core.lifecycle.types import (
    ComponentLifecycleState,
    ComponentRecord,
    ComponentType,
)
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class LifecycleManager:
    """Thread-safe lifecycle manager governing component operational states."""

    def __init__(self, max_consecutive_errors: int = 3) -> None:
        self.max_consecutive_errors = max_consecutive_errors
        self._records: dict[str, ComponentRecord] = {}
        self._lock = Lock()

    def register_component(
        self,
        component_id: str,
        component_type: ComponentType,
        version: str = "1.0.0",
    ) -> ComponentRecord:
        """Register a new component in REGISTERED state."""
        with self._lock:
            if component_id in self._records:
                return self._records[component_id]

            record = ComponentRecord(
                component_id=component_id,
                component_type=component_type,
                state=ComponentLifecycleState.REGISTERED,
                version=version,
            )
            self._records[component_id] = record
            logger.info(
                "component_registered", component_id=component_id, type=component_type.value
            )
            return record

    def validate_component(self, component_id: str) -> ComponentRecord:
        """Validate component integrity and schema."""
        with self._lock:
            record = self._records[component_id]
            record.transition_to(ComponentLifecycleState.VALIDATED)
            logger.info("component_validated", component_id=component_id)
            return record

    def enable_component(self, component_id: str) -> ComponentRecord:
        """Enable component for runtime execution."""
        with self._lock:
            record = self._records[component_id]
            record.transition_to(ComponentLifecycleState.ENABLED)
            logger.info("component_enabled", component_id=component_id)
            return record

    def get_component(self, component_id: str) -> ComponentRecord | None:
        """Retrieve a component record by ID."""
        with self._lock:
            return self._records.get(component_id)

    def is_available(self, component_id: str) -> bool:
        """Return True if component is in ENABLED or RUNNING state."""
        with self._lock:
            record = self._records.get(component_id)
            if not record:
                return False
            return record.state in (
                ComponentLifecycleState.ENABLED,
                ComponentLifecycleState.RUNNING,
            )

    def record_invocation_start(self, component_id: str) -> None:
        """Record the start of an execution invocation."""
        with self._lock:
            record = self._records.get(component_id)
            if record and record.state == ComponentLifecycleState.ENABLED:
                record.transition_to(ComponentLifecycleState.RUNNING)
            if record:
                record.total_invocations += 1

    def record_invocation_success(self, component_id: str) -> None:
        """Record a successful execution, clearing consecutive errors."""
        with self._lock:
            record = self._records.get(component_id)
            if record:
                record.consecutive_errors = 0
                if record.state == ComponentLifecycleState.RUNNING:
                    record.transition_to(ComponentLifecycleState.ENABLED)

    def record_invocation_failure(self, component_id: str, error: str) -> ComponentRecord | None:
        """Record an execution failure, potentially triggering quarantine."""
        with self._lock:
            record = self._records.get(component_id)
            if not record:
                return None

            record.total_errors += 1
            record.consecutive_errors += 1

            if record.consecutive_errors >= self.max_consecutive_errors:
                reason = (
                    f"Component exceeded failure limit ({record.consecutive_errors}/"
                    f"{self.max_consecutive_errors}): {error}"
                )
                logger.warning("component_quarantined", component_id=component_id, reason=reason)
                record.transition_to(ComponentLifecycleState.QUARANTINED, reason=reason)
            elif record.state == ComponentLifecycleState.RUNNING:
                record.transition_to(ComponentLifecycleState.ENABLED)

            return record

    def repair_component(self, component_id: str, re_enable: bool = True) -> ComponentRecord:
        """Initiate repair on a quarantined component."""
        with self._lock:
            record = self._records[component_id]
            record.transition_to(ComponentLifecycleState.REPAIR)
            logger.info("component_repair_started", component_id=component_id)

            if re_enable:
                record.transition_to(ComponentLifecycleState.VALIDATED)
                record.transition_to(ComponentLifecycleState.ENABLED)
                logger.info("component_repair_completed", component_id=component_id)

            return record

    def disable_component(self, component_id: str) -> ComponentRecord:
        """Administratively disable a component."""
        with self._lock:
            record = self._records[component_id]
            record.transition_to(ComponentLifecycleState.DISABLED)
            logger.info("component_disabled", component_id=component_id)
            return record

    def retire_component(self, component_id: str) -> ComponentRecord:
        """Permanently decommission a component."""
        with self._lock:
            record = self._records[component_id]
            record.transition_to(ComponentLifecycleState.RETIRED)
            logger.info("component_retired", component_id=component_id)
            return record
