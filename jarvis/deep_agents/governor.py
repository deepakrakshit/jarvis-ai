"""JARVIS Deep Agents Subagent Governor.

Enforces monotonic attenuation and capability containment on all subagents:
1. Invariant: child capability set <= parent authorized capability set.
2. Anti-Escalation: Subagents cannot request or execute tools denied to the parent.
3. Containment Inheritance: Subagents inherit the parent's governed sandbox boundary.
4. Nesting Control: Subagents cannot spawn further untracked descendants without parent permission.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jarvis.core.logging import get_logger
from jarvis.core.policy.decision import AutonomyLevel
from jarvis.deep_agents.models import (
    EXEC_TOOLS,
    MUTATING_FS_TOOLS,
    SubagentPolicyContract,
    SubagentPrivilegeEscalationError,
)
from jarvis.deep_agents.projection import (
    JarvisToolGovernanceMiddleware,
    extract_tool_name,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from deepagents import FilesystemPermission
    from deepagents.middleware.subagents import SubAgent

    from jarvis.core.policy.engine import PolicyEngine
    from jarvis.deep_agents.backend import JarvisSandboxBackend

logger = get_logger(__name__)


def _patterns_overlap(p1: str, p2: str) -> bool:
    """Check if two path patterns overlap or if one is a subpath of another."""
    c1 = p1.rstrip("/*").rstrip("/")
    c2 = p2.rstrip("/*").rstrip("/")
    return c1.startswith(c2) or c2.startswith(c1)


class JarvisSubagentGovernor:
    """Oversees subagent creation, capability bounds, and parent-child containment."""

    def __init__(
        self,
        parent_contract: SubagentPolicyContract,
        parent_backend: JarvisSandboxBackend,
        policy_engine: PolicyEngine | None = None,
        parent_permissions: Sequence[FilesystemPermission] | None = None,
    ) -> None:
        self.parent_contract = parent_contract
        self.parent_backend = parent_backend
        self.policy_engine = policy_engine
        self.parent_permissions = parent_permissions

    def create_child_contract(
        self,
        child_name: str,
        requested_tools: set[str] | None = None,
        requested_scopes: set[str] | None = None,
        requested_autonomy: AutonomyLevel | None = None,
        allow_nesting: bool = False,
    ) -> SubagentPolicyContract:
        """Derive an attenuated child policy contract strictly bounded by parent capabilities."""
        # Monotonic capability attenuation
        effective_tools = (
            frozenset(requested_tools)
            if requested_tools is not None
            else self.parent_contract.authorized_tools
        )
        effective_scopes = (
            frozenset(requested_scopes)
            if requested_scopes is not None
            else self.parent_contract.authorized_scopes
        )
        effective_autonomy = (
            min(requested_autonomy, self.parent_contract.max_autonomy_level)
            if requested_autonomy is not None
            else self.parent_contract.max_autonomy_level
        )

        child = SubagentPolicyContract(
            name=child_name,
            parent_agent_id=self.parent_contract.name,
            authorized_tools=effective_tools,
            authorized_scopes=effective_scopes,
            max_autonomy_level=effective_autonomy,
            allow_subagent_nesting=allow_nesting and self.parent_contract.allow_subagent_nesting,
        )

        # Validate monotonic attenuation invariant
        self.parent_contract.validate_child(child)
        return child

    def validate_and_govern_subagent(self, spec: SubAgent) -> SubAgent:
        """Validate and govern an individual subagent specification."""
        name = spec.get("name", "anonymous_subagent")

        # 1. Reject fork mode to prevent internal state, secret, and witness leaks
        if spec.get("mode") == "fork":
            raise SubagentPrivilegeEscalationError(
                f"Subagent '{name}' requested mode='fork' which is prohibited under "
                f"JARVIS zero-trust governance to prevent state, witness, and secret leakage. "
                f"Use 'mode=isolated' instead."
            )

        raw_tools = spec.get("tools") or []
        declared_tool_names = {extract_tool_name(t) for t in raw_tools}

        # 2. Check for unauthorized tool declarations
        unauthorized = declared_tool_names - set(self.parent_contract.authorized_tools)
        if unauthorized:
            raise SubagentPrivilegeEscalationError(
                f"Subagent '{name}' attempts privilege escalation: declared tools {sorted(unauthorized)} "
                f"which are not in parent '{self.parent_contract.name}' authorized tools: "
                f"{sorted(self.parent_contract.authorized_tools)}."
            )

        # 3. Check tool vs parent autonomy level constraints
        if self.parent_contract.max_autonomy_level < AutonomyLevel.AUTO_BOUNDED_MUTATION and any(
            t in (MUTATING_FS_TOOLS | EXEC_TOOLS) for t in declared_tool_names
        ):
            raise SubagentPrivilegeEscalationError(
                f"Subagent '{name}' declared mutating or execution tools which exceed parent "
                f"read-only autonomy level {self.parent_contract.max_autonomy_level.value}."
            )

        # 4. Monotonic Filesystem Permission Attenuation
        governed_permissions: list[FilesystemPermission] | None = None
        if self.parent_permissions:
            parent_denies = [
                p for perm in self.parent_permissions if perm.mode == "deny" for p in perm.paths
            ]
            parent_allows = [
                p for perm in self.parent_permissions if perm.mode == "allow" for p in perm.paths
            ]
            child_perms = spec.get("permissions")
            if child_perms is not None:
                for cp in child_perms:
                    if cp.mode == "allow":
                        # Cannot allow what parent denies
                        for p in cp.paths:
                            if any(_patterns_overlap(p, dp) for dp in parent_denies):
                                raise SubagentPrivilegeEscalationError(
                                    f"Subagent '{name}' attempts permission escalation: "
                                    f"parent denies '{p}', but child requested allow."
                                )
                        # Cannot allow outside parent allowed scope
                        if parent_allows:
                            for p in cp.paths:
                                if not any(_patterns_overlap(p, ap) for ap in parent_allows):
                                    raise SubagentPrivilegeEscalationError(
                                        f"Subagent '{name}' attempts permission escalation: "
                                        f"path '{p}' exceeds parent allowed scope."
                                    )
                effective_child_perms = list(child_perms)
                for pp in self.parent_permissions:
                    if pp.mode == "deny" and pp not in effective_child_perms:
                        effective_child_perms.append(pp)
                governed_permissions = effective_child_perms
            else:
                governed_permissions = list(self.parent_permissions)

        # 5. Derive child policy contract
        child_tools = (
            frozenset(declared_tool_names)
            if declared_tool_names
            else self.parent_contract.authorized_tools
        )
        child_contract = self.create_child_contract(
            child_name=name,
            requested_tools=set(child_tools),
        )

        # 6. Inject JARVIS Tool Governance Middleware into subagent middleware stack
        existing_middleware = list(spec.get("middleware") or [])
        governance_mw = JarvisToolGovernanceMiddleware(
            authorized_tools=child_contract.authorized_tools,
            policy_engine=self.policy_engine,
            task_id=self.parent_backend.task_id,
            autonomy_level=child_contract.max_autonomy_level,
        )
        existing_middleware.append(governance_mw)

        # 7. Clone and return governed spec
        governed_spec: SubAgent = {
            **spec,
            "middleware": existing_middleware,
        }
        if governed_permissions is not None:
            governed_spec["permissions"] = governed_permissions
        return governed_spec

    def govern_subagent_specs(
        self,
        subagent_specs: Sequence[SubAgent] | None,
    ) -> list[SubAgent]:
        """Validate all subagent specifications against parent policy envelope."""
        if not subagent_specs:
            return []

        # Verify parent possesses delegation capability
        if (
            "agent:subagent:delegate" not in self.parent_contract.authorized_scopes
            and "task" not in self.parent_contract.authorized_tools
        ):
            raise SubagentPrivilegeEscalationError(
                f"Parent '{self.parent_contract.name}' lacks delegation authority ('task' or 'agent:subagent:delegate'). "
                f"Cannot register subagents."
            )

        governed: list[SubAgent] = []
        for spec in subagent_specs:
            governed.append(self.validate_and_govern_subagent(spec))

        logger.info(
            "subagents_governed",
            parent=self.parent_contract.name,
            subagents=[s.get("name") for s in governed],
        )
        return governed
