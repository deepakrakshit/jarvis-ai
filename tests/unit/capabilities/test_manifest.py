"""Tests for CapabilityManifest schema, digest sealing, and verification."""

from jarvis.core.capabilities.manifest import (
    CapabilityManifest,
    CapabilityStatus,
    RiskClass,
    SideEffectClass,
    ToolType,
)
from jarvis.core.ifc.sinks import SinkType
from jarvis.core.trust.taxonomy import TrustLevel


def test_manifest_creation_and_defaults() -> None:
    """Verify CapabilityManifest field initialization and defaults."""
    manifest = CapabilityManifest(
        capability_id="native:fs:read_file",
        owner="core",
        description="Reads contents of a file within the workspace",
        required_scopes=["filesystem:read"],
        risk_class=RiskClass.READ_ONLY,
        side_effect_class=SideEffectClass.NONE,
        allowed_trust_sources=[TrustLevel.USER_INPUT, TrustLevel.SYSTEM_POLICY],
        allowed_sinks=[SinkType.LLM_PROMPT, SinkType.USER_DISPLAY],
    )

    assert manifest.capability_id == "native:fs:read_file"
    assert manifest.tool_type == ToolType.NATIVE
    assert manifest.status == CapabilityStatus.ACTIVE
    assert manifest.digest is None
    assert not manifest.verify_digest()


def test_manifest_seal_and_tamper_detection() -> None:
    """Verify cryptographic SHA-256 digest sealing and tamper detection."""
    manifest = CapabilityManifest(
        capability_id="mcp:brave_search:search",
        owner="research",
        tool_type=ToolType.MCP,
        description="Searches the web via Brave API",
        required_scopes=["network:outbound"],
        risk_class=RiskClass.READ_ONLY,
    )

    # Seal manifest with digest
    manifest.seal()
    assert manifest.digest is not None
    assert len(manifest.digest) == 64
    assert manifest.verify_digest()

    # Tampering with a field breaks digest verification
    tampered = manifest.model_copy(update={"risk_class": RiskClass.DANGEROUS})
    assert not tampered.verify_digest()
