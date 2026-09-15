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
