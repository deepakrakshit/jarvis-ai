"""JARVIS Deep Agents Capability & Tool Projection Layer.

Enforces least-privilege capability projection (Layer 11 & Layer 12):
1. Capability Non-Disclosure: Unauthorized tools are never projected into the LLM's tool schema.
2. Interception Guard: Out-of-projection or unapproved tool calls are rejected at runtime.
3. Policy Integration: Invocation decisions flow through the JARVIS Policy Engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage

from jarvis.core.capabilities.manifest import (
    CapabilityManifest,
    RiskClass,
    SideEffectClass,
    ToolType,
)
from jarvis.core.logging import get_logger
from jarvis.core.policy.decision import AutonomyLevel, PolicyDecisionType
from jarvis.deep_agents.models import (
    DELEGATION_TOOLS,
    EXEC_TOOLS,
    MUTATING_FS_TOOLS,
    READ_ONLY_FS_TOOLS,
    DeepAgentToolName,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from deepagents.middleware.filesystem import FsToolName
    from langgraph.types import Command

    from jarvis.core.policy.engine import PolicyEngine

logger = get_logger(__name__)


def extract_tool_name(tool: Any) -> str:
    """Extract tool identifier name from BaseTool, function, or schema dictionary."""
    if hasattr(tool, "name"):
        return str(tool.name)
    if isinstance(tool, dict):
        if "name" in tool:
            return str(tool["name"])
        if "function" in tool and isinstance(tool["function"], dict) and "name" in tool["function"]:
            return str(tool["function"]["name"])
    if hasattr(tool, "__name__"):
        return str(tool.__name__)
    return str(tool)


class JarvisCapabilityProjector:
    """Calculates model-visible tool projections from active task scopes and autonomy levels."""

    @classmethod
    def compute_projected_tools(
        cls,
        task_scopes: frozenset[str],
        autonomy_level: AutonomyLevel = AutonomyLevel.AUTO_BOUNDED_MUTATION,
    ) -> frozenset[str]:
        """Determine which Deep Agents tools are authorized for exposure to the agent model.

        Capability Non-Disclosure Rules:
        1. Level 0 (Observe Only): No tool executions permitted. Returns empty set.
        2. Read-only filesystem tools ('read_file', 'ls', 'glob', 'grep') require 'sandbox:read' or 'fs:read'.
        3. Mutating filesystem tools ('write_file', 'edit_file', 'delete') require 'sandbox:write' or 'fs:write'
           and autonomy level >= AUTO_BOUNDED_MUTATION (Level 3).
        4. Command execution ('execute') requires 'sandbox:execute' and autonomy level >= AUTO_BOUNDED_MUTATION.
        5. Subagent delegation ('task') requires 'agent:subagent:delegate'.
        """
        if autonomy_level == AutonomyLevel.OBSERVE_ONLY:
            return frozenset()

        projected: set[str] = set()

        # Read-only filesystem tools
        has_read = bool(task_scopes.intersection({"sandbox:read", "fs:read"}))
        if has_read:
            projected.update(READ_ONLY_FS_TOOLS)

        # Mutating filesystem tools
        has_write = bool(task_scopes.intersection({"sandbox:write", "fs:write"}))
        if has_write and autonomy_level >= AutonomyLevel.AUTO_BOUNDED_MUTATION:
            projected.update(MUTATING_FS_TOOLS)

        # Command execution
        has_exec = "sandbox:execute" in task_scopes
        if has_exec and autonomy_level >= AutonomyLevel.AUTO_BOUNDED_MUTATION:
            projected.update(EXEC_TOOLS)

        # Subagent delegation
        has_delegate = "agent:subagent:delegate" in task_scopes
        if has_delegate:
            projected.update(DELEGATION_TOOLS)

        logger.debug(
            "deepagent_tools_projected",
            projected_count=len(projected),
            tools=sorted(projected),
            autonomy=autonomy_level.value,
        )
        return frozenset(projected)

    @classmethod
    def build_filesystem_tools_list(
        cls,
        projected_tools: frozenset[str],
    ) -> list[FsToolName] | None:
        """Derive the list of FsToolName literals for FilesystemMiddleware(tools=...).

        If 'read_file' is not in projected_tools, returns None because FilesystemMiddleware
        requires 'read_file' in any explicit tools list.
        """
        if DeepAgentToolName.READ_FILE.value not in projected_tools:
            return None

        fs_candidates: list[str] = [
            DeepAgentToolName.LS.value,
            DeepAgentToolName.READ_FILE.value,
            DeepAgentToolName.WRITE_FILE.value,
            DeepAgentToolName.EDIT_FILE.value,
            DeepAgentToolName.DELETE.value,
            DeepAgentToolName.GLOB.value,
            DeepAgentToolName.GREP.value,
            DeepAgentToolName.EXECUTE.value,
        ]
        active_fs = [c for c in fs_candidates if c in projected_tools]
        return active_fs  # type: ignore[return-value]


class JarvisToolGovernanceMiddleware(AgentMiddleware[Any, Any, Any]):
    """Enforces capability non-disclosure and runtime zero-trust policy evaluation.

    1. In wrap_model_call / awrap_model_call:
       Strips any tools from request.tools that are not authorized, guaranteeing
       the LLM never receives unauthorized tool definitions.
    2. In wrap_tool_call / awrap_tool_call:
       Intercepts tool calls, verifies policy authorization, and halts or flags
       human approval requirements before handler dispatch.
    """

    name: str = "JarvisToolGovernanceMiddleware"

    def __init__(
        self,
        authorized_tools: frozenset[str],
        policy_engine: PolicyEngine | None = None,
        task_id: UUID | None = None,
        autonomy_level: AutonomyLevel = AutonomyLevel.AUTO_BOUNDED_MUTATION,
    ) -> None:
        super().__init__()
        self.authorized_tools = authorized_tools
        self.policy_engine = policy_engine
        self.task_id = task_id or uuid4()
        self.autonomy_level = autonomy_level

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """Filter out unauthorized tools before they reach the model (Capability Non-Disclosure)."""
        filtered_tools = [
            tool for tool in request.tools if extract_tool_name(tool) in self.authorized_tools
        ]
        request = request.override(tools=filtered_tools)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Async version: Filter out unauthorized tools before they reach the model."""
        filtered_tools = [
            tool for tool in request.tools if extract_tool_name(tool) in self.authorized_tools
        ]
        request = request.override(tools=filtered_tools)
        return await handler(request)

    def _evaluate_tool_policy(self, tool_name: str, args: dict[str, Any]) -> ToolMessage | None:
        """Evaluate whether a tool invocation is allowed, denied, or requires HITL."""
        tool_call_id = args.get("tool_call_id", "")

        # 1. Non-disclosure / projection enforcement
        if tool_name not in self.authorized_tools:
            logger.warning(
                "unauthorized_tool_invocation_blocked",
                tool=tool_name,
                authorized=sorted(self.authorized_tools),
            )
            return ToolMessage(
                content=f"Error: Tool '{tool_name}' is not authorized under current task policy projection.",
                tool_call_id=tool_call_id,
                name=tool_name,
                status="error",
            )

        # 2. Centralized Policy Engine evaluation
        if self.policy_engine is not None:
            # Map tool to capability risk class
            risk_class = (
                RiskClass.READ_ONLY
                if tool_name in READ_ONLY_FS_TOOLS
                else RiskClass.BOUNDED_MUTATION
            )
            manifest = CapabilityManifest(
                capability_id=f"deepagent:{tool_name}",
                description=f"Invocation of Deep Agents tool {tool_name}",
                risk_class=risk_class,
                side_effect_class=SideEffectClass.NON_IDEMPOTENT
                if risk_class == RiskClass.BOUNDED_MUTATION
                else SideEffectClass.NONE,
                tool_type=ToolType.SANDBOX,
            )
            decision = self.policy_engine.evaluate_invocation(
                task_id=self.task_id,
                manifest=manifest,
                arguments=args,
                autonomy_level=self.autonomy_level,
            )

            if decision.decision == PolicyDecisionType.REQUIRE_HITL:
                logger.info(
                    "deepagent_tool_hitl_required",
                    tool=tool_name,
                    reason=decision.reason,
                )
                return ToolMessage(
                    content=f"Action paused: Tool '{tool_name}' requires human-in-the-loop approval ({decision.reason}).",
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    status="error",
                )

            if decision.decision == PolicyDecisionType.DENY:
                logger.warning(
                    "deepagent_tool_policy_denied",
                    tool=tool_name,
                    reason=decision.reason,
                )
                return ToolMessage(
                    content=f"Action blocked: Tool '{tool_name}' denied by policy ({decision.reason}).",
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    status="error",
                )

        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Intercept tool call for zero-trust authorization check."""
        name = request.tool_call.get("name", "")
        call_id = request.tool_call.get("id", "")
        args = {"tool_call_id": call_id, **request.tool_call.get("args", {})}

        blocked = self._evaluate_tool_policy(name, args)
        if blocked is not None:
            return blocked

        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Async version: Intercept tool call for zero-trust authorization check."""
        name = request.tool_call.get("name", "")
        call_id = request.tool_call.get("id", "")
        args = {"tool_call_id": call_id, **request.tool_call.get("args", {})}

        blocked = self._evaluate_tool_policy(name, args)
        if blocked is not None:
            return blocked

        return await handler(request)
