"""Unit tests for Deep Agents Capability Projection and Tool Governance Middleware.

Validates:
1. Least-Privilege Capability Projection: Scopes and autonomy determine visible tools.
2. Capability Non-Disclosure: Unauthorized tools are completely omitted from model requests.
3. Tool Call Interception: Out-of-projection calls are blocked before execution.
4. Human-In-The-Loop: Policy decisions requiring HITL pause execution with explicit notices.
5. Central Policy Denial: Policy DENY decisions block tool calls cleanly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from jarvis.core.capabilities.manifest import CapabilityManifest
from jarvis.core.policy.decision import (
    AutonomyLevel,
    PolicyDecision,
    PolicyDecisionType,
)
from jarvis.core.policy.engine import PolicyEngine
from jarvis.deep_agents.models import (
    DELEGATION_TOOLS,
    EXEC_TOOLS,
    MUTATING_FS_TOOLS,
    READ_ONLY_FS_TOOLS,
)
from jarvis.deep_agents.projection import (
    JarvisCapabilityProjector,
    JarvisToolGovernanceMiddleware,
    extract_tool_name,
)


@tool
def sample_read(path: str) -> str:
    """Sample read file tool."""
    return f"reading {path}"


@tool
def sample_execute(command: str) -> str:
    """Sample execute command tool."""
    return f"executing {command}"


@tool
def sample_write(path: str, content: str) -> str:
    """Sample write file tool."""
    return f"writing {path}"


class MockPolicyEngine(PolicyEngine):
    """Mock PolicyEngine to evaluate tool governance decisions."""

    def __init__(self, decision: PolicyDecisionType = PolicyDecisionType.ALLOW) -> None:
        super().__init__()
        self.mock_decision = decision
        self.last_manifest: CapabilityManifest | None = None

    def evaluate_invocation(
        self,
        task_id: object,
        manifest: CapabilityManifest,
        arguments: dict[str, object],
        autonomy_level: AutonomyLevel | None = None,
        target_resource: str | None = None,
        input_data: object | None = None,
        agent_id: str = "core_agent",
        user_id: str = "default_user",
    ) -> PolicyDecision:
        self.last_manifest = manifest
        return PolicyDecision(
            decision=self.mock_decision,
            reason=f"Policy decision: {self.mock_decision.value}",
            risk_score=0.1 if self.mock_decision == PolicyDecisionType.ALLOW else 0.85,
        )


def test_projector_observe_only_returns_empty() -> None:
    """Autonomy Level 0 (Observe Only) strictly forbids all tool capabilities."""
    scopes = frozenset({"sandbox:read", "sandbox:write", "sandbox:execute"})
    projected = JarvisCapabilityProjector.compute_projected_tools(
        task_scopes=scopes,
        autonomy_level=AutonomyLevel.OBSERVE_ONLY,
    )
    assert len(projected) == 0


def test_projector_read_only_scopes() -> None:
    """Read scopes project read-only tools but omit mutating and exec tools."""
    scopes = frozenset({"sandbox:read"})
    projected = JarvisCapabilityProjector.compute_projected_tools(
        task_scopes=scopes,
        autonomy_level=AutonomyLevel.AUTO_BOUNDED_MUTATION,
    )
    assert projected == READ_ONLY_FS_TOOLS
    assert "execute" not in projected
    assert "write_file" not in projected


def test_projector_autonomy_gate_on_mutating_tools() -> None:
    """Mutating tools require Autonomy Level >= AUTO_BOUNDED_MUTATION (Level 3)."""
    scopes = frozenset({"sandbox:read", "sandbox:write", "sandbox:execute"})

    # Level 2 (AUTO_READ_ONLY): mutations hidden despite scopes
    projected_l2 = JarvisCapabilityProjector.compute_projected_tools(
        task_scopes=scopes,
        autonomy_level=AutonomyLevel.AUTO_READ_ONLY,
    )
    assert projected_l2 == READ_ONLY_FS_TOOLS
    assert "execute" not in projected_l2
    assert "write_file" not in projected_l2

    # Level 3 (AUTO_BOUNDED_MUTATION): mutations projected
    projected_l3 = JarvisCapabilityProjector.compute_projected_tools(
        task_scopes=scopes,
        autonomy_level=AutonomyLevel.AUTO_BOUNDED_MUTATION,
    )
    assert READ_ONLY_FS_TOOLS.issubset(projected_l3)
    assert MUTATING_FS_TOOLS.issubset(projected_l3)
    assert EXEC_TOOLS.issubset(projected_l3)


def test_projector_subagent_delegation_scope() -> None:
    """Task delegation tool is projected only when delegation scope is present."""
    scopes_without = frozenset({"sandbox:read"})
    projected_without = JarvisCapabilityProjector.compute_projected_tools(scopes_without)
    assert "task" not in projected_without

    scopes_with = frozenset({"sandbox:read", "agent:subagent:delegate"})
    projected_with = JarvisCapabilityProjector.compute_projected_tools(scopes_with)
    assert "task" in projected_with
    assert DELEGATION_TOOLS.issubset(projected_with)


def test_build_filesystem_tools_list() -> None:
    """Validate filesystem tool list generation."""
    # When read_file is authorized
    tools = frozenset({"read_file", "ls", "execute"})
    fs_list = JarvisCapabilityProjector.build_filesystem_tools_list(tools)
    assert fs_list is not None
    assert set(fs_list) == {"read_file", "ls", "execute"}

    # When read_file is not authorized
    no_read = frozenset({"execute"})
    fs_list_none = JarvisCapabilityProjector.build_filesystem_tools_list(no_read)
    assert fs_list_none is None


def test_middleware_capability_non_disclosure_sync() -> None:
    """Validate that wrap_model_call strips unauthorized tools before model sees them."""
    authorized = frozenset({"sample_read"})
    mw = JarvisToolGovernanceMiddleware(authorized_tools=authorized)

    # Request containing both authorized and unauthorized tools
    req = ModelRequest(
        model=MagicMock(),
        messages=[],
        tools=[sample_read, sample_execute, sample_write],
    )

    captured_tools: list[object] = []

    def mock_handler(modified_req: ModelRequest[Any]) -> ModelResponse[Any]:
        if modified_req.tools is not None:
            captured_tools.extend(modified_req.tools)
        return ModelResponse(result=[])

    mw.wrap_model_call(req, mock_handler)

    assert len(captured_tools) == 1
    assert extract_tool_name(captured_tools[0]) == "sample_read"


@pytest.mark.asyncio
async def test_middleware_capability_non_disclosure_async() -> None:
    """Validate that awrap_model_call strips unauthorized tools asynchronously."""
    authorized = frozenset({"sample_read"})
    mw = JarvisToolGovernanceMiddleware(authorized_tools=authorized)

    req = ModelRequest(
        model=MagicMock(),
        messages=[],
        tools=[sample_read, sample_execute, sample_write],
    )

    captured_tools: list[object] = []

    async def mock_handler(modified_req: ModelRequest[Any]) -> ModelResponse[Any]:
        if modified_req.tools is not None:
            captured_tools.extend(modified_req.tools)
        return ModelResponse(result=[])

    await mw.awrap_model_call(req, mock_handler)

    assert len(captured_tools) == 1
    assert extract_tool_name(captured_tools[0]) == "sample_read"


def test_middleware_tool_call_unauthorized_interception() -> None:
    """Validate that out-of-projection tool calls are blocked at invocation."""
    authorized = frozenset({"read_file"})
    mw = JarvisToolGovernanceMiddleware(authorized_tools=authorized)

    # Attempt to invoke 'execute' which is not authorized
    req = ToolCallRequest(
        tool_call={"name": "execute", "args": {"command": "whoami"}, "id": "call-123"},
        tool=None,
        state={},
        runtime=MagicMock(),
    )

    handler_called = False

    def mock_handler(r: ToolCallRequest) -> ToolMessage:
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="ran", tool_call_id="call-123")

    result = mw.wrap_tool_call(req, mock_handler)

    assert not handler_called
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "not authorized under current task policy projection" in result.content


@pytest.mark.asyncio
async def test_middleware_hitl_pause_enforcement() -> None:
    """Validate that policy REQUIRE_HITL returns an explicit pause message."""
    hitl_engine = MockPolicyEngine(decision=PolicyDecisionType.REQUIRE_HITL)
    mw = JarvisToolGovernanceMiddleware(
        authorized_tools=frozenset({"execute"}),
        policy_engine=hitl_engine,
        task_id=uuid4(),
    )

    req = ToolCallRequest(
        tool_call={"name": "execute", "args": {"command": "rm -rf /"}, "id": "call-456"},
        tool=None,
        state={},
        runtime=MagicMock(),
    )

    result = await mw.awrap_tool_call(req, MagicMock())

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "requires human-in-the-loop approval" in result.content


@pytest.mark.asyncio
async def test_middleware_policy_denial_enforcement() -> None:
    """Validate that policy DENY returns an explicit policy blocked message."""
    deny_engine = MockPolicyEngine(decision=PolicyDecisionType.DENY)
    mw = JarvisToolGovernanceMiddleware(
        authorized_tools=frozenset({"execute"}),
        policy_engine=deny_engine,
        task_id=uuid4(),
    )

    req = ToolCallRequest(
        tool_call={"name": "execute", "args": {"command": "format_c"}, "id": "call-789"},
        tool=None,
        state={},
        runtime=MagicMock(),
    )

    result = await mw.awrap_tool_call(req, MagicMock())

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "denied by policy" in result.content
