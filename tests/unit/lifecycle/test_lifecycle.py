"""Unit tests for Component Lifecycle Manager and transitions."""

import pytest

from jarvis.core.lifecycle.manager import LifecycleManager
from jarvis.core.lifecycle.types import (
    ComponentLifecycleState,
    ComponentRecord,
    ComponentType,
    InvalidLifecycleTransitionError,
)


def _assert_state(rec: ComponentRecord, expected: ComponentLifecycleState) -> None:
    assert rec.state == expected


def test_component_registration_and_validation() -> None:
    """Test standard component onboarding: REGISTERED -> VALIDATED -> ENABLED."""
    mgr = LifecycleManager()
    rec = mgr.register_component("native:fs:read_file", ComponentType.TOOL)
    _assert_state(rec, ComponentLifecycleState.REGISTERED)

    mgr.validate_component("native:fs:read_file")
    _assert_state(rec, ComponentLifecycleState.VALIDATED)

    mgr.enable_component("native:fs:read_file")
    _assert_state(rec, ComponentLifecycleState.ENABLED)
    assert mgr.is_available("native:fs:read_file") is True


def test_consecutive_errors_trigger_quarantine() -> None:
    """Test that breaching the consecutive error threshold quarantines the component."""
    mgr = LifecycleManager(max_consecutive_errors=2)
    rec = mgr.register_component("flaky:tool", ComponentType.TOOL)
    mgr.validate_component("flaky:tool")
    mgr.enable_component("flaky:tool")

    mgr.record_invocation_start("flaky:tool")
    mgr.record_invocation_failure("flaky:tool", "Connection timeout 1")
    _assert_state(rec, ComponentLifecycleState.ENABLED)

    mgr.record_invocation_start("flaky:tool")
    mgr.record_invocation_failure("flaky:tool", "Connection timeout 2")
    _assert_state(rec, ComponentLifecycleState.QUARANTINED)
    assert mgr.is_available("flaky:tool") is False
    assert rec.quarantine_reason is not None
    assert "failure limit" in rec.quarantine_reason


def test_repair_quarantined_component() -> None:
    """Test repairing a quarantined component restores it to ENABLED."""
    mgr = LifecycleManager(max_consecutive_errors=1)
    rec = mgr.register_component("repair:tool", ComponentType.TOOL)
    mgr.validate_component("repair:tool")
    mgr.enable_component("repair:tool")

    mgr.record_invocation_failure("repair:tool", "Fault injected")
    _assert_state(rec, ComponentLifecycleState.QUARANTINED)

    # Repair and re-enable
    mgr.repair_component("repair:tool", re_enable=True)
    _assert_state(rec, ComponentLifecycleState.ENABLED)
    assert mgr.is_available("repair:tool") is True
    assert rec.consecutive_errors == 0


def test_invalid_lifecycle_transition_rejected() -> None:
    """Ensure illegal transitions raise InvalidLifecycleTransitionError."""
    mgr = LifecycleManager()
    rec = mgr.register_component("illegal:tool", ComponentType.TOOL)

    # Cannot jump directly from REGISTERED to RUNNING
    with pytest.raises(InvalidLifecycleTransitionError):
        rec.transition_to(ComponentLifecycleState.RUNNING)


@pytest.mark.asyncio
async def test_health_probe_success_and_failure() -> None:
    """Test registering and executing diagnostic health probes (Contract 16)."""
    from jarvis.core.lifecycle.types import HealthProbeResult

    mgr = LifecycleManager()
    mgr.register_component("probe:tool", ComponentType.TOOL)
    mgr.validate_component("probe:tool")
    mgr.enable_component("probe:tool")

    # 1. Successful probe
    async def healthy_probe() -> HealthProbeResult:
        return HealthProbeResult(component_id="probe:tool", healthy=True)

    mgr.register_health_probe("probe:tool", healthy_probe)
    res = await mgr.run_health_probe("probe:tool")
    assert res.healthy is True
    assert mgr.is_available("probe:tool") is True

    # 2. Failing probe triggers quarantine
    async def unhealthy_probe() -> HealthProbeResult:
        return HealthProbeResult(
            component_id="probe:tool",
            healthy=False,
            error="Backend connection degraded",
        )

    mgr.register_health_probe("probe:tool", unhealthy_probe)
    res_fail = await mgr.run_health_probe("probe:tool")
    assert res_fail.healthy is False
    assert mgr.is_available("probe:tool") is False
    rec = mgr.get_component("probe:tool")
    assert rec is not None
    assert rec.state == ComponentLifecycleState.QUARANTINED
    assert "Backend connection degraded" in str(rec.quarantine_reason)


@pytest.mark.asyncio
async def test_attempt_repair_automated_self_healing() -> None:
    """Test automated self-healing repair workflow with health re-validation."""
    from jarvis.core.lifecycle.types import HealthProbeResult

    mgr = LifecycleManager()
    mgr.register_component("auto:heal", ComponentType.SPECIALIST)
    mgr.validate_component("auto:heal")
    mgr.enable_component("auto:heal")

    # Quarantine explicitly
    mgr.quarantine_component("auto:heal", "Anomalous failure burst")
    assert mgr.is_available("auto:heal") is False

    repair_executed = False

    async def custom_repair() -> bool:
        nonlocal repair_executed
        repair_executed = True
        return True

    async def passing_probe() -> HealthProbeResult:
        return HealthProbeResult(component_id="auto:heal", healthy=True)

    mgr.register_repair_handler("auto:heal", custom_repair)
    mgr.register_health_probe("auto:heal", passing_probe)

    success = await mgr.attempt_repair("auto:heal")
    assert success is True
    assert repair_executed is True
    assert mgr.is_available("auto:heal") is True
    rec = mgr.get_component("auto:heal")
    assert rec is not None
    assert rec.state == ComponentLifecycleState.ENABLED
    assert rec.quarantine_reason is None


@pytest.mark.asyncio
async def test_attempt_repair_failure_returns_to_quarantine() -> None:
    """Test that a failing repair handler leaves component quarantined."""
    mgr = LifecycleManager()
    mgr.register_component("failing:repair", ComponentType.SPECIALIST)
    mgr.validate_component("failing:repair")
    mgr.enable_component("failing:repair")
    mgr.quarantine_component("failing:repair", "Severe fault")

    async def broken_repair() -> bool:
        return False

    mgr.register_repair_handler("failing:repair", broken_repair)
    success = await mgr.attempt_repair("failing:repair")
    assert success is False
    assert mgr.is_available("failing:repair") is False
    rec = mgr.get_component("failing:repair")
    assert rec is not None
    assert rec.state == ComponentLifecycleState.QUARANTINED


@pytest.mark.asyncio
async def test_run_all_health_probes_and_status() -> None:
    """Test running health probes across all components and getting status summary."""
    mgr = LifecycleManager()
    mgr.register_component("tool:a", ComponentType.TOOL)
    mgr.validate_component("tool:a")
    mgr.enable_component("tool:a")

    mgr.register_component("spec:b", ComponentType.SPECIALIST)
    mgr.validate_component("spec:b")
    mgr.enable_component("spec:b")

    results = await mgr.run_all_health_probes()
    assert "tool:a" in results
    assert "spec:b" in results
    assert results["tool:a"].healthy is True
    assert results["spec:b"].healthy is True

    status = mgr.get_health_status()
    assert status["total_components"] == 2
    assert status["ready"] is True
    assert len(status["quarantined"]) == 0

    # Filter components
    tools = mgr.list_components(component_type=ComponentType.TOOL)
    assert len(tools) == 1
    assert tools[0].component_id == "tool:a"
