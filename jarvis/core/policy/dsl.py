"""JARVIS Policy DSL and Rule Evaluation Engine.

Provides declarative YAML/JSON rules matching tool invocations against
context attributes, autonomy levels, risk thresholds, and boundary constraints.
"""

import fnmatch
import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from jarvis.core.capabilities.manifest import CapabilityManifest
from jarvis.core.ifc.labels import ConfidentialityLabel, IntegrityLabel
from jarvis.core.logging import get_logger
from jarvis.core.policy.decision import AutonomyLevel, PolicyDecisionType

logger = get_logger(__name__)


class PolicyEvaluationContext(BaseModel):
    """Execution context provided to the policy engine for evaluation."""

    task_id: str
    tool_id: str
    manifest: CapabilityManifest
    arguments: dict[str, Any] = Field(default_factory=dict)
    target_resource: str | None = None
    is_in_workspace: bool = True
    confidentiality: ConfidentialityLabel = ConfidentialityLabel.PUBLIC
    integrity: IntegrityLabel = IntegrityLabel.UNTRUSTED
    autonomy_level: AutonomyLevel = AutonomyLevel.AUTO_BOUNDED_MUTATION
    risk_score: float = 0.0
    environment: str = "development"
    user_id: str = "default_user"
    agent_id: str = "core_agent"


class PolicyCondition(BaseModel):
    """Matching conditions for a declarative policy rule."""

    workspace: str | None = None  # "approved", "outside", or None
    confidentiality: str | None = None  # e.g., "<=INTERNAL", "==PUBLIC"
    integrity: str | None = None  # e.g., ">=SYSTEM_TRUSTED"
    autonomy_level: str | None = None  # e.g., ">=3", "==2"
    max_risk: float | None = None
    min_risk: float | None = None
    environment: str | None = None
    arguments_regex: dict[str, str] | None = None

    def evaluate(self, ctx: PolicyEvaluationContext) -> bool:
        """Evaluate whether the given context satisfies all condition predicates."""
        # 1. Workspace check
        if self.workspace:
            if self.workspace.lower() == "approved" and not ctx.is_in_workspace:
                return False
            if self.workspace.lower() == "outside" and ctx.is_in_workspace:
                return False

        # 2. Autonomy level comparison
        if self.autonomy_level and not self._compare_numeric(
            ctx.autonomy_level.value, self.autonomy_level
        ):
            return False

        # 3. Risk bounds check
        if self.max_risk is not None and ctx.risk_score > self.max_risk:
            return False
        if self.min_risk is not None and ctx.risk_score < self.min_risk:
            return False

        # 4. Confidentiality label check
        if self.confidentiality and not self._compare_confidentiality(
            ctx.confidentiality, self.confidentiality
        ):
            return False

        # 5. Integrity label check
        if self.integrity and not self._compare_integrity(ctx.integrity, self.integrity):
            return False

        # 6. Environment check
        if self.environment and self.environment.lower() != ctx.environment.lower():
            return False

        # 7. Arguments regex check
        if self.arguments_regex:
            for key, pattern in self.arguments_regex.items():
                val = str(ctx.arguments.get(key, ""))
                if not re.search(pattern, val):
                    return False

        return True

    @staticmethod
    def _compare_numeric(val: int | float, expr: str) -> bool:
        """Parse and compare expressions like '>=3', '<2', '==4'."""
        expr = expr.strip()
        op = "=="
        num_str = expr
        for candidate_op in (">=", "<=", "!=", ">", "<", "=="):
            if expr.startswith(candidate_op):
                op = candidate_op
                num_str = expr[len(candidate_op) :].strip()
                break

        try:
            num = float(num_str)
        except ValueError:
            return False

        if op == ">=":
            return val >= num
        if op == "<=":
            return val <= num
        if op == ">":
            return val > num
        if op == "<":
            return val < num
        if op == "!=":
            return val != num
        return val == num

    @staticmethod
    def _compare_confidentiality(actual: ConfidentialityLabel, expr: str) -> bool:
        """Compare confidentiality labels hierarchically (PUBLIC < INTERNAL < CONFIDENTIAL < SECRET)."""
        rank_map = {
            ConfidentialityLabel.PUBLIC: 0,
            ConfidentialityLabel.INTERNAL: 1,
            ConfidentialityLabel.CONFIDENTIAL: 2,
            ConfidentialityLabel.SECRET: 3,
        }
        expr = expr.strip()
        op = "=="
        target_name = expr
        for candidate_op in (">=", "<=", "!=", ">", "<", "=="):
            if expr.startswith(candidate_op):
                op = candidate_op
                target_name = expr[len(candidate_op) :].strip()
                break

        try:
            target_label = ConfidentialityLabel(target_name.upper())
            actual_rank = rank_map[actual]
            target_rank = rank_map[target_label]
            return PolicyCondition._compare_numeric(actual_rank, f"{op}{target_rank}")
        except Exception:
            return False

    @staticmethod
    def _compare_integrity(actual: IntegrityLabel, expr: str) -> bool:
        """Compare integrity labels hierarchically (UNTRUSTED < USER_VALIDATED < SYSTEM_TRUSTED)."""
        rank_map = {
            IntegrityLabel.UNTRUSTED: 0,
            IntegrityLabel.USER_CONTROLLED: 1,
            IntegrityLabel.SYSTEM_TRUSTED: 2,
        }
        expr = expr.strip()
        op = "=="
        target_name = expr
        for candidate_op in (">=", "<=", "!=", ">", "<", "=="):
            if expr.startswith(candidate_op):
                op = candidate_op
                target_name = expr[len(candidate_op) :].strip()
                break

        try:
            target_label = IntegrityLabel(target_name.upper())
            actual_rank = rank_map[actual]
            target_rank = rank_map[target_label]
            return PolicyCondition._compare_numeric(actual_rank, f"{op}{target_rank}")
        except Exception:
            return False


class PolicyRule(BaseModel):
    """Declarative policy rule specification."""

    rule_id: str
    description: str = ""
    priority: int = 100
    """Higher priority numbers are evaluated first."""

    tool_pattern: str = "*"
    """Glob pattern matching tool capability ID (e.g. 'native:fs:*', 'mcp:*')."""

    when: PolicyCondition = Field(default_factory=PolicyCondition)
    decision: PolicyDecisionType
    obligations: list[str] = Field(default_factory=list)

    def matches(self, ctx: PolicyEvaluationContext) -> bool:
        """Return True if tool ID matches glob and conditions evaluate True."""
        if not fnmatch.fnmatch(ctx.tool_id, self.tool_pattern):
            return False
        return self.when.evaluate(ctx)


class PolicyRuleSet(BaseModel):
    """Ordered collection of policy rules with fail-closed default verdict."""

    version: str = "1.0.0"
    default_decision: PolicyDecisionType = PolicyDecisionType.DENY
    rules: list[PolicyRule] = Field(default_factory=list)

    def add_rule(self, rule: PolicyRule) -> None:
        """Add a rule and re-sort by priority descending."""
        self.rules.append(rule)
        self.rules.sort(key=lambda r: r.priority, reverse=True)


class PolicyDSLLoader:
    """Loads and compiles declarative YAML and JSON policy rule sets."""

    @staticmethod
    def load_from_yaml(yaml_content_or_path: str | Path) -> PolicyRuleSet:
        """Load ruleset from YAML file or raw YAML string."""
        raw_text: str
        if isinstance(yaml_content_or_path, Path) or (
            isinstance(yaml_content_or_path, str)
            and "\n" not in yaml_content_or_path
            and Path(yaml_content_or_path).exists()
        ):
            raw_text = Path(yaml_content_or_path).read_text(encoding="utf-8")
        else:
            raw_text = str(yaml_content_or_path)

        data = yaml.safe_load(raw_text)
        if not isinstance(data, dict):
            raise ValueError("Policy YAML root must be a mapping dictionary.")

        raw_rules = data.get("rules", [])
        rules: list[PolicyRule] = []
        for r_dict in raw_rules:
            rules.append(PolicyRule.model_validate(r_dict))

        # Sort priority descending
        rules.sort(key=lambda r: r.priority, reverse=True)

        return PolicyRuleSet(
            version=str(data.get("version", "1.0.0")),
            default_decision=PolicyDecisionType(
                data.get("default_decision", PolicyDecisionType.DENY)
            ),
            rules=rules,
        )
