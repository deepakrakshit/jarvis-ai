"""Tests for CapabilityRegistry storage, digest validation, and deny-by-default."""

import pytest

from jarvis.core.capabilities.manifest import (
    CapabilityManifest,
    CapabilityStatus,
    RiskClass,
)
from jarvis.core.capabilities.registry import CapabilityRegistry
from jarvis.core.exceptions import CapabilityFirewallError


@pytest.fixture
def registry() -> CapabilityRegistry:
    return CapabilityRegistry()


def test_registry_register_and_lookup(registry: CapabilityRegistry) -> None:
    """Verify registration, auto-sealing, and lookup."""
    manifest = CapabilityManifest(
        capability_id="native:time:get_clock",
        description="Returns current UTC timestamp",
        risk_class=RiskClass.READ_ONLY,
    )

    registry.register(manifest)
    retrieved = registry.get("native:time:get_clock")
    assert retrieved is not None
    assert retrieved.capability_id == "native:time:get_clock"
    assert retrieved.digest is not None
    assert retrieved.verify_digest()


def test_registry_deny_by_default(registry: CapabilityRegistry) -> None:
    """Verify that require() enforces deny-by-default for unregistered capabilities."""
    with pytest.raises(CapabilityFirewallError) as exc:
        registry.require("unregistered:dangerous:tool")
    assert "deny-by-default" in str(exc.value)


def test_registry_digest_tamper_rejection(registry: CapabilityRegistry) -> None:
    """Verify that registering a tampered manifest with an invalid digest raises error."""
    manifest = CapabilityManifest(
        capability_id="native:fs:write_file",
        description="Writes a file",
        risk_class=RiskClass.BOUNDED_MUTATION,
    )
    manifest.seal()

    # Tamper with description without updating digest
    tampered = manifest.model_copy(update={"description": "Tampered description"})
    with pytest.raises(CapabilityFirewallError) as exc:
        registry.register(tampered, verify_digest=True)
    assert "integrity violation" in str(exc.value)


def test_registry_status_filtering(registry: CapabilityRegistry) -> None:
    """Verify that list_active filters out DISABLED and QUARANTINED capabilities."""
    cap1 = CapabilityManifest(capability_id="tool:active", description="Active tool")
    cap2 = CapabilityManifest(
        capability_id="tool:disabled",
        description="Disabled tool",
        status=CapabilityStatus.DISABLED,
    )

    registry.register(cap1)
    registry.register(cap2)

    assert len(registry.list_all()) == 2
    active = registry.list_active()
    assert len(active) == 1
    assert active[0].capability_id == "tool:active"

    # Update status
    registry.set_status("tool:active", CapabilityStatus.QUARANTINED)
    assert len(registry.list_active()) == 0
