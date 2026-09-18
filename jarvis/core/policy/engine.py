"""JARVIS Centralized Policy Engine.

Evaluates every tool invocation across all protocols (Native, MCP, A2A, Deep Agents)
combining static capability manifests, dynamic risk scoring, autonomy levels (0-5),
information flow control (IFC) sink policies, and declarative rules.
"""

from pathlib import Path
from typing import Any
from uuid import UUID

from jarvis.core.capabilities.manifest import CapabilityManifest, RiskClass
from jarvis.core.exceptions import SecurityViolationError
from jarvis.core.ifc.labels import ConfidentialityLabel, IntegrityLabel
from jarvis.core.ifc.sinks import SinkEnforcer
from jarvis.core.ifc.taint import LabeledData
from jarvis.core.logging import get_logger
from jarvis.core.policy.decision import (
    AutonomyLevel,
    PolicyDecision,
    PolicyDecisionType,
)
from jarvis.core.policy.dsl import PolicyEvaluationContext, PolicyRuleSet
from jarvis.core.policy.hitl import HITLPipeline
from jarvis.core.policy.risk import RiskCalculator

logger = get_logger(__name__)


class PolicyEngine:
    """Centralized authorization gatekeeper for the JARVIS Personal AI Operating System."""

    ruleset: PolicyRuleSet
    hitl_pipeline: HITLPipeline
    sink_enforcer: SinkEnforcer
    workspace_root: Path | None
    default_autonomy: AutonomyLevel
    environment: str

    def __init__(
        self,
        ruleset: PolicyRuleSet | None = None,
        hitl_pipeline: HITLPipeline | None = None,
        sink_enforcer: SinkEnforcer | None = None,
        workspace_root: Path | str | None = None,
        default_autonomy: AutonomyLevel = AutonomyLevel.AUTO_BOUNDED_MUTATION,
        environment: str = "development",
    ) -> None:
        self.ruleset = ruleset or PolicyRuleSet()
        self.hitl_pipeline = hitl_pipeline or HITLPipeline()
        self.sink_enforcer = sink_enforcer or SinkEnforcer()
        self.workspace_root = Path(workspace_root) if workspace_root else None
        self.default_autonomy = default_autonomy
        self.environment = environment

    def evaluate_invocation(
        self,
        task_id: str | UUID,
        manifest: CapabilityManifest,
        arguments: dict[str, Any],
        autonomy_level: AutonomyLevel | None = None,
        target_resource: str | None = None,
        input_data: LabeledData[Any] | None = None,
        agent_id: str = "core_agent",
        user_id: str = "default_user",
    ) -> PolicyDecision:
        """Evaluate an intended tool invocation against all policy layers.

        Returns a PolicyDecision (ALLOW, DENY, REQUIRE_HITL, REQUIRE_SANDBOX, etc.).
        """
        active_autonomy = autonomy_level if autonomy_level is not None else self.default_autonomy

        # 1. Level 0: Observe Only -> Absolute Deny
        if active_autonomy == AutonomyLevel.OBSERVE_ONLY:
            return PolicyDecision(
                decision=PolicyDecisionType.DENY,
                reason="Autonomy Level 0 (Observe Only) strictly forbids all tool execution.",
                risk_score=0.0,
            )

        # Level 5: Mandatory approval for all mutations
        if (
            active_autonomy == AutonomyLevel.MANDATORY_APPROVAL_MUTATIONS
            and manifest.risk_class != RiskClass.READ_ONLY
        ):
            return PolicyDecision(
                decision=PolicyDecisionType.REQUIRE_HITL,
                reason="Autonomy Level 5 mandates human approval for all mutations.",
                risk_score=0.5,
                obligations=["hitl"],
            )

        # 2. Extract confidentiality and integrity from input data
        confidentiality = input_data.confidentiality if input_data else ConfidentialityLabel.PUBLIC
        integrity = input_data.integrity if input_data else IntegrityLabel.UNTRUSTED

        # 3. Determine if target resource is inside approved workspace
        resolved_target = target_resource or RiskCalculator._extract_target_resource(arguments)
        is_in_workspace = True
        if self.workspace_root and resolved_target:
            is_in_workspace = RiskCalculator._is_within_workspace(
                resolved_target, self.workspace_root
            )

        # 4. Compute dynamic invocation risk
        risk_score = RiskCalculator.calculate_risk(
            manifest=manifest,
            arguments=arguments,
            target_resource=resolved_target,
            workspace_root=self.workspace_root,
            confidentiality=confidentiality,
            environment=self.environment,
        )

        # 5. Build full evaluation context
        ctx = PolicyEvaluationContext(
            task_id=str(task_id),
            tool_id=manifest.capability_id,
            manifest=manifest,
            arguments=arguments,
            target_resource=resolved_target,
            is_in_workspace=is_in_workspace,
            confidentiality=confidentiality,
            integrity=integrity,
            autonomy_level=active_autonomy,
            risk_score=risk_score,
            environment=self.environment,
            user_id=user_id,
            agent_id=agent_id,
        )

        # 6. Evaluate Declarative Ruleset (Highest Priority Custom Rules)
        for rule in self.ruleset.rules:
            if rule.matches(ctx):
                logger.info(
                    "policy_rule_matched",
                    rule_id=rule.rule_id,
                    tool_id=manifest.capability_id,
                    decision=rule.decision.value,
                )
                return PolicyDecision(
                    decision=rule.decision,
                    reason=f"Matched declarative policy rule '{rule.rule_id}': {rule.description}",
                    risk_score=risk_score,
                    matched_rule_id=rule.rule_id,
                    obligations=rule.obligations,
                    context_snapshot={"autonomy": active_autonomy.value, "risk": risk_score},
                )

        # 7. Check Sensitive Sinks (IFC Invariant)
        if input_data and manifest.allowed_sinks:
            for sink in manifest.allowed_sinks:
                try:
                    self.sink_enforcer.enforce(
                        sink_type=sink,
                        data=input_data,
                        destination=resolved_target or manifest.capability_id,
                    )
                except SecurityViolationError as sink_err:
                    logger.warning(
                        "policy_sink_violation",
                        tool_id=manifest.capability_id,
                        sink=sink.value,
                        error=str(sink_err),
                    )
                    return PolicyDecision(
                        decision=PolicyDecisionType.DENY,
                        reason=f"Sensitive sink violation: {sink_err}",
                        risk_score=risk_score,
                    )

        # 8. Evaluate Capability Manifest Hard Invariants
        if manifest.approval_requirement:
            return PolicyDecision(
                decision=PolicyDecisionType.REQUIRE_HITL,
                reason=f"Capability '{manifest.capability_id}' requires mandatory approval.",
                risk_score=risk_score,
                obligations=["hitl"],
            )

        # 9. Evaluate Autonomy Level Hierarchy (Layer 12 Baseline Invariants)
        if active_autonomy == AutonomyLevel.RECOMMEND_ONLY:
            # Level 1: Human must manually execute all actions
            return PolicyDecision(
                decision=PolicyDecisionType.REQUIRE_HITL,
                reason="Autonomy Level 1 (Recommend Only) requires human execution for all actions.",
                risk_score=risk_score,
                obligations=["hitl"],
            )

        if (
            active_autonomy == AutonomyLevel.MANDATORY_APPROVAL_MUTATIONS
            and manifest.risk_class != RiskClass.READ_ONLY
        ):
            # Level 5: Mandatory approval for all mutations
            return PolicyDecision(
                decision=PolicyDecisionType.REQUIRE_HITL,
                reason="Autonomy Level 5 mandates human approval for all mutations.",
                risk_score=risk_score,
                obligations=["hitl"],
            )

        if active_autonomy == AutonomyLevel.AUTO_READ_ONLY:
            # Level 2: Auto-execute safe, read-only operations
            if manifest.risk_class != RiskClass.READ_ONLY:
                return PolicyDecision(
                    decision=PolicyDecisionType.REQUIRE_HITL,
                    reason=f"Autonomy Level 2 does not permit mutation '{manifest.capability_id}'.",
                    risk_score=risk_score,
                    obligations=["hitl"],
                )
            if risk_score > 0.40:
                return PolicyDecision(
                    decision=PolicyDecisionType.REQUIRE_HITL,
                    reason="Read-only operation exceeded safe risk threshold (0.40).",
                    risk_score=risk_score,
                    obligations=["hitl"],
                )

        if active_autonomy == AutonomyLevel.AUTO_BOUNDED_MUTATION:
            # Level 3: Auto-execute bounded mutations within approved workspaces
            if manifest.risk_class == RiskClass.DANGEROUS:
                return PolicyDecision(
                    decision=PolicyDecisionType.REQUIRE_HITL,
                    reason="Dangerous action requires explicit human approval under Autonomy Level 3.",
                    risk_score=risk_score,
                    obligations=["hitl"],
                )
            if manifest.risk_class == RiskClass.UNBOUNDED_MUTATION:
                return PolicyDecision(
                    decision=PolicyDecisionType.REQUIRE_HITL,
                    reason="Unbounded mutation outside task workspace requires human approval.",
                    risk_score=risk_score,
                    obligations=["hitl"],
                )
            is_fs_mutation = (
                any(s.startswith("filesystem:") for s in manifest.required_scopes)
                or ":fs:" in manifest.capability_id
            )
            if (
                is_fs_mutation
                and not is_in_workspace
                and manifest.risk_class != RiskClass.READ_ONLY
            ):
                return PolicyDecision(
                    decision=PolicyDecisionType.REQUIRE_HITL,
                    reason="Target resource is outside approved workspace boundary.",
                    risk_score=risk_score,
                    obligations=["hitl"],
                )

        if (
            active_autonomy == AutonomyLevel.AUTONOMOUS_BUDGETED
            and manifest.risk_class == RiskClass.DANGEROUS
            and risk_score > 0.85
        ):
            # Level 4: Autonomous execution within budget
            return PolicyDecision(
                decision=PolicyDecisionType.REQUIRE_HITL,
                reason="High-risk dangerous action exceeds Level 4 autonomous risk threshold (0.85).",
                risk_score=risk_score,
                obligations=["hitl"],
            )

        # 10. Check Sandbox Obligation
        obligations: list[str] = []
        decision_type = PolicyDecisionType.ALLOW
        if manifest.sandbox_requirement:
            decision_type = PolicyDecisionType.REQUIRE_SANDBOX
            obligations.append("sandbox")

        if manifest.verification_requirement:
            obligations.append("external_verification")

        return PolicyDecision(
            decision=decision_type,
            reason=f"Action '{manifest.capability_id}' authorized under Autonomy Level {active_autonomy.value}.",
            risk_score=risk_score,
            obligations=obligations,
            context_snapshot={"autonomy": active_autonomy.value, "risk": risk_score},
        )
