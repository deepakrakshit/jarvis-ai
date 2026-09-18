"""Compiled Deep Agents Security Boundary and Black-Box Audit Suite.

Verifies end-to-end compiled agent behavior:
1. Tool Non-Disclosure on compiled model requests.
2. Filesystem permissions survival through middleware composition.
3. Interrupt / HITL graph pausing on restricted paths.
4. Allow-list and deny-list enforcement.
5. AND-composition between Deep Agents permissions and JARVIS Policy Engine.
6. Direct backend bypass prevention.
7. Execute tool authorization and SandboxBackendProtocol constraints.
8. Fail-closed Docker unavailability (no Tier-0 host fallback).
9. Subagent monotonic attenuation (tools, scopes, autonomy, permissions).
10. Fork-mode rejection to prevent state and secret leakage.
11. Subagent model-visible tool surface capture.
12. Private upstream API compatibility guard.
13. Output integrity and exit-code fidelity.
14. Path traversal, normalization, and encoding defenses.
15. Control plane secret canary containment.
16. Real-model smoke test (run if API key present, reported NOT RUN if absent).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from deepagents.backends.utils import create_file_data
from deepagents.middleware.subagents import SubAgent
from google.genai.errors import ClientError
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool, tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import GoogleRateLimitError
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from jarvis.core.capabilities.manifest import CapabilityManifest
from jarvis.core.config import get_settings
from jarvis.core.exceptions import (
    SandboxBackendUnavailableError,
)
from jarvis.core.policy.decision import (
    AutonomyLevel,
    PolicyDecision,
    PolicyDecisionType,
)
from jarvis.core.policy.engine import PolicyEngine
from jarvis.deep_agents.backend import (
    JarvisSandboxBackend,
    normalize_and_validate_sandbox_path,
)
from jarvis.deep_agents.compatibility import (
    verify_deepagents_compatibility,
)
from jarvis.deep_agents.factory import (
    create_governed_deep_agent,
    resolve_canonical_gemini_model,
)
from jarvis.deep_agents.governor import JarvisSubagentGovernor
from jarvis.deep_agents.models import (
    SubagentPolicyContract,
    SubagentPrivilegeEscalationError,
)
from jarvis.deep_agents.projection import (
    JarvisToolGovernanceMiddleware,
    extract_tool_name,
)
from jarvis.deep_agents.selection import (
    SelectedModelMetadata,
    discover_model_candidates,
    resolve_governed_agent_model,
)
from jarvis.sandbox.models import (
    NetworkProfile,
    SandboxExecutionResult,
    SandboxState,
)
from jarvis.sandbox.provider import DockerSandboxProvider, TestDoubleSandboxProvider


class ScriptedChatModel(BaseChatModel):
    """Deterministic scripted model capturing tool bindings and returning scripted messages."""

    messages_to_return: list[AIMessage] = Field(default_factory=list)
    bound_tools_history: list[list[str]] = Field(default_factory=list)

    def __init__(self, messages: list[AIMessage] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.messages_to_return = list(messages or [])
        self.bound_tools_history = []

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.messages_to_return:
            msg = self.messages_to_return.pop(0)
        else:
            msg = AIMessage(content="Workflow complete.")
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, Any]:
        tool_names = [extract_tool_name(t) for t in tools]
        self.bound_tools_history.append(tool_names)
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted_audit_model"


class MockAuditPolicyEngine(PolicyEngine):
    """Configurable PolicyEngine for black-box compiled agent audits."""

    def __init__(self, default_decision: PolicyDecisionType = PolicyDecisionType.ALLOW) -> None:
        super().__init__()
        self.default_decision = default_decision
        self.decision_overrides: dict[str, PolicyDecisionType] = {}
        self.evaluated_invocations: list[dict[str, Any]] = []

    def set_override(self, tool_name: str, decision: PolicyDecisionType) -> None:
        self.decision_overrides[tool_name] = decision

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
        self.evaluated_invocations.append(
            {
                "task_id": task_id,
                "tool_id": manifest.capability_id,
                "arguments": arguments,
                "autonomy_level": autonomy_level,
            }
        )
        decision = self.decision_overrides.get(manifest.capability_id, self.default_decision)
        return PolicyDecision(
            decision=decision,
            reason=f"Policy verdict: {decision.value}",
            risk_score=0.9 if decision == PolicyDecisionType.DENY else 0.1,
        )


class TestCompiledAgentSecurityBoundary:
    """Comprehensive Black-Box Security Suite for Compiled Deep Agents graphs."""

    def test_compiled_model_visible_tool_surface_capture(self) -> None:
        """Requirement 4: Verify unauthorized tools are absent from compiled model-visible schema."""
        provider = TestDoubleSandboxProvider()
        backend = JarvisSandboxBackend(provider=provider, allow_test_doubles=True)
        model = ScriptedChatModel()

        # Compile agent with read-only scopes (AUTO_READ_ONLY)
        handle = create_governed_deep_agent(
            model=model,
            backend=backend,
            task_scopes=frozenset({"sandbox:read"}),
            autonomy_level=AutonomyLevel.AUTO_READ_ONLY,
            allow_test_doubles=True,
        )

        handle.invoke({"messages": [HumanMessage(content="inspect workspace")]})

        # Inspect the tool sets that were bound to the model during execution
        assert len(model.bound_tools_history) > 0
        active_tools = model.bound_tools_history[-1]

        # Read-only tools must be present
        assert "read_file" in active_tools
        assert "ls" in active_tools

        # Mutating and execution tools must be strictly absent
        assert "write_file" not in active_tools
        assert "edit_file" not in active_tools
        assert "delete" not in active_tools
        assert "execute" not in active_tools
        assert "task" not in active_tools

    def test_filesystem_deny_rules_survive_middleware_composition(self) -> None:
        """Requirement 5: Verify filesystem deny rules survive middleware assembly and block calls."""
        provider = TestDoubleSandboxProvider()
        sandbox = JarvisSandboxBackend(provider=provider, allow_test_doubles=True)
        backend = CompositeBackend(default=StateBackend(), routes={"/workspace": sandbox})

        perm_deny = FilesystemPermission(
            paths=["/protected/**"],
            operations=["read", "write"],
            mode="deny",
        )

        scripted_model = ScriptedChatModel(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"file_path": "/protected/secret.txt"},
                            "id": "call_deny_1",
                        }
                    ],
                )
            ]
        )

        handle = create_governed_deep_agent(
            model=scripted_model,
            backend=backend,
            task_scopes=frozenset({"sandbox:read", "sandbox:write"}),
            autonomy_level=AutonomyLevel.AUTO_BOUNDED_MUTATION,
            allow_test_doubles=True,
            permissions=[perm_deny],
        )

        result = handle.invoke({"messages": [HumanMessage(content="read protected secret")]})
        messages = result["messages"]

        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        assert len(tool_messages) == 1
        assert "permission denied" in tool_messages[0].content
        assert tool_messages[0].status == "error"

    def test_filesystem_interrupt_survives_middleware_composition(self) -> None:
        """Requirement 6: Verify interrupt / HITL mode pauses the compiled runnable."""
        provider = TestDoubleSandboxProvider()
        sandbox = JarvisSandboxBackend(provider=provider, allow_test_doubles=True)
        backend = CompositeBackend(default=StateBackend(), routes={"/workspace": sandbox})

        perm_interrupt = FilesystemPermission(
            paths=["/sensitive/**"],
            operations=["write"],
            mode="interrupt",
        )

        scripted_model = ScriptedChatModel(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {
                                "file_path": "/sensitive/config.env",
                                "content": "KEY=VALUE",
                            },
                            "id": "call_hitl_1",
                        }
                    ],
                )
            ]
        )

        checkpointer = MemorySaver()
        # Compile agent with memory checkpointer to support graph interruption
        agent_graph = create_deep_agent(
            model=scripted_model,
            backend=backend,
            permissions=[perm_interrupt],
            middleware=[JarvisToolGovernanceMiddleware(authorized_tools=frozenset({"write_file"}))],
            checkpointer=checkpointer,
        )

        config: RunnableConfig = {"configurable": {"thread_id": "audit_hitl_thread_1"}}
        agent_graph.invoke(
            {"messages": [HumanMessage(content="write sensitive config")]}, config=config
        )

        # Inspect graph state: the HumanInTheLoopMiddleware must have entered interrupted state
        state = agent_graph.get_state(config)
        assert len(state.tasks) > 0
        interrupts = state.tasks[0].interrupts
        assert len(interrupts) > 0

    def test_filesystem_allow_list_boundary_enforcement(self) -> None:
        """Requirement 7: Verify operations inside allow-list succeed while operations outside are rejected."""
        state_backend = StateBackend()
        init_files = {
            "/workspace/data.txt": create_file_data("allowed payload"),
            "/outside/forbidden.txt": create_file_data("forbidden payload"),
        }

        perm_allow = [
            FilesystemPermission(
                paths=["/workspace/**"],
                operations=["read"],
                mode="allow",
            ),
            FilesystemPermission(
                paths=["/**"],
                operations=["read"],
                mode="deny",
            ),
        ]

        # Test inside allow-list: succeeds
        model_inside = ScriptedChatModel(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"file_path": "/workspace/data.txt"},
                            "id": "call_inside",
                        }
                    ],
                )
            ]
        )
        agent_inside = create_deep_agent(
            model=model_inside,
            backend=state_backend,
            permissions=perm_allow,
            middleware=[JarvisToolGovernanceMiddleware(authorized_tools=frozenset({"read_file"}))],
        )
        input_inside: dict[str, Any] = {
            "messages": [HumanMessage(content="read inside")],
            "files": init_files,
        }
        res_inside = cast("Any", agent_inside).invoke(input_inside)
        tool_msgs_inside = [m for m in res_inside["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs_inside) == 1
        assert "allowed payload" in tool_msgs_inside[0].content
        assert tool_msgs_inside[0].status == "success"

        # Test outside allow-list: denied
        model_outside = ScriptedChatModel(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"file_path": "/outside/forbidden.txt"},
                            "id": "call_outside",
                        }
                    ],
                )
            ]
        )
        agent_outside = create_deep_agent(
            model=model_outside,
            backend=state_backend,
            permissions=perm_allow,
            middleware=[JarvisToolGovernanceMiddleware(authorized_tools=frozenset({"read_file"}))],
        )
        input_outside: dict[str, Any] = {
            "messages": [HumanMessage(content="read outside")],
            "files": init_files,
        }
        res_outside = cast("Any", agent_outside).invoke(input_outside)
        tool_msgs_outside = [m for m in res_outside["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs_outside) == 1
        assert "permission denied" in tool_msgs_outside[0].content
        assert tool_msgs_outside[0].status == "error"

    def test_jarvis_policy_engine_and_composition_monotonicity(self) -> None:
        """Requirement 8: AND-composition between Deep Agents permissions and JARVIS Policy Engine."""
        provider = TestDoubleSandboxProvider()
        sandbox = JarvisSandboxBackend(provider=provider, allow_test_doubles=True)
        backend = CompositeBackend(default=StateBackend(), routes={"/workspace": sandbox})

        # Deep Agents allows /workspace/**, but JARVIS Policy DENIES read_file
        policy_engine = MockAuditPolicyEngine(default_decision=PolicyDecisionType.ALLOW)
        policy_engine.set_override("deepagent:read_file", PolicyDecisionType.DENY)

        perm_allow = FilesystemPermission(
            paths=["/workspace/**"],
            operations=["read"],
            mode="allow",
        )

        model = ScriptedChatModel(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"file_path": "/workspace/doc.txt"},
                            "id": "call_and_1",
                        }
                    ],
                )
            ]
        )

        handle = create_governed_deep_agent(
            model=model,
            backend=backend,
            policy_engine=policy_engine,
            task_scopes=frozenset({"sandbox:read"}),
            allow_test_doubles=True,
            permissions=[perm_allow],
        )

        result = handle.invoke({"messages": [HumanMessage(content="read document")]})
        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert "denied by policy" in tool_msgs[0].content

        # Conversely, if JARVIS Policy allows but Deep Agents denies:
        policy_engine_allow = MockAuditPolicyEngine(default_decision=PolicyDecisionType.ALLOW)
        perm_deny = FilesystemPermission(
            paths=["/workspace/restricted/**"],
            operations=["read"],
            mode="deny",
        )

        model_rev = ScriptedChatModel(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"file_path": "/workspace/restricted/doc.txt"},
                            "id": "call_and_2",
                        }
                    ],
                )
            ]
        )

        handle_rev = create_governed_deep_agent(
            model=model_rev,
            backend=backend,
            policy_engine=policy_engine_allow,
            task_scopes=frozenset({"sandbox:read"}),
            allow_test_doubles=True,
            permissions=[perm_deny],
        )

        result_rev = handle_rev.invoke({"messages": [HumanMessage(content="read restricted")]})
        tool_msgs_rev = [m for m in result_rev["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs_rev) == 1
        assert "permission denied" in tool_msgs_rev[0].content

    def test_direct_backend_bypass_is_governed(self) -> None:
        """Requirement 9: Direct backend adapter operations still evaluate JARVIS policy."""
        provider = TestDoubleSandboxProvider()
        policy_engine = MockAuditPolicyEngine(default_decision=PolicyDecisionType.DENY)

        backend = JarvisSandboxBackend(
            provider=provider,
            policy_engine=policy_engine,
            allow_test_doubles=True,
        )

        # Attempt direct execution bypass
        res = backend.execute("ls -la")
        assert res.exit_code == 126
        assert "Execution blocked by Policy Engine" in res.output
        assert "Policy verdict: DENY" in res.output

    def test_execute_tool_governance_and_protocol_dependency(self) -> None:
        """Requirement 10: Execute tool depends on SandboxBackendProtocol and obeys policy."""
        # 1. When backend does not implement SandboxBackendProtocol, execute returns error
        state_backend = StateBackend()
        model = ScriptedChatModel(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "execute", "args": {"command": "whoami"}, "id": "call_ex_1"}
                    ],
                )
            ]
        )
        agent = create_deep_agent(
            model=model,
            backend=state_backend,
            middleware=[JarvisToolGovernanceMiddleware(authorized_tools=frozenset({"execute"}))],
        )
        res = agent.invoke({"messages": [HumanMessage(content="run whoami")]})
        tool_msgs = [m for m in res["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert "does not support command execution" in tool_msgs[0].content

        # 2. When backend implements SandboxBackendProtocol and Policy Engine allows:
        provider = TestDoubleSandboxProvider()
        sandbox_backend = JarvisSandboxBackend(provider=provider, allow_test_doubles=True)
        model_sbx = ScriptedChatModel(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "execute", "args": {"command": "echo test"}, "id": "call_ex_2"}
                    ],
                )
            ]
        )
        handle = create_governed_deep_agent(
            model=model_sbx,
            backend=sandbox_backend,
            task_scopes=frozenset({"sandbox:execute"}),
            allow_test_doubles=True,
        )
        res_sbx = handle.invoke({"messages": [HumanMessage(content="run echo")]})
        tool_msgs_sbx = [m for m in res_sbx["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs_sbx) == 1
        assert tool_msgs_sbx[0].status == "success"

    def test_docker_unavailable_fails_closed_without_tier0_fallback(self) -> None:
        """Requirement 11: When Docker daemon is offline, execution fails closed without host fallback."""
        docker_provider = DockerSandboxProvider()
        backend = JarvisSandboxBackend(provider=docker_provider, allow_test_doubles=False)

        with pytest.raises(SandboxBackendUnavailableError) as exc_info:
            create_governed_deep_agent(
                model=ScriptedChatModel(),
                backend=backend,
                allow_test_doubles=False,
            )
        assert "Docker container daemon is offline" in str(exc_info.value)

    def test_subagent_monotonic_attenuation_all_cases(self) -> None:
        """Requirement 12: Comprehensive subagent attenuation audit (Cases A-E)."""
        provider = TestDoubleSandboxProvider()
        backend = JarvisSandboxBackend(provider=provider, allow_test_doubles=True)

        parent_contract = SubagentPolicyContract(
            name="parent_auditor",
            authorized_tools=frozenset({"read", "read_file", "ls", "task"}),
            authorized_scopes=frozenset({"sandbox:read", "agent:subagent:delegate"}),
            max_autonomy_level=AutonomyLevel.AUTO_READ_ONLY,
        )

        parent_perms = [
            FilesystemPermission(paths=["/workspace/**"], operations=["read"], mode="allow"),
            FilesystemPermission(paths=["/secrets/**"], operations=["read"], mode="deny"),
        ]

        governor = JarvisSubagentGovernor(
            parent_contract=parent_contract,
            parent_backend=backend,
            parent_permissions=parent_perms,
        )

        # Case A: Child omits permissions -> inherits parent permissions
        spec_a: SubAgent = {
            "name": "child_a",
            "description": "Child A",
            "tools": [backend.read],
        }
        governed_a = governor.validate_and_govern_subagent(spec_a)
        assert governed_a.get("permissions") is not None
        assert len(governed_a["permissions"]) == 2

        # Case B: Child attempts to escalate a denied path
        spec_b: SubAgent = {
            "name": "child_b",
            "description": "Child B",
            "tools": [backend.read],
            "permissions": [
                FilesystemPermission(paths=["/secrets/**"], operations=["read"], mode="allow")
            ],
        }
        with pytest.raises(SubagentPrivilegeEscalationError) as exc_b:
            governor.validate_and_govern_subagent(spec_b)
        assert "parent denies" in str(exc_b.value)

        # Case C: Narrower child permissions allowed
        spec_c: SubAgent = {
            "name": "child_c",
            "description": "Child C",
            "tools": [backend.read],
            "permissions": [
                FilesystemPermission(
                    paths=["/workspace/project/**"], operations=["read"], mode="allow"
                )
            ],
        }
        governed_c = governor.validate_and_govern_subagent(spec_c)
        assert governed_c.get("permissions") is not None
        # Must retain parent deny rule as well
        child_c_modes = [p.mode for p in governed_c["permissions"]]
        assert "deny" in child_c_modes

        # Case D: Tool escalation: Parent lacks execute, child requests execute
        @tool
        def custom_execute(cmd: str) -> str:
            """Execute command."""
            return f"ran {cmd}"

        spec_d: SubAgent = {
            "name": "child_d",
            "description": "Child D",
            "tools": [custom_execute],
        }
        with pytest.raises(SubagentPrivilegeEscalationError) as exc_d:
            governor.validate_and_govern_subagent(spec_d)
        assert "privilege escalation" in str(exc_d.value)

        # Case E: Autonomy escalation: Parent has AUTO_READ_ONLY (Level 2), child declares write tool
        @tool
        def write_file(path: str, data: str) -> str:
            """Write file tool."""
            return f"wrote {path}"

        parent_contract_l2 = SubagentPolicyContract(
            name="parent_l2",
            authorized_tools=frozenset({"read_file", "write_file", "task"}),
            authorized_scopes=frozenset(
                {"sandbox:read", "sandbox:write", "agent:subagent:delegate"}
            ),
            max_autonomy_level=AutonomyLevel.AUTO_READ_ONLY,  # Level 2 restricts mutating execution
        )
        governor_l2 = JarvisSubagentGovernor(
            parent_contract=parent_contract_l2,
            parent_backend=backend,
        )
        spec_e: SubAgent = {
            "name": "child_e",
            "description": "Child E",
            "tools": [write_file],
        }
        with pytest.raises(SubagentPrivilegeEscalationError) as exc_e:
            governor_l2.validate_and_govern_subagent(spec_e)
        assert "read-only autonomy level" in str(exc_e.value)

    def test_fork_mode_is_prohibited_by_governor(self) -> None:
        """Requirement 13: Fork mode is rejected to prevent private state and secret leakage."""
        provider = TestDoubleSandboxProvider()
        backend = JarvisSandboxBackend(provider=provider, allow_test_doubles=True)

        parent_contract = SubagentPolicyContract(
            name="root",
            authorized_tools=frozenset({"read_file", "task"}),
            authorized_scopes=frozenset({"sandbox:read", "agent:subagent:delegate"}),
        )
        governor = JarvisSubagentGovernor(parent_contract=parent_contract, parent_backend=backend)

        fork_spec: SubAgent = {
            "name": "forked_worker",
            "description": "Worker in fork mode",
            "mode": "fork",
        }

        with pytest.raises(SubagentPrivilegeEscalationError) as exc_info:
            governor.validate_and_govern_subagent(fork_spec)
        assert "mode='fork' which is prohibited" in str(exc_info.value)

    def test_upstream_deepagents_compatibility_guard(self) -> None:
        """Requirement 16: Verify runtime compatibility guard asserts expected _permissions channel."""
        compat_info = verify_deepagents_compatibility()
        assert compat_info["compatible"] is True
        assert compat_info["major"] == 0
        assert compat_info["minor"] == 7
        assert compat_info["has_permissions_param"] is True

    def test_output_integrity_and_exit_code_preservation(self) -> None:
        """Requirement 17: Verify non-zero exit codes are preserved as actual execution evidence."""
        provider = TestDoubleSandboxProvider()
        now = datetime.now(UTC)

        async def mock_execute(*args: Any, **kwargs: Any) -> SandboxExecutionResult:
            return SandboxExecutionResult(
                sandbox_id=uuid4(),
                task_id=uuid4(),
                exit_code=42,
                stdout="Compilation failed with syntax error.",
                stderr="fatal error: unterminated string literal",
                duration_seconds=0.5,
                state=SandboxState.READY,
                backend_type=provider.backend_type,
                isolation_tier=provider.supported_tier,
                provider_category=provider.provider_category,
                network_profile=NetworkProfile.NONE,
                started_at=now,
                finished_at=now,
            )

        provider.execute = mock_execute  # type: ignore[method-assign]

        backend = JarvisSandboxBackend(provider=provider, allow_test_doubles=True)
        response = backend.execute("cargo build")

        assert response.exit_code == 42
        assert "Compilation failed" in response.output
        assert "fatal error" in response.output
        assert not response.truncated

    def test_path_semantics_and_traversal_defenses(self) -> None:
        """Requirement 18: Path normalization and traversal defenses."""
        assert normalize_and_validate_sandbox_path("foo/bar.txt") == "foo/bar.txt"
        assert normalize_and_validate_sandbox_path("/workspace/app.py") == "app.py"
        assert normalize_and_validate_sandbox_path("app///main.py") == "app/main.py"
        assert normalize_and_validate_sandbox_path("..") is None
        assert normalize_and_validate_sandbox_path("../etc/passwd") is None
        assert normalize_and_validate_sandbox_path("foo/../../etc/passwd") is None
        assert normalize_and_validate_sandbox_path("%2e%2e/secret") is None
        assert normalize_and_validate_sandbox_path("") is None
        assert normalize_and_validate_sandbox_path("/") is None
        assert normalize_and_validate_sandbox_path("foo\x00bar") is None

    def test_secret_canary_isolation(self) -> None:
        """Requirement 19: Control plane secrets do not leak into sandbox or subagent state."""
        synthetic_canary = "CANARY_SECRET_TOKEN_XYZ_98765"
        os.environ["AUDIT_TEST_CANARY_SECRET"] = synthetic_canary

        try:
            provider = TestDoubleSandboxProvider()
            backend = JarvisSandboxBackend(provider=provider, allow_test_doubles=True)

            model = ScriptedChatModel()
            handle = create_governed_deep_agent(
                model=model,
                backend=backend,
                allow_test_doubles=True,
            )

            result = handle.invoke({"messages": [HumanMessage(content="run clean check")]})
            # Verify canary is nowhere in returned messages
            for msg in result["messages"]:
                assert synthetic_canary not in str(msg.content)

            # Verify canary is not leaked into profile or provider state
            assert synthetic_canary not in str(backend._profile.model_dump())
            assert synthetic_canary not in str(getattr(backend._provider, "state", {}))
        finally:
            os.environ.pop("AUDIT_TEST_CANARY_SECRET", None)

    def test_observability_audit_telemetry(self) -> None:
        """Requirement 20: Tool executions generate structured audit logs with required metadata."""
        policy_engine = MockAuditPolicyEngine()
        provider = TestDoubleSandboxProvider()
        task_id = uuid4()

        backend = JarvisSandboxBackend(
            provider=provider,
            policy_engine=policy_engine,
            task_id=task_id,
            allow_test_doubles=True,
        )

        model = ScriptedChatModel(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "execute", "args": {"command": "hostname"}, "id": "call_obs_1"}
                    ],
                )
            ]
        )

        handle = create_governed_deep_agent(
            model=model,
            backend=backend,
            task_id=task_id,
            policy_engine=policy_engine,
            allow_test_doubles=True,
        )

        handle.invoke({"messages": [HumanMessage(content="run hostname")]})

        assert len(policy_engine.evaluated_invocations) > 0
        inv = policy_engine.evaluated_invocations[0]
        assert inv["task_id"] == task_id
        assert inv["tool_id"] in ("deepagent:execute", "sandbox:execute")

    def test_configured_gemini_model_is_explicitly_injected(self) -> None:
        """Requirement: Configured model is explicitly injected; model=None is never passed to create_deep_agent."""
        settings = get_settings()
        if not settings.GEMINI_API_KEY:
            pytest.skip(
                "Gemini API key is not configured in settings; skipping model resolution test."
            )

        resolved = resolve_canonical_gemini_model(model=None)
        assert isinstance(resolved, ChatGoogleGenerativeAI)
        assert resolved.model in (
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-flash-latest",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemma-4-31b-it",
        )
        assert "google" in resolved._llm_type.lower()

    def test_model_selection_consults_existing_model_roster(self) -> None:
        """Requirement: Model selection mechanism consults existing JARVIS roster without hardcoding."""
        candidates = discover_model_candidates()
        assert len(candidates) >= 2
        # Ensure candidates originate from known roster pools
        model_ids = [c[1] for c in candidates]
        assert "gemini-3.5-flash-lite" in model_ids or "gemini-3.1-flash-lite" in model_ids
        # Ensure no universal hardcoding of a single model as the sole choice
        assert any("flash-lite" in m for m in model_ids)

    def test_model_selection_captures_provider_identity_and_tool_capability(self) -> None:
        """Requirement: Selection captures provider identity, verifies tool capability, and records source."""
        settings = get_settings()
        if not settings.GEMINI_API_KEY:
            pytest.skip("Credentials unavailable for selection metadata test.")

        metadata = resolve_governed_agent_model(settings=settings)
        assert isinstance(metadata, SelectedModelMetadata)
        assert metadata.provider == "Google Gemini"
        assert metadata.supports_tools is True
        assert len(metadata.model_id) > 0
        assert "existing JARVIS" in metadata.source
        assert hasattr(metadata.chat_model, "bind_tools")

    def test_unavailable_candidate_is_not_blindly_invoked(self) -> None:
        """Requirement: Candidate without configured credentials raises SandboxBackendUnavailableError (fails closed)."""
        from jarvis.core.config import Settings

        dummy_settings = Settings(
            GEMINI_API_KEY=None,
            GROQ_API_KEY=None,
        )
        old_gemini = os.environ.pop("GEMINI_API_KEY", None)
        old_google = os.environ.pop("GOOGLE_API_KEY", None)
        try:
            with pytest.raises(SandboxBackendUnavailableError) as exc_info:
                resolve_governed_agent_model(settings=dummy_settings)
            assert "No valid model provider credentials" in str(exc_info.value)
        finally:
            if old_gemini:
                os.environ["GEMINI_API_KEY"] = old_gemini
            if old_google:
                os.environ["GOOGLE_API_KEY"] = old_google

    def test_anthropic_and_openai_key_absence_does_not_matter(self) -> None:
        """Requirement: Anthropic and OpenAI keys are irrelevant to JARVIS; absence causes zero errors."""
        old_anthropic = os.environ.pop("ANTHROPIC_API_KEY", None)
        old_openai = os.environ.pop("OPENAI_API_KEY", None)
        try:
            provider = TestDoubleSandboxProvider()
            backend = JarvisSandboxBackend(provider=provider, allow_test_doubles=True)
            scripted_model = ScriptedChatModel()

            # Works cleanly without any Anthropic or OpenAI credentials
            handle = create_governed_deep_agent(
                model=scripted_model,
                backend=backend,
                allow_test_doubles=True,
            )
            assert handle is not None
            assert handle.name == "governed_deep_agent"
        finally:
            if old_anthropic:
                os.environ["ANTHROPIC_API_KEY"] = old_anthropic
            if old_openai:
                os.environ["OPENAI_API_KEY"] = old_openai

    def test_existing_gemini_configuration_is_canonical_source(self) -> None:
        """Requirement: Canonical settings are reused without duplicating loading or requiring separate DEEP_AGENTS keys."""
        settings = get_settings()
        assert settings is not None
        assert hasattr(settings, "GEMINI_API_KEY")
        # Ensure no custom separate keys are demanded
        assert "DEEP_AGENTS_GOOGLE_API_KEY" not in os.environ
        assert "DEEP_AGENTS_MODEL" not in os.environ

    def test_real_gemini_smoke_test_and_tool_governance(self) -> None:
        """Requirement 5: Exactly ONE bounded real-model test with actual Gemini API and JARVIS governance."""
        settings = get_settings()
        if not settings.GEMINI_API_KEY:
            pytest.skip(
                "Gemini API key is genuinely unavailable in settings/.env; real-model smoke test skipped."
            )

        provider = TestDoubleSandboxProvider()
        policy_engine = MockAuditPolicyEngine(default_decision=PolicyDecisionType.ALLOW)
        task_id = uuid4()
        backend = JarvisSandboxBackend(
            provider=provider,
            policy_engine=policy_engine,
            task_id=task_id,
            allow_test_doubles=True,
        )

        # Explicitly construct Gemini chat model via project canonical path
        gemini_model = resolve_canonical_gemini_model()
        assert isinstance(gemini_model, ChatGoogleGenerativeAI)
        assert "google" in gemini_model._llm_type.lower()

        handle = create_governed_deep_agent(
            model=gemini_model,
            backend=backend,
            task_id=task_id,
            policy_engine=policy_engine,
            allow_test_doubles=True,
        )

        prompt = "Create a file named /workspace/hello.txt containing 'hello world'. Read it back and tell me what it contains."
        try:
            result = handle.invoke({"messages": [HumanMessage(content=prompt)]})
        except Exception as exc:
            err_msg = str(exc).lower()
            if any(
                term in err_msg
                for term in (
                    "resource_exhausted",
                    "429",
                    "quota",
                    "remoteprotocolerror",
                    "server disconnected",
                )
            ):
                pytest.skip(f"Gemini API quota or upstream connection exhausted: {exc}")
            raise

        # Assert real Gemini invoked tools through JARVIS
        messages = result["messages"]
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        assert len(tool_messages) >= 1
        # Assert policy engine intercepted invocations
        assert len(policy_engine.evaluated_invocations) >= 1
        # Assert provider is Google/Gemini
        assert "google" in gemini_model._llm_type.lower()
        # Assert final response received
        assert len(messages) >= 3

    def test_real_gemini_multi_turn_conversation(self) -> None:
        """Requirement 9: Bounded multi-turn conversational sequence verifying conversation state and tool execution."""
        settings = get_settings()
        if not settings.GEMINI_API_KEY:
            pytest.skip(
                "Gemini API key is genuinely unavailable in settings/.env; real-model multi-turn test skipped."
            )

        provider = TestDoubleSandboxProvider()
        policy_engine = MockAuditPolicyEngine(default_decision=PolicyDecisionType.ALLOW)
        task_id = uuid4()
        backend = JarvisSandboxBackend(
            provider=provider,
            policy_engine=policy_engine,
            task_id=task_id,
            allow_test_doubles=True,
        )

        gemini_model = resolve_canonical_gemini_model()
        checkpointer = MemorySaver()

        handle = create_governed_deep_agent(
            model=gemini_model,
            backend=backend,
            task_id=task_id,
            policy_engine=policy_engine,
            allow_test_doubles=True,
            checkpointer=checkpointer,
        )

        config: RunnableConfig = {"configurable": {"thread_id": f"gemini_conv_{uuid4()}"}}

        try:
            # Turn 1: Create file
            turn1 = handle.invoke(
                {
                    "messages": [
                        HumanMessage(content="Create hello.txt in /workspace containing 'hello'.")
                    ]
                },
                config=config,
            )
            assert any(
                isinstance(m, ToolMessage) and m.name == "write_file" for m in turn1["messages"]
            )

            # Turn 2: Read file back
            turn2 = handle.invoke(
                {"messages": [HumanMessage(content="Read it back and tell me what it contains.")]},
                config=config,
            )
            assert any(
                isinstance(m, ToolMessage) and m.name == "read_file" for m in turn2["messages"]
            )

            # Turn 3: Summarize what was done
            turn3 = handle.invoke(
                {
                    "messages": [
                        HumanMessage(content="Now summarize what you did in one brief sentence.")
                    ]
                },
                config=config,
            )
            last_msg = turn3["messages"][-1]
            assert isinstance(last_msg, AIMessage)
            assert len(str(last_msg.content)) > 0
        except (GoogleRateLimitError, ClientError) as exc:
            if "RESOURCE_EXHAUSTED" in str(exc) or "429" in str(exc):
                pytest.skip(f"Gemini API quota exhausted (429 RESOURCE_EXHAUSTED): {exc}")
            raise
