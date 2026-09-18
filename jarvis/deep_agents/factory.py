"""JARVIS Deep Agents Factory and Lifecycle Orchestration.

Assembles fully governed Deep Agent graphs with zero-trust capability projection,
monotonic subagent attenuation, and fail-closed host isolation boundaries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend

from jarvis.core.exceptions import (
    SandboxBackendUnavailableError,
)
from jarvis.core.logging import get_logger
from jarvis.core.policy.decision import AutonomyLevel
from jarvis.deep_agents.compatibility import verify_deepagents_compatibility
from jarvis.deep_agents.governor import JarvisSubagentGovernor
from jarvis.deep_agents.models import SubagentPolicyContract
from jarvis.deep_agents.projection import (
    JarvisCapabilityProjector,
    JarvisToolGovernanceMiddleware,
    extract_tool_name,
)
from jarvis.sandbox.detector import get_system_capabilities
from jarvis.sandbox.models import CapabilityStatus, ProviderCategory, SandboxState
from jarvis.sandbox.provider import DockerSandboxProvider

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from deepagents.middleware.subagents import SubAgent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.runnables import RunnableConfig
    from langchain_core.tools.base import BaseTool
    from langgraph.graph.state import CompiledStateGraph

    from jarvis.core.broker.broker import ActionBroker
    from jarvis.core.policy.engine import PolicyEngine
    from jarvis.deep_agents.backend import JarvisSandboxBackend
    from jarvis.sandbox.manager import SandboxLifecycleManager

logger = get_logger(__name__)


class GovernedDeepAgentHandle:
    """Execution handle wrapping a compiled Deep Agent graph and its governed sandbox lifecycle."""

    def __init__(
        self,
        graph: CompiledStateGraph[Any, Any, Any, Any],
        backend: JarvisSandboxBackend | CompositeBackend,
        lifecycle_manager: SandboxLifecycleManager | None = None,
        name: str = "governed_deep_agent",
    ) -> None:
        self.graph = graph
        self.backend = backend
        self.lifecycle_manager = lifecycle_manager
        self.name = name

    async def ainvoke(
        self,
        input_data: dict[str, Any],
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Asynchronously invoke the governed agent graph."""
        res = await self.graph.ainvoke(input_data, config=config)
        return dict(res) if isinstance(res, dict) else {"result": res}

    def invoke(
        self,
        input_data: dict[str, Any],
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Synchronously invoke the governed agent graph."""
        res = self.graph.invoke(input_data, config=config)
        return dict(res) if isinstance(res, dict) else {"result": res}

    async def terminate(self, reason: str = "normal_completion") -> SandboxState:
        """Quiesce, terminate, destroy, and verify absence of the underlying sandbox container."""
        if isinstance(self.backend, CompositeBackend):
            last_state = SandboxState.TERMINATED
            for b in [self.backend.default, *self.backend.routes.values()]:
                if hasattr(b, "sandbox_id") and hasattr(b, "provider"):
                    if self.lifecycle_manager is not None:
                        last_state = await self.lifecycle_manager.terminate_sandbox(
                            b.sandbox_id, reason=reason
                        )
                    else:
                        await b.provider.terminate(b.sandbox_id, reason=reason)
                        await b.provider.cleanup(b.sandbox_id)
                        last_state = await b.provider.inspect(b.sandbox_id)
            return last_state

        if self.lifecycle_manager is not None:
            return await self.lifecycle_manager.terminate_sandbox(
                self.backend.sandbox_id, reason=reason
            )

        # Fallback to direct provider cleanup and verification
        provider = self.backend.provider
        await provider.terminate(self.backend.sandbox_id, reason=reason)
        await provider.cleanup(self.backend.sandbox_id)
        verified_state = await provider.inspect(self.backend.sandbox_id)
        return verified_state


def resolve_canonical_gemini_model(
    model: str | BaseChatModel | None = None,
) -> BaseChatModel:
    """Resolve and construct the authoritative chat model for Deep Agents.

    Core Invariants:
    1. Dynamic Discovery: Inspects configured roster (MODEL_ROSTER.md, router pools)
       without hardcoding fixed model identifiers.
    2. Never None: Deep Agents' deprecated model=None path defaults to Anthropic Claude
       and requires ANTHROPIC_API_KEY. JARVIS explicitly resolves and passes an authorized
       BaseChatModel instance.
    3. Tool Capability Verification: Guarantees selected candidate supports tool binding.
    4. Single Source of Truth: Reuses existing JARVIS settings from jarvis.core.config.get_settings().
    """
    from jarvis.deep_agents.selection import resolve_governed_agent_model

    metadata = resolve_governed_agent_model(model)
    return metadata.chat_model


def create_governed_deep_agent(
    model: str | BaseChatModel | None = None,
    backend: JarvisSandboxBackend | CompositeBackend | None = None,
    task_id: UUID | None = None,
    task_scopes: frozenset[str] = frozenset({"sandbox:read", "sandbox:write", "sandbox:execute"}),
    autonomy_level: AutonomyLevel = AutonomyLevel.AUTO_BOUNDED_MUTATION,
    policy_engine: PolicyEngine | None = None,
    action_broker: ActionBroker | None = None,
    lifecycle_manager: SandboxLifecycleManager | None = None,
    subagents: Sequence[SubAgent] | None = None,
    system_prompt: str | None = None,
    extra_tools: Sequence[BaseTool] | None = None,
    name: str = "governed_deep_agent",
    allow_test_doubles: bool = False,
    permissions: Sequence[FilesystemPermission] | None = None,
    interrupt_on: dict[str, Any] | None = None,
    checkpointer: Any | None = None,
) -> GovernedDeepAgentHandle:
    """Construct and compile a Deep Agent graph bound to JARVIS control plane governance.

    Enforces:
    1. Zero-Trust Sandbox Binding: Requires an explicit JarvisSandboxBackend (or CompositeBackend containing one).
    2. Fail Closed: Rejects test double backends if allow_test_doubles is False.
       Fails closed if Docker daemon is offline when Docker provider is targeted.
    3. Capability Non-Disclosure: Unauthorized tools are completely omitted from the model schema.
    4. Monotonic Subagent Attenuation: Subagents are bounded strictly by parent capabilities.
    5. Clean Termination & Container Absence Verification.
    6. Middleware Composition Non-Loss: Preserves upstream FilesystemMiddleware permissions and HITL.
    """
    # 0. Upstream Deep Agents compatibility verification
    verify_deepagents_compatibility()

    # Canonical model resolution: Enforce Gemini provider; never let model=None reach create_deep_agent
    resolved_model = resolve_canonical_gemini_model(model)

    if backend is None:
        raise SandboxBackendUnavailableError(
            "Governed Deep Agent requires an explicit JarvisSandboxBackend or CompositeBackend instance."
        )

    # Resolve primary sandbox for fail-closed validation and governance
    primary_sandbox: Any
    if isinstance(backend, CompositeBackend):
        sandboxes = [
            b
            for b in [backend.default, *backend.routes.values()]
            if hasattr(b, "provider") and hasattr(b, "sandbox_id")
        ]
        if not sandboxes:
            raise SandboxBackendUnavailableError(
                "CompositeBackend must route to at least one JarvisSandboxBackend."
            )
        primary_sandbox = sandboxes[0]
    elif hasattr(backend, "provider"):
        primary_sandbox = backend
    else:
        raise SandboxBackendUnavailableError(
            "Governed Deep Agent requires a backend providing containment."
        )

    # 1. Fail-closed backend availability validation
    if primary_sandbox.provider_category == ProviderCategory.TEST_DOUBLE and not allow_test_doubles:
        raise SandboxBackendUnavailableError(
            "Test double sandbox provider is prohibited for live execution tasks."
        )

    if isinstance(primary_sandbox.provider, DockerSandboxProvider):
        caps = get_system_capabilities()
        if caps.docker_daemon_available != CapabilityStatus.SUPPORTED and not allow_test_doubles:
            raise SandboxBackendUnavailableError(
                "Docker container daemon is offline; real Tier-1 isolation cannot be provided."
            )

    active_task_id = task_id or primary_sandbox.task_id

    # 2. Tool Projection (Capability Non-Disclosure)
    projected_tools = JarvisCapabilityProjector.compute_projected_tools(
        task_scopes=task_scopes,
        autonomy_level=autonomy_level,
    )

    # Authorized tools include projected filesystem/exec tools plus caller-provided extra tools
    authorized_tools_set = set(projected_tools)
    if extra_tools:
        authorized_tools_set.update(extract_tool_name(t) for t in extra_tools)
    authorized_tools = frozenset(authorized_tools_set)

    # 3. Assemble Middleware
    # Zero-Trust Invariant (Section 15): Do NOT replace the upstream FilesystemMiddleware
    # with a custom FilesystemMiddleware(tools=...), as doing so would drop upstream
    # _permissions and interrupt_on configurations. Instead, allow create_deep_agent
    # to instantiate its standard FilesystemMiddleware with permissions intact, and enforce
    # capability non-disclosure and runtime policy via JarvisToolGovernanceMiddleware.
    governance_middleware = JarvisToolGovernanceMiddleware(
        authorized_tools=authorized_tools,
        policy_engine=policy_engine,
        task_id=active_task_id,
        autonomy_level=autonomy_level,
    )

    middleware_stack = [governance_middleware]

    # 4. Subagent Governance
    parent_contract = SubagentPolicyContract(
        name=name,
        parent_agent_id="root",
        authorized_tools=authorized_tools,
        authorized_scopes=task_scopes,
        max_autonomy_level=autonomy_level,
        allow_subagent_nesting=False,
    )
    governor = JarvisSubagentGovernor(
        parent_contract=parent_contract,
        parent_backend=primary_sandbox,
        policy_engine=policy_engine,
        parent_permissions=permissions,
    )
    governed_subagents = governor.govern_subagent_specs(subagents)

    # 5. Compile StateGraph via official Deep Agents API
    graph = create_deep_agent(
        model=resolved_model,
        backend=backend,
        tools=list(extra_tools or []),
        middleware=middleware_stack,
        subagents=governed_subagents if governed_subagents else None,
        system_prompt=system_prompt,
        name=name,
        permissions=list(permissions) if permissions else None,
        interrupt_on=interrupt_on,
        checkpointer=checkpointer,
    )

    logger.info(
        "governed_deep_agent_created",
        name=name,
        task_id=str(active_task_id),
        sandbox_id=str(primary_sandbox.sandbox_id),
        projected_tools=sorted(projected_tools),
    )

    return GovernedDeepAgentHandle(
        graph=graph,
        backend=backend,
        lifecycle_manager=lifecycle_manager,
        name=name,
    )
