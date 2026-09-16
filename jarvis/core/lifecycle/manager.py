"""JARVIS Component Lifecycle Manager.

Monitors, validates, and orchestrates the health state machine for all components
including tools, specialists, models, and skills (ARCHITECTURE.md Layer 11, Contract 16).
"""

import inspect
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from jarvis.core.lifecycle.types import (
    ComponentLifecycleState,
    ComponentRecord,
    ComponentType,
    HealthProbeResult,
)
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class LifecycleManager:
    """Thread-safe lifecycle manager governing component operational states (Contract 16)."""

    def __init__(self, max_consecutive_errors: int = 3) -> None:
        self.max_consecutive_errors = max_consecutive_errors
        self._records: dict[str, ComponentRecord] = {}
        self._probes: dict[str, Callable[..., Any]] = {}
        self._repair_handlers: dict[str, Callable[..., Any]] = {}
        self._lock = Lock()

    def register_component(
        self,
        component_id: str,
        component_type: ComponentType,
        version: str = "1.0.0",
        metadata: dict[str, Any] | None = None,
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
                metadata=metadata or {},
            )
            self._records[component_id] = record
            logger.info(
                "component_registered",
                component_id=component_id,
                type=component_type.value,
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
                record.last_success_at = datetime.now(UTC)
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
            record.last_failure_at = datetime.now(UTC)

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

    def quarantine_component(self, component_id: str, reason: str) -> ComponentRecord:
        """Explicitly quarantine a component due to health, security, or policy violation."""
        with self._lock:
            record = self._records[component_id]
            record.transition_to(ComponentLifecycleState.QUARANTINED, reason=reason)
            logger.warning(
                "component_quarantined_explicitly", component_id=component_id, reason=reason
            )
            return record

    def register_health_probe(
        self,
        component_id: str,
        probe_fn: Callable[..., HealthProbeResult | Awaitable[HealthProbeResult]],
    ) -> None:
        """Register a diagnostic health probe callable for a component."""
        with self._lock:
            self._probes[component_id] = probe_fn
            logger.info("health_probe_registered", component_id=component_id)

    def register_repair_handler(
        self,
        component_id: str,
        handler_fn: Callable[..., bool | Awaitable[bool]],
    ) -> None:
        """Register a self-healing repair callback for a quarantined component."""
        with self._lock:
            self._repair_handlers[component_id] = handler_fn
            logger.info("repair_handler_registered", component_id=component_id)

    async def run_health_probe(self, component_id: str) -> HealthProbeResult:
        """Execute diagnostic health check on a specific component (Contract 16)."""
        probe_fn = None
        record = None
        with self._lock:
            record = self._records.get(component_id)
            probe_fn = self._probes.get(component_id)

        if not record:
            return HealthProbeResult(
                component_id=component_id,
                healthy=False,
                error=f"Component '{component_id}' is not registered in LifecycleManager",
            )

        start = time.perf_counter()
        if probe_fn is None:
            # Default health check: verify component is not quarantined or disabled
            is_ok = record.state not in (
                ComponentLifecycleState.QUARANTINED,
                ComponentLifecycleState.DISABLED,
                ComponentLifecycleState.RETIRED,
            )
            res = HealthProbeResult(
                component_id=component_id,
                healthy=is_ok,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=record.quarantine_reason if not is_ok else None,
                details={"state": record.state.value},
            )
        else:
            try:
                if inspect.iscoroutinefunction(probe_fn):
                    res = await probe_fn()
                else:
                    res = probe_fn()

                if not isinstance(res, HealthProbeResult):
                    res = HealthProbeResult(
                        component_id=component_id,
                        healthy=bool(res),
                        latency_ms=(time.perf_counter() - start) * 1000.0,
                    )
                else:
                    res.latency_ms = (time.perf_counter() - start) * 1000.0
            except Exception as exc:
                res = HealthProbeResult(
                    component_id=component_id,
                    healthy=False,
                    latency_ms=(time.perf_counter() - start) * 1000.0,
                    error=str(exc),
                )

        with self._lock:
            record.last_health_probe = res
            if not res.healthy and record.state in (
                ComponentLifecycleState.ENABLED,
                ComponentLifecycleState.RUNNING,
                ComponentLifecycleState.VALIDATED,
            ):
                reason = f"Health probe failed: {res.error or 'Unhealthy diagnostic'}"
                record.transition_to(ComponentLifecycleState.QUARANTINED, reason=reason)
                logger.warning(
                    "component_quarantined_by_probe", component_id=component_id, reason=reason
                )

        return res

    async def run_all_health_probes(self) -> dict[str, HealthProbeResult]:
        """Execute health checks across all registered components."""
        component_ids = list(self._records.keys())
        results: dict[str, HealthProbeResult] = {}
        for cid in component_ids:
            results[cid] = await self.run_health_probe(cid)
        return results

    async def attempt_repair(self, component_id: str) -> bool:
        """Attempt automated self-healing on a quarantined component."""
        record = None
        handler = None
        with self._lock:
            record = self._records.get(component_id)
            handler = self._repair_handlers.get(component_id)

        if not record:
            return False

        if record.state != ComponentLifecycleState.QUARANTINED:
            return record.state in (
                ComponentLifecycleState.ENABLED,
                ComponentLifecycleState.RUNNING,
            )

        with self._lock:
            record.transition_to(ComponentLifecycleState.REPAIR)
            logger.info("component_repair_started", component_id=component_id)

        repair_ok = True
        if handler:
            try:
                if inspect.iscoroutinefunction(handler):
                    repair_ok = await handler()
                else:
                    repair_ok = handler()
            except Exception as exc:
                logger.error(
                    "component_repair_handler_failed", component_id=component_id, error=str(exc)
                )
                repair_ok = False

        if repair_ok:
            probe_result = await self.run_health_probe(component_id)
            repair_ok = probe_result.healthy

        with self._lock:
            if repair_ok:
                record.transition_to(ComponentLifecycleState.VALIDATED)
                record.transition_to(ComponentLifecycleState.ENABLED)
                record.consecutive_errors = 0
                record.quarantine_reason = None
                logger.info("component_repair_succeeded", component_id=component_id)
                return True
            else:
                record.transition_to(
                    ComponentLifecycleState.QUARANTINED,
                    reason="Self-healing repair routine failed validation probe",
                )
                logger.warning("component_repair_failed", component_id=component_id)
                return False

    def repair_component(self, component_id: str, re_enable: bool = True) -> ComponentRecord:
        """Synchronous lifecycle repair transition helper."""
        with self._lock:
            record = self._records[component_id]
            record.transition_to(ComponentLifecycleState.REPAIR)
            logger.info("component_repair_started", component_id=component_id)

            if re_enable:
                record.transition_to(ComponentLifecycleState.VALIDATED)
                record.transition_to(ComponentLifecycleState.ENABLED)
                record.consecutive_errors = 0
                record.quarantine_reason = None
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

    def list_components(
        self,
        component_type: ComponentType | None = None,
        state: ComponentLifecycleState | None = None,
    ) -> list[ComponentRecord]:
        """List component records filtered by type or operational state."""
        with self._lock:
            records = list(self._records.values())

        if component_type:
            records = [r for r in records if r.component_type == component_type]
        if state:
            records = [r for r in records if r.state == state]
        return records

    def get_health_status(self) -> dict[str, Any]:
        """Return system-wide component health and readiness summary."""
        with self._lock:
            total = len(self._records)
            by_state: dict[str, int] = {}
            by_type: dict[str, int] = {}
            quarantined: list[dict[str, Any]] = []

            for r in self._records.values():
                by_state[r.state.value] = by_state.get(r.state.value, 0) + 1
                by_type[r.component_type.value] = by_type.get(r.component_type.value, 0) + 1
                if r.state == ComponentLifecycleState.QUARANTINED:
                    quarantined.append(
                        {
                            "component_id": r.component_id,
                            "type": r.component_type.value,
                            "reason": r.quarantine_reason,
                            "count": r.quarantine_count,
                        }
                    )

            is_ready = total > 0 and len(quarantined) == 0
            return {
                "total_components": total,
                "ready": is_ready,
                "by_state": by_state,
                "by_type": by_type,
                "quarantined": quarantined,
            }
