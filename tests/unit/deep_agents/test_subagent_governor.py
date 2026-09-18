"""Unit tests for JarvisSubagentGovernor.

Validates:
1. Monotonic Capability Attenuation: child capability set <= parent authorized capability set.
2. Anti-Escalation Protection: Subagents cannot claim tools or scopes denied to the parent.
3. Autonomy Boundaries: Subagents cannot exceed the parent's autonomy level.
4. Nesting Control: Subagents cannot spawn further untracked descendants.
5. Delegation Authority: Subagents cannot be registered if parent lacks delegation scope.
6. Middleware Injection: Subagent specs automatically receive governed interception middleware.
"""

from __future__ import annotations

import pytest
from deepagents.middleware.subagents import SubAgent
from langchain_core.tools import tool

from jarvis.core.policy.decision import AutonomyLevel
from jarvis.deep_agents.backend import JarvisSandboxBackend
from jarvis.deep_agents.governor import JarvisSubagentGovernor
from jarvis.deep_agents.models import (
    SubagentPolicyContract,
    SubagentPrivilegeEscalationError,
)
from jarvis.deep_agents.projection import JarvisToolGovernanceMiddleware
from jarvis.sandbox.provider import TestDoubleSandboxProvider


@tool
def read_tool(path: str) -> str:
    """Read tool."""
    return path


@tool
def write_tool(path: str, content: str) -> str:
    """Write tool."""
    return path


@tool
def exec_tool(command: str) -> str:
    """Execute tool."""
    return command


@pytest.fixture
def mock_parent_backend() -> JarvisSandboxBackend:
    """Fixture providing a mock-backed JarvisSandboxBackend."""
    provider = TestDoubleSandboxProvider()
    return JarvisSandboxBackend(
        provider=provider,
        allow_test_doubles=True,
    )


def test_subagent_monotonic_attenuation_success(
    mock_parent_backend: JarvisSandboxBackend,
) -> None:
    """Validate that child contracts bounded by parent succeed."""
    parent_contract = SubagentPolicyContract(
        name="parent_agent",
        authorized_tools=frozenset({"read_file", "ls", "write_file", "task"}),
        authorized_scopes=frozenset({"sandbox:read", "sandbox:write", "agent:subagent:delegate"}),
        max_autonomy_level=AutonomyLevel.AUTO_BOUNDED_MUTATION,
        allow_subagent_nesting=True,
    )
    governor = JarvisSubagentGovernor(
        parent_contract=parent_contract,
        parent_backend=mock_parent_backend,
    )

    child = governor.create_child_contract(
        child_name="child_worker",
        requested_tools={"read_file", "ls"},
        requested_scopes={"sandbox:read"},
        requested_autonomy=AutonomyLevel.AUTO_READ_ONLY,
    )

    assert child.name == "child_worker"
    assert child.parent_agent_id == "parent_agent"
    assert child.authorized_tools == frozenset({"read_file", "ls"})
    assert child.authorized_scopes == frozenset({"sandbox:read"})
    assert child.max_autonomy_level == AutonomyLevel.AUTO_READ_ONLY


def test_subagent_tool_escalation_blocked(
    mock_parent_backend: JarvisSandboxBackend,
) -> None:
    """Validate that a subagent requesting unauthorized tools is rejected."""
    parent_contract = SubagentPolicyContract(
        name="restricted_parent",
        authorized_tools=frozenset({"read_tool", "task"}),
        authorized_scopes=frozenset({"sandbox:read", "agent:subagent:delegate"}),
        max_autonomy_level=AutonomyLevel.AUTO_READ_ONLY,
    )
    governor = JarvisSubagentGovernor(
        parent_contract=parent_contract,
        parent_backend=mock_parent_backend,
    )

    spec: SubAgent = {
        "name": "rogue_subagent",
        "description": "Attempts to escalate to exec_tool",
        "tools": [read_tool, exec_tool],
    }

    with pytest.raises(SubagentPrivilegeEscalationError) as exc_info:
        governor.validate_and_govern_subagent(spec)

    assert "exec_tool" in str(exc_info.value)
    assert "privilege escalation" in str(exc_info.value)


def test_subagent_scope_escalation_blocked() -> None:
    """Validate that child contract cannot claim scopes not held by parent."""
    parent_contract = SubagentPolicyContract(
        name="read_only_parent",
        authorized_tools=frozenset({"read_file"}),
        authorized_scopes=frozenset({"sandbox:read"}),
        max_autonomy_level=AutonomyLevel.AUTO_READ_ONLY,
    )

    child_contract = SubagentPolicyContract(
        name="escalating_child",
        parent_agent_id="read_only_parent",
        authorized_tools=frozenset({"read_file"}),
        authorized_scopes=frozenset({"sandbox:read", "sandbox:execute"}),
        max_autonomy_level=AutonomyLevel.AUTO_READ_ONLY,
    )

    with pytest.raises(SubagentPrivilegeEscalationError) as exc_info:
        parent_contract.validate_child(child_contract)

    assert "scope escalation" in str(exc_info.value)
    assert "sandbox:execute" in str(exc_info.value)


def test_subagent_autonomy_escalation_blocked() -> None:
    """Validate that child cannot claim higher autonomy level than parent."""
    parent_contract = SubagentPolicyContract(
        name="level_2_parent",
        authorized_tools=frozenset({"read_file"}),
        authorized_scopes=frozenset({"sandbox:read"}),
        max_autonomy_level=AutonomyLevel.AUTO_READ_ONLY,
    )

    child_contract = SubagentPolicyContract(
        name="level_4_child",
        parent_agent_id="level_2_parent",
        authorized_tools=frozenset({"read_file"}),
        authorized_scopes=frozenset({"sandbox:read"}),
        max_autonomy_level=AutonomyLevel.AUTONOMOUS_BUDGETED,
    )

    with pytest.raises(SubagentPrivilegeEscalationError) as exc_info:
        parent_contract.validate_child(child_contract)

    assert "autonomy escalation" in str(exc_info.value)


def test_subagent_nesting_permission_enforcement() -> None:
    """Validate that child cannot permit nesting if parent disallows it."""
    parent_contract = SubagentPolicyContract(
        name="no_nesting_parent",
        authorized_tools=frozenset({"read_file"}),
        authorized_scopes=frozenset({"sandbox:read"}),
        allow_subagent_nesting=False,
    )

    child_contract = SubagentPolicyContract(
        name="nesting_child",
        parent_agent_id="no_nesting_parent",
        authorized_tools=frozenset({"read_file"}),
        authorized_scopes=frozenset({"sandbox:read"}),
        allow_subagent_nesting=True,
    )

    with pytest.raises(SubagentPrivilegeEscalationError) as exc_info:
        parent_contract.validate_child(child_contract)

    assert "cannot permit further subagent nesting" in str(exc_info.value)


def test_delegation_prohibited_without_parent_delegation_authority(
    mock_parent_backend: JarvisSandboxBackend,
) -> None:
    """Validate that parent cannot declare subagents without delegation authority."""
    parent_contract = SubagentPolicyContract(
        name="leaf_worker",
        authorized_tools=frozenset({"read_file"}),
        authorized_scopes=frozenset({"sandbox:read"}),
    )
    governor = JarvisSubagentGovernor(
        parent_contract=parent_contract,
        parent_backend=mock_parent_backend,
    )

    spec: SubAgent = {
        "name": "child_task",
        "description": "Subagent should be rejected",
        "tools": [],
    }

    with pytest.raises(SubagentPrivilegeEscalationError) as exc_info:
        governor.govern_subagent_specs([spec])

    assert "lacks delegation authority" in str(exc_info.value)


def test_subagent_middleware_injection(
    mock_parent_backend: JarvisSandboxBackend,
) -> None:
    """Validate that governed subagents receive JarvisToolGovernanceMiddleware."""
    parent_contract = SubagentPolicyContract(
        name="root_agent",
        authorized_tools=frozenset({"read_tool", "task"}),
        authorized_scopes=frozenset({"sandbox:read", "agent:subagent:delegate"}),
    )
    governor = JarvisSubagentGovernor(
        parent_contract=parent_contract,
        parent_backend=mock_parent_backend,
    )

    spec: SubAgent = {
        "name": "reading_subagent",
        "description": "Safe read-only subagent",
        "tools": [read_tool],
        "middleware": [],
    }

    governed = governor.validate_and_govern_subagent(spec)

    assert len(governed["middleware"]) == 1
    injected_mw = governed["middleware"][0]
    assert isinstance(injected_mw, JarvisToolGovernanceMiddleware)
    assert injected_mw.authorized_tools == frozenset({"read_tool"})
