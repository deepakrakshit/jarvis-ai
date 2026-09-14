"""Tests for CapabilityFirewall least-privilege projection and Stage 3 Exit Gate."""

import pytest

from jarvis.core.capabilities.firewall import CapabilityFirewall
from jarvis.core.capabilities.manifest import (
    CapabilityManifest,
    RiskClass,
)
from jarvis.core.capabilities.registry import CapabilityRegistry
from jarvis.core.exceptions import CapabilityFirewallError
from jarvis.core.trust.taxonomy import TrustLevel


@pytest.fixture
def populated_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    # 1. Read-only safe tool
    reg.register(
        CapabilityManifest(
            capability_id="native:fs:read",
            description="Reads file contents",
            required_scopes=["filesystem:read"],
            risk_class=RiskClass.READ_ONLY,
            allowed_trust_sources=[
                TrustLevel.USER_INPUT,
                TrustLevel.EXTERNAL_UNTRUSTED,
                TrustLevel.SYSTEM_POLICY,
            ],
        )
    )
    # 2. Workspace mutation tool (requires filesystem:write)
    reg.register(
        CapabilityManifest(
            capability_id="native:fs:write",
            description="Writes file in workspace",
            required_scopes=["filesystem:write"],
            risk_class=RiskClass.BOUNDED_MUTATION,
            allowed_trust_sources=[TrustLevel.USER_INPUT, TrustLevel.SYSTEM_POLICY],
        )
    )
    # 3. Network egress tool (requires network:outbound)
    reg.register(
        CapabilityManifest(
            capability_id="native:net:http_get",
            description="Performs HTTP GET",
            required_scopes=["network:outbound"],
            risk_class=RiskClass.READ_ONLY,
            allowed_trust_sources=[TrustLevel.USER_INPUT, TrustLevel.SYSTEM_POLICY],
        )
    )
    # 4. Dangerous root mutation tool (requires dangerous override)
    reg.register(
        CapabilityManifest(
            capability_id="native:os:format_disk",
            description="Formats disk partition",
            required_scopes=["system:dangerous:override"],
            risk_class=RiskClass.DANGEROUS,
            allowed_trust_sources=[TrustLevel.SYSTEM_POLICY],
        )
    )
    return reg


def test_stage_3_exit_gate_differing_scopes_differing_projections(
    populated_registry: CapabilityRegistry,
) -> None:
    """STAGE 3 EXIT GATE:

    'Given two tasks with different scopes, their model-visible tool lists differ
    exactly as policy predicts.'
    """
    # Task A: Granted only read scope
    scopes_task_a = {"filesystem:read"}
    visible_task_a = CapabilityFirewall.project_visible_tools(
        registry=populated_registry,
        task_scopes=scopes_task_a,
        autonomy_level=2,
        source_trust=TrustLevel.USER_INPUT,
    )
    visible_ids_a = {c.capability_id for c in visible_task_a}

    assert visible_ids_a == {"native:fs:read"}
    assert "native:fs:write" not in visible_ids_a
    assert "native:net:http_get" not in visible_ids_a

    # Task B: Granted read + write + network scopes
    scopes_task_b = {"filesystem:read", "filesystem:write", "network:outbound"}
    visible_task_b = CapabilityFirewall.project_visible_tools(
        registry=populated_registry,
        task_scopes=scopes_task_b,
        autonomy_level=2,
        source_trust=TrustLevel.USER_INPUT,
    )
    visible_ids_b = {c.capability_id for c in visible_task_b}

    assert visible_ids_b == {"native:fs:read", "native:fs:write", "native:net:http_get"}
    assert "native:os:format_disk" not in visible_ids_b

    # Verifiable assertion: The difference is exactly the predicted scopes
    difference = visible_ids_b - visible_ids_a
    assert difference == {"native:fs:write", "native:net:http_get"}


def test_autonomy_level_zero_restricts_to_read_only(
    populated_registry: CapabilityRegistry,
) -> None:
    """Under Autonomy Level 0 (Observe Only), all mutations are hidden even with scopes."""
    all_scopes = {"filesystem:read", "filesystem:write", "network:outbound"}
    visible = CapabilityFirewall.project_visible_tools(
        registry=populated_registry,
        task_scopes=all_scopes,
        autonomy_level=0,  # Observe Only
    )
    visible_ids = {c.capability_id for c in visible}

    # Only READ_ONLY tools visible
    assert visible_ids == {"native:fs:read", "native:net:http_get"}
    assert "native:fs:write" not in visible_ids


def test_untrusted_source_trust_hides_sensitive_tools(
    populated_registry: CapabilityRegistry,
) -> None:
    """Untrusted input cannot see tools that require USER_INPUT or higher."""
    all_scopes = {"filesystem:read", "filesystem:write", "network:outbound"}
    visible = CapabilityFirewall.project_visible_tools(
        registry=populated_registry,
        task_scopes=all_scopes,
        autonomy_level=2,
        source_trust=TrustLevel.EXTERNAL_UNTRUSTED,
    )
    visible_ids = {c.capability_id for c in visible}

    # Only tools declaring EXTERNAL_UNTRUSTED as allowed are visible
    assert visible_ids == {"native:fs:read"}
    assert "native:fs:write" not in visible_ids


def test_validate_invocation_rejects_unscoped_call(
    populated_registry: CapabilityRegistry,
) -> None:
    """Invoking a capability without required scopes raises CapabilityFirewallError."""
    write_manifest = populated_registry.require("native:fs:write")

    with pytest.raises(CapabilityFirewallError) as exc:
        CapabilityFirewall.validate_invocation(
            manifest=write_manifest,
            task_scopes={"filesystem:read"},  # Missing filesystem:write
            autonomy_level=2,
            source_trust=TrustLevel.USER_INPUT,
        )
    assert "requires missing scopes" in str(exc.value)
