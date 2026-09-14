"""Unit tests for ManifestLoader and builtin capabilities."""

from pathlib import Path

import pytest

from jarvis.core.capabilities.builtin import (
    BUILTIN_CAPABILITIES,
    register_builtin_capabilities,
)
from jarvis.core.capabilities.loader import ManifestLoader
from jarvis.core.capabilities.manifest import (
    CapabilityManifest,
    CapabilityStatus,
    RiskClass,
    SideEffectClass,
    ToolType,
)
from jarvis.core.capabilities.registry import CapabilityRegistry
from jarvis.core.exceptions import CapabilityFirewallError


def test_save_and_load_json_manifest(tmp_path: Path) -> None:
    """Verify serialization to JSON and loading with digest verification."""
    manifest = CapabilityManifest(
        capability_id="test:tool:json",
        owner="test_suite",
        version="1.0.0",
        provider="pytest",
        tool_type=ToolType.NATIVE,
        description="Test JSON tool.",
        required_scopes=["test:scope"],
        risk_class=RiskClass.READ_ONLY,
        side_effect_class=SideEffectClass.NONE,
    )
    file_path = tmp_path / "test_tool.json"
    ManifestLoader.save_file(manifest, file_path, format_type="json")

    loaded = ManifestLoader.load_file(file_path, verify_digest=True)
    assert loaded.capability_id == "test:tool:json"
    assert loaded.verify_digest() is True
    assert loaded.status == CapabilityStatus.ACTIVE


def test_save_and_load_yaml_manifest(tmp_path: Path) -> None:
    """Verify serialization to YAML and loading with digest verification."""
    manifest = CapabilityManifest(
        capability_id="test:tool:yaml",
        owner="test_suite",
        version="2.0.0",
        provider="pytest",
        tool_type=ToolType.SANDBOX,
        description="Test YAML tool.",
        required_scopes=["test:yaml_scope"],
        risk_class=RiskClass.BOUNDED_MUTATION,
        side_effect_class=SideEffectClass.IDEMPOTENT,
    )
    file_path = tmp_path / "test_tool.yaml"
    ManifestLoader.save_file(manifest, file_path, format_type="yaml")

    loaded = ManifestLoader.load_file(file_path, verify_digest=True)
    assert loaded.capability_id == "test:tool:yaml"
    assert loaded.tool_type == ToolType.SANDBOX
    assert loaded.verify_digest() is True


def test_load_directory_recursively(tmp_path: Path) -> None:
    """Verify recursive directory discovery and optional auto-registration."""
    sub1 = tmp_path / "group_a"
    sub2 = tmp_path / "group_b"

    m1 = CapabilityManifest(
        capability_id="native:a:one",
        description="Tool one",
        required_scopes=["scope:a"],
    )
    m2 = CapabilityManifest(
        capability_id="native:b:two",
        description="Tool two",
        required_scopes=["scope:b"],
    )

    ManifestLoader.save_file(m1, sub1 / "m1.json", format_type="json")
    ManifestLoader.save_file(m2, sub2 / "m2.yml", format_type="yaml")

    registry = CapabilityRegistry()
    loaded_list = ManifestLoader.load_directory(tmp_path, registry=registry)

    assert len(loaded_list) == 2
    assert registry.get("native:a:one") is not None
    assert registry.get("native:b:two") is not None


def test_tampered_file_rejected(tmp_path: Path) -> None:
    """Verify that tampering with manifest contents triggers integrity rejection."""
    manifest = CapabilityManifest(
        capability_id="tamper:target",
        description="Original description",
    )
    file_path = tmp_path / "tamper.json"
    ManifestLoader.save_file(manifest, file_path, format_type="json")

    # Tamper with the file on disk without recalculating digest
    content = file_path.read_text(encoding="utf-8")
    tampered = content.replace("Original description", "Malicious modified description")
    file_path.write_text(tampered, encoding="utf-8")

    with pytest.raises(CapabilityFirewallError, match="Integrity check failed"):
        ManifestLoader.load_file(file_path, verify_digest=True)


def test_invalid_schema_rejected(tmp_path: Path) -> None:
    """Verify that malformed or schema-violating manifests fail closed."""
    file_path = tmp_path / "bad.json"
    file_path.write_text('{"invalid_field": 123}', encoding="utf-8")

    with pytest.raises(CapabilityFirewallError):
        ManifestLoader.load_file(file_path)


def test_register_builtin_capabilities() -> None:
    """Verify all built-in capabilities register cleanly and pass digest verification."""
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)

    assert len(registry.list_all()) == len(BUILTIN_CAPABILITIES)
    fs_read = registry.require("native:fs:read_file")
    assert fs_read.risk_class == RiskClass.READ_ONLY
    assert fs_read.verify_digest() is True

    shell_exec = registry.require("native:shell:execute")
    assert shell_exec.risk_class == RiskClass.DANGEROUS
    assert shell_exec.sandbox_requirement is True
    assert shell_exec.verify_digest() is True
