"""JARVIS Deep Agents Integration and Governance Layer.

Integrates the official Python deepagents package as an execution intelligence
subsystem under authoritative JARVIS control plane governance (ARCHITECTURE.md Layer 12-14).
"""

from jarvis.deep_agents.backend import JarvisSandboxBackend
from jarvis.deep_agents.compatibility import (
    DeepAgentCompatibilityError,
    verify_deepagents_compatibility,
)
from jarvis.deep_agents.factory import (
    GovernedDeepAgentHandle,
    create_governed_deep_agent,
    resolve_canonical_gemini_model,
)
from jarvis.deep_agents.governor import JarvisSubagentGovernor
from jarvis.deep_agents.models import (
    ALL_DEEP_AGENT_TOOLS,
    DELEGATION_TOOLS,
    EXEC_TOOLS,
    MUTATING_FS_TOOLS,
    READ_ONLY_FS_TOOLS,
    DeepAgentGovernanceError,
    DeepAgentToolName,
    GovernedDeepAgentExecutionResult,
    HumanApprovalRequiredError,
    SubagentPolicyContract,
    SubagentPrivilegeEscalationError,
    UnauthorizedToolInvocationError,
)
from jarvis.deep_agents.projection import (
    JarvisCapabilityProjector,
    JarvisToolGovernanceMiddleware,
    extract_tool_name,
)
from jarvis.deep_agents.selection import (
    SelectedModelMetadata,
    discover_model_candidates,
    resolve_governed_agent_model,
)

__all__ = [
    "ALL_DEEP_AGENT_TOOLS",
    "DELEGATION_TOOLS",
    "EXEC_TOOLS",
    "MUTATING_FS_TOOLS",
    "READ_ONLY_FS_TOOLS",
    "DeepAgentCompatibilityError",
    "DeepAgentGovernanceError",
    "DeepAgentToolName",
    "GovernedDeepAgentExecutionResult",
    "GovernedDeepAgentHandle",
    "HumanApprovalRequiredError",
    "JarvisCapabilityProjector",
    "JarvisSandboxBackend",
    "JarvisSubagentGovernor",
    "JarvisToolGovernanceMiddleware",
    "SelectedModelMetadata",
    "SubagentPolicyContract",
    "SubagentPrivilegeEscalationError",
    "UnauthorizedToolInvocationError",
    "create_governed_deep_agent",
    "discover_model_candidates",
    "extract_tool_name",
    "resolve_canonical_gemini_model",
    "resolve_governed_agent_model",
    "verify_deepagents_compatibility",
]
