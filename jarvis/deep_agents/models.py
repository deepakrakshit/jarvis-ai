"""JARVIS Deep Agents Governance Contracts and Models.

Defines typed contracts, tool mapping taxonomies, subagent capability
envelopes, and policy bounds governing Deep Agent execution.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from jarvis.core.exceptions import SecurityViolationError
from jarvis.core.policy.decision import AutonomyLevel


class DeepAgentGovernanceError(SecurityViolationError):
    """Base exception for Deep Agents policy and capability governance violations."""


class SubagentPrivilegeEscalationError(DeepAgentGovernanceError):
    """Raised when a subagent attempts privilege escalation beyond its parent envelope."""


class UnauthorizedToolInvocationError(DeepAgentGovernanceError):
    """Raised when an agent attempts to invoke a capability tool not authorized by policy."""


class HumanApprovalRequiredError(DeepAgentGovernanceError):
    """Raised when an operation requires explicit human-in-the-loop authorization."""


class DeepAgentToolName(StrEnum):
    """Canonical tool names provided by Deep Agents filesystem and execution layers."""

    EXECUTE = "execute"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    EDIT_FILE = "edit_file"
    DELETE = "delete"
    LS = "ls"
    GLOB = "glob"
    GREP = "grep"
    TASK = "task"


READ_ONLY_FS_TOOLS: frozenset[str] = frozenset(
    {
        DeepAgentToolName.READ_FILE.value,
        DeepAgentToolName.LS.value,
        DeepAgentToolName.GLOB.value,
        DeepAgentToolName.GREP.value,
    }
)

MUTATING_FS_TOOLS: frozenset[str] = frozenset(
    {
        DeepAgentToolName.WRITE_FILE.value,
        DeepAgentToolName.EDIT_FILE.value,
        DeepAgentToolName.DELETE.value,
    }
)

EXEC_TOOLS: frozenset[str] = frozenset({DeepAgentToolName.EXECUTE.value})

DELEGATION_TOOLS: frozenset[str] = frozenset({DeepAgentToolName.TASK.value})

ALL_DEEP_AGENT_TOOLS: frozenset[str] = (
    READ_ONLY_FS_TOOLS | MUTATING_FS_TOOLS | EXEC_TOOLS | DELEGATION_TOOLS
)

TOOL_REQUIRED_SCOPES: dict[str, tuple[str, ...]] = {
    DeepAgentToolName.READ_FILE.value: ("sandbox:read", "fs:read"),
    DeepAgentToolName.LS.value: ("sandbox:read", "fs:read"),
    DeepAgentToolName.GLOB.value: ("sandbox:read", "fs:read"),
    DeepAgentToolName.GREP.value: ("sandbox:read", "fs:read"),
    DeepAgentToolName.WRITE_FILE.value: ("sandbox:write", "fs:write"),
    DeepAgentToolName.EDIT_FILE.value: ("sandbox:write", "fs:write"),
    DeepAgentToolName.DELETE.value: ("sandbox:write", "fs:write"),
    DeepAgentToolName.EXECUTE.value: ("sandbox:execute",),
    DeepAgentToolName.TASK.value: ("agent:subagent:delegate",),
}


class SubagentPolicyContract(BaseModel):
    """Policy envelope bounding subagent capabilities (child <= parent invariant)."""

    model_config = ConfigDict(frozen=True)

    name: str
    parent_agent_id: str = "main"
    authorized_tools: frozenset[str] = Field(default_factory=frozenset)
    authorized_scopes: frozenset[str] = Field(default_factory=frozenset)
    max_autonomy_level: AutonomyLevel = AutonomyLevel.AUTO_BOUNDED_MUTATION
    allow_subagent_nesting: bool = False

    def validate_child(self, child: SubagentPolicyContract) -> None:
        """Validate that a proposed child subagent does not escalate privileges.

        Enforces the monotonic attenuation invariant:
        child capability set <= parent authorized capability set.
        """
        excess_tools = child.authorized_tools - self.authorized_tools
        if excess_tools:
            raise SubagentPrivilegeEscalationError(
                f"Subagent '{child.name}' attempts privilege escalation: "
                f"tools {sorted(excess_tools)} exceed parent '{self.name}' authorized tools {sorted(self.authorized_tools)}."
            )

        excess_scopes = child.authorized_scopes - self.authorized_scopes
        if excess_scopes:
            raise SubagentPrivilegeEscalationError(
                f"Subagent '{child.name}' attempts scope escalation: "
                f"scopes {sorted(excess_scopes)} exceed parent '{self.name}' authorized scopes {sorted(self.authorized_scopes)}."
            )

        if child.max_autonomy_level > self.max_autonomy_level:
            raise SubagentPrivilegeEscalationError(
                f"Subagent '{child.name}' attempts autonomy escalation: "
                f"level {child.max_autonomy_level.value} exceeds parent '{self.name}' level {self.max_autonomy_level.value}."
            )

        if not self.allow_subagent_nesting and child.allow_subagent_nesting:
            raise SubagentPrivilegeEscalationError(
                f"Subagent '{child.name}' cannot permit further subagent nesting when parent '{self.name}' disallows nesting."
            )


class GovernedDeepAgentExecutionResult(BaseModel):
    """Execution telemetry and audit record for a governed Deep Agent run."""

    model_config = ConfigDict(frozen=True)

    execution_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    sandbox_id: UUID
    agent_name: str
    tools_invoked: list[str] = Field(default_factory=list)
    blocked_tool_invocations: list[str] = Field(default_factory=list)
    hitl_pauses: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    output_summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
