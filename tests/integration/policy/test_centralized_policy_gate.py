"""Integration tests proving the Centralized Policy Boundary Invariant.

Invariant: 100% of tool invocations—native, MCP, Deep Agents, or future sources—
must pass through the centralized policy boundary.
"""

from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.core.capabilities.manifest import (
    CapabilityManifest,
    RiskClass,
    ToolType,
)
from jarvis.core.policy.decision import AutonomyLevel, PolicyDecisionType
from jarvis.core.policy.dsl import PolicyDSLLoader
from jarvis.core.policy.engine import PolicyEngine


@pytest.fixture
def enterprise_policy_engine(tmp_path: Path) -> PolicyEngine:
    ruleset = PolicyDSLLoader.load_from_yaml(Path("jarvis/policies/default_policies.yaml"))
    return PolicyEngine(ruleset=ruleset, workspace_root=tmp_path)


def test_all_tool_types_pass_through_policy_boundary(
    enterprise_policy_engine: PolicyEngine, tmp_path: Path
) -> None:
    """CENTRALIZED POLICY BOUNDARY INVARIANT:

    Tests that Native, MCP, A2A, and Sandbox capabilities are all strictly intercepted
    and adjudicated by the Policy Engine.
    """
    tools = [
        # Native tool
        CapabilityManifest(
            capability_id="native:fs:write_file",
            tool_type=ToolType.NATIVE,
            description="Native write",
            risk_class=RiskClass.BOUNDED_MUTATION,
        ),
        # MCP external server tool
        CapabilityManifest(
            capability_id="mcp:github:create_issue",
            tool_type=ToolType.MCP,
            description="MCP GitHub issue creation",
            risk_class=RiskClass.UNBOUNDED_MUTATION,
        ),
        # A2A agent-to-agent delegation
        CapabilityManifest(
            capability_id="a2a:specialist:delegate_task",
            tool_type=ToolType.A2A,
            description="A2A task delegation",
            risk_class=RiskClass.UNBOUNDED_MUTATION,
        ),
        # Sandbox execution
        CapabilityManifest(
            capability_id="sandbox:docker:run_container",
            tool_type=ToolType.SANDBOX,
            description="Sandbox run",
            risk_class=RiskClass.DANGEROUS,
            sandbox_requirement=True,
        ),
    ]

    for tool in tools:
        # 1. Under Autonomy Level 0 (Observe Only), all 4 tool types MUST be denied
        decision_l0 = enterprise_policy_engine.evaluate_invocation(
            task_id=uuid4(),
            manifest=tool,
            arguments={"param": "value"},
            autonomy_level=AutonomyLevel.OBSERVE_ONLY,
        )
        assert decision_l0.decision == PolicyDecisionType.DENY, (
            f"Tool {tool.capability_id} ({tool.tool_type.value}) bypassed Level 0 policy!"
        )

        # 2. Under Autonomy Level 1 (Recommend Only), all 4 tool types MUST require HITL
        decision_l1 = enterprise_policy_engine.evaluate_invocation(
            task_id=uuid4(),
            manifest=tool,
            arguments={"param": "value"},
            autonomy_level=AutonomyLevel.RECOMMEND_ONLY,
        )
        assert decision_l1.decision == PolicyDecisionType.REQUIRE_HITL, (
            f"Tool {tool.capability_id} ({tool.tool_type.value}) bypassed Level 1 HITL gate!"
        )

        # 3. Under Autonomy Level 3, dangerous or unbounded external tools require HITL
        if tool.risk_class in (RiskClass.DANGEROUS, RiskClass.UNBOUNDED_MUTATION):
            decision_l3 = enterprise_policy_engine.evaluate_invocation(
                task_id=uuid4(),
                manifest=tool,
                arguments={"param": "value"},
                autonomy_level=AutonomyLevel.AUTO_BOUNDED_MUTATION,
            )
            assert decision_l3.decision == PolicyDecisionType.REQUIRE_HITL, (
                f"High-risk tool {tool.capability_id} bypassed Level 3 safety boundary!"
            )
