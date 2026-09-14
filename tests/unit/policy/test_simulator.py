"""Unit tests for PolicySimulator dry-run preview."""

from pathlib import Path
from uuid import uuid4

from jarvis.core.capabilities.builtin import BUILTIN_CAPABILITIES
from jarvis.core.policy.decision import AutonomyLevel, PolicyDecisionType
from jarvis.core.policy.dsl import PolicyDSLLoader
from jarvis.core.policy.engine import PolicyEngine
from jarvis.core.policy.simulator import PolicySimulator


def test_simulator_dry_run_does_not_mutate_state(tmp_path: Path) -> None:
    """Verify PolicySimulator previews decisions accurately without side effects."""
    ruleset = PolicyDSLLoader.load_from_yaml(Path("jarvis/policies/default_policies.yaml"))
    engine = PolicyEngine(ruleset=ruleset, workspace_root=tmp_path)
    simulator = PolicySimulator(engine)

    fs_write = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:fs:write_file")

    decision = simulator.simulate(
        capability=fs_write,
        arguments={"file_path": str(tmp_path / "simulated.txt"), "content": "simulated"},
        task_id=uuid4(),
        autonomy_level=AutonomyLevel.AUTO_BOUNDED_MUTATION,
    )

    assert decision.decision == PolicyDecisionType.ALLOW
    assert decision.risk_score > 0.0
    assert not (tmp_path / "simulated.txt").exists()


def test_simulator_detects_dangerous_action_without_execution(tmp_path: Path) -> None:
    """Verify PolicySimulator flags dangerous shell execution as requiring HITL."""
    ruleset = PolicyDSLLoader.load_from_yaml(Path("jarvis/policies/default_policies.yaml"))
    engine = PolicyEngine(ruleset=ruleset, workspace_root=tmp_path)
    simulator = PolicySimulator(engine)

    shell_tool = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:shell:execute")

    decision = simulator.simulate(
        capability=shell_tool,
        arguments={"command": "rm -rf /"},
        task_id=uuid4(),
        autonomy_level=AutonomyLevel.AUTONOMOUS_BUDGETED,
    )

    assert decision.decision == PolicyDecisionType.REQUIRE_HITL
    assert "hitl" in decision.obligations
    assert "sandbox" in decision.obligations
