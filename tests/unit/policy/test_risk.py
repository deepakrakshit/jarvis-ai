"""Unit tests for the Dynamic Invocation Risk Calculator."""

from pathlib import Path

from jarvis.core.capabilities.builtin import BUILTIN_CAPABILITIES
from jarvis.core.capabilities.manifest import CapabilityManifest, RiskClass
from jarvis.core.ifc.labels import ConfidentialityLabel
from jarvis.core.policy.risk import RiskCalculator


def test_static_risk_base_scores() -> None:
    """Verify base risk scores for all 4 risk classes without extra modifiers."""
    ro = CapabilityManifest(
        capability_id="test:ro", description="read only", risk_class=RiskClass.READ_ONLY
    )
    bm = CapabilityManifest(
        capability_id="test:bm",
        description="bounded mutation",
        risk_class=RiskClass.BOUNDED_MUTATION,
    )
    um = CapabilityManifest(
        capability_id="test:um",
        description="unbounded mutation",
        risk_class=RiskClass.UNBOUNDED_MUTATION,
    )
    dang = CapabilityManifest(
        capability_id="test:dang", description="dangerous", risk_class=RiskClass.DANGEROUS
    )

    score_ro = RiskCalculator.calculate_risk(ro, {})
    score_bm = RiskCalculator.calculate_risk(bm, {})
    score_um = RiskCalculator.calculate_risk(um, {})
    score_dang = RiskCalculator.calculate_risk(dang, {})

    assert score_ro < score_bm < score_um < score_dang
    assert score_ro == 0.05
    assert score_dang == 0.95


def test_destructive_shell_command_escalates_risk() -> None:
    """Verify that destructive commands in arguments dynamically elevate risk score."""
    shell_tool = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:shell:execute")

    safe_args = {"command": "ls -la"}
    safe_risk = RiskCalculator.calculate_risk(shell_tool, safe_args)

    dangerous_args = {"command": "rm -rf /tmp/data"}
    dangerous_risk = RiskCalculator.calculate_risk(shell_tool, dangerous_args)

    assert dangerous_risk > safe_risk
    assert dangerous_risk >= 0.95


def test_sensitive_target_file_escalates_risk(tmp_path: Path) -> None:
    """Verify targeting sensitive files like .env or .ssh dramatically escalates risk."""
    fs_read = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:fs:read_file")

    normal_args = {"file_path": str(tmp_path / "app.py")}
    normal_risk = RiskCalculator.calculate_risk(fs_read, normal_args, workspace_root=tmp_path)

    env_args = {"file_path": str(tmp_path / ".env")}
    env_risk = RiskCalculator.calculate_risk(fs_read, env_args, workspace_root=tmp_path)

    ssh_args = {"file_path": "/home/user/.ssh/id_rsa"}
    ssh_risk = RiskCalculator.calculate_risk(fs_read, ssh_args, workspace_root=tmp_path)

    assert env_risk > normal_risk
    assert ssh_risk > normal_risk


def test_path_traversal_escalates_risk(tmp_path: Path) -> None:
    """Verify path traversal patterns ('..') dynamically escalate risk."""
    fs_read = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:fs:read_file")

    traversal_args = {"file_path": "../../secret.txt"}
    risk = RiskCalculator.calculate_risk(fs_read, traversal_args, workspace_root=tmp_path)

    assert risk >= 0.40


def test_workspace_boundary_confinement(tmp_path: Path) -> None:
    """Verify target resources outside the approved workspace elevate risk."""
    fs_write = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:fs:write_file")

    in_ws_args = {"file_path": str(tmp_path / "output.txt"), "content": "hello"}
    in_ws_risk = RiskCalculator.calculate_risk(fs_write, in_ws_args, workspace_root=tmp_path)

    out_ws_args = {"file_path": "C:/Windows/System32/drivers/etc/hosts", "content": "bad"}
    out_ws_risk = RiskCalculator.calculate_risk(fs_write, out_ws_args, workspace_root=tmp_path)

    assert out_ws_risk > in_ws_risk


def test_confidentiality_and_environment_modifiers() -> None:
    """Verify data confidentiality levels and environment tiers calibrate risk."""
    ro = CapabilityManifest(
        capability_id="test:ro", description="read only", risk_class=RiskClass.READ_ONLY
    )

    public_dev = RiskCalculator.calculate_risk(
        ro, {}, confidentiality=ConfidentialityLabel.PUBLIC, environment="development"
    )
    secret_prod = RiskCalculator.calculate_risk(
        ro, {}, confidentiality=ConfidentialityLabel.SECRET, environment="production"
    )

    assert secret_prod > public_dev
