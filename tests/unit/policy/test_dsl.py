"""Unit tests for Policy DSL, condition predicates, and ruleset matching."""

from pathlib import Path

from jarvis.core.capabilities.builtin import BUILTIN_CAPABILITIES
from jarvis.core.ifc.labels import ConfidentialityLabel, IntegrityLabel
from jarvis.core.policy.decision import PolicyDecisionType
from jarvis.core.policy.dsl import (
    PolicyCondition,
    PolicyDSLLoader,
    PolicyEvaluationContext,
    PolicyRule,
    PolicyRuleSet,
)


def test_condition_numeric_comparison() -> None:
    """Verify numeric comparisons like >=, <=, !=."""
    assert PolicyCondition._compare_numeric(3, ">=3") is True
    assert PolicyCondition._compare_numeric(2, ">=3") is False
    assert PolicyCondition._compare_numeric(5, "<=10") is True
    assert PolicyCondition._compare_numeric(5, "==5") is True
    assert PolicyCondition._compare_numeric(5, "!=5") is False


def test_condition_confidentiality_hierarchy() -> None:
    """Verify confidentiality label hierarchy comparisons."""
    assert (
        PolicyCondition._compare_confidentiality(ConfidentialityLabel.PUBLIC, "<=INTERNAL") is True
    )
    assert (
        PolicyCondition._compare_confidentiality(ConfidentialityLabel.INTERNAL, "<=INTERNAL")
        is True
    )
    assert (
        PolicyCondition._compare_confidentiality(ConfidentialityLabel.CONFIDENTIAL, "<=INTERNAL")
        is False
    )
    assert PolicyCondition._compare_confidentiality(ConfidentialityLabel.SECRET, "==SECRET") is True


def test_condition_integrity_hierarchy() -> None:
    """Verify integrity label hierarchy comparisons."""
    assert (
        PolicyCondition._compare_integrity(IntegrityLabel.SYSTEM_TRUSTED, ">=USER_CONTROLLED")
        is True
    )
    assert (
        PolicyCondition._compare_integrity(IntegrityLabel.UNTRUSTED, ">=USER_CONTROLLED") is False
    )


def test_rule_priority_ordering() -> None:
    """Verify rules are evaluated strictly in descending priority order."""
    ruleset = PolicyRuleSet()
    r1 = PolicyRule(
        rule_id="low-priority", priority=10, tool_pattern="*", decision=PolicyDecisionType.ALLOW
    )
    r2 = PolicyRule(
        rule_id="high-priority-deny",
        priority=100,
        tool_pattern="*",
        decision=PolicyDecisionType.DENY,
    )
    ruleset.add_rule(r1)
    ruleset.add_rule(r2)

    assert ruleset.rules[0].rule_id == "high-priority-deny"
    assert ruleset.rules[1].rule_id == "low-priority"


def test_load_default_policies_yaml() -> None:
    """Verify loading default policies YAML file from disk."""
    default_yaml_path = Path("jarvis/policies/default_policies.yaml")
    assert default_yaml_path.exists()

    ruleset = PolicyDSLLoader.load_from_yaml(default_yaml_path)
    assert len(ruleset.rules) >= 5
    assert ruleset.default_decision == PolicyDecisionType.DENY

    # Verify highest priority is path traversal deny
    top_rule = ruleset.rules[0]
    assert top_rule.rule_id == "deny-path-traversal"
    assert top_rule.decision == PolicyDecisionType.DENY


def test_rule_arguments_regex_matching() -> None:
    """Verify regex matching on argument payloads."""
    fs_read = next(c for c in BUILTIN_CAPABILITIES if c.capability_id == "native:fs:read_file")
    rule = PolicyRule(
        rule_id="block-env",
        tool_pattern="native:fs:*",
        priority=100,
        when=PolicyCondition(arguments_regex={"file_path": r"\.env$"}),
        decision=PolicyDecisionType.DENY,
    )

    ctx_env = PolicyEvaluationContext(
        task_id="t1",
        tool_id=fs_read.capability_id,
        manifest=fs_read,
        arguments={"file_path": "/app/.env"},
    )
    assert rule.matches(ctx_env) is True

    ctx_py = PolicyEvaluationContext(
        task_id="t1",
        tool_id=fs_read.capability_id,
        manifest=fs_read,
        arguments={"file_path": "/app/main.py"},
    )
    assert rule.matches(ctx_py) is False
