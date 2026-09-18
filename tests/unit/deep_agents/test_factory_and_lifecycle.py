"""Unit tests for Deep Agents Factory and Lifecycle Orchestration.

Validates:
1. Zero-Trust Sandbox Binding: Requires an explicit JarvisSandboxBackend instance.
2. Fail-Closed Boundary: Rejects test double backends when allow_test_doubles is False.
3. Fail-Closed Invariant: Inactive Docker daemon blocks live container execution.
4. Compiled Agent Graph Execution: Governed execution runs with tool projection and tracking.
5. Clean Termination & Absence Verification: Terminates container and verifies destruction.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from deepagents.middleware.subagents import SubAgent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tools import tool

from jarvis.core.exceptions import SandboxBackendUnavailableError
from jarvis.core.policy.decision import AutonomyLevel
from jarvis.deep_agents.backend import JarvisSandboxBackend
from jarvis.deep_agents.factory import (
    GovernedDeepAgentHandle,
    create_governed_deep_agent,
)
from jarvis.sandbox.manager import SandboxLifecycleManager
from jarvis.sandbox.models import (
    IsolationTier,
    SandboxProfile,
    SandboxState,
)
from jarvis.sandbox.provider import DockerSandboxProvider, TestDoubleSandboxProvider


@tool
def helper_read(path: str) -> str:
    """Read helper file."""
    return f"content of {path}"


class FakeToolModel(FakeListChatModel):
    """Fake model implementing bind_tools for function calling agent graphs."""

    def bind_tools(self, tools: object, **kwargs: object) -> FakeToolModel:
        return self


@pytest.fixture
def test_double_backend() -> JarvisSandboxBackend:
    """Fixture returning a test double JarvisSandboxBackend."""
    provider = TestDoubleSandboxProvider(supported_tier=IsolationTier.TIER_1_CONTAINER)
    return JarvisSandboxBackend(
        provider=provider,
        allow_test_doubles=True,
    )


def test_factory_requires_explicit_backend() -> None:
    """Validate that omitting a backend fails closed immediately."""
    with pytest.raises(SandboxBackendUnavailableError) as exc_info:
        create_governed_deep_agent(
            model=FakeListChatModel(responses=["hi"]),
            backend=None,
        )
    assert "requires an explicit JarvisSandboxBackend" in str(exc_info.value)


def test_factory_fails_closed_when_test_double_prohibited() -> None:
    """Validate that test double backend is rejected if allow_test_doubles is False."""
    provider = TestDoubleSandboxProvider()
    backend = JarvisSandboxBackend(
        provider=provider,
        allow_test_doubles=True,
    )

    with pytest.raises(SandboxBackendUnavailableError) as exc_info:
        create_governed_deep_agent(
            model=FakeListChatModel(responses=["hi"]),
            backend=backend,
            allow_test_doubles=False,
        )
    assert "Test double sandbox provider is prohibited" in str(exc_info.value)


def test_factory_fails_closed_on_inactive_docker_daemon() -> None:
    """Validate that Docker provider fails closed when daemon is offline."""
    docker_provider = DockerSandboxProvider()
    backend = JarvisSandboxBackend(
        provider=docker_provider,
        allow_test_doubles=False,
    )

    with pytest.raises(SandboxBackendUnavailableError) as exc_info:
        create_governed_deep_agent(
            model=FakeListChatModel(responses=["hi"]),
            backend=backend,
            allow_test_doubles=False,
        )
    assert "Docker container daemon is offline" in str(exc_info.value)


def test_factory_compilation_and_synchronous_invocation(
    test_double_backend: JarvisSandboxBackend,
) -> None:
    """Validate successful compilation and synchronous invocation with fake model."""
    fake_model = FakeToolModel(responses=["Task completed successfully."])

    handle = create_governed_deep_agent(
        model=fake_model,
        backend=test_double_backend,
        task_scopes=frozenset({"sandbox:read", "sandbox:write", "sandbox:execute"}),
        autonomy_level=AutonomyLevel.AUTO_BOUNDED_MUTATION,
        name="test_worker",
        allow_test_doubles=True,
    )

    assert isinstance(handle, GovernedDeepAgentHandle)
    assert handle.name == "test_worker"
    assert handle.backend is test_double_backend

    result = handle.invoke({"messages": [{"role": "user", "content": "Execute check"}]})
    assert isinstance(result, dict)
    assert "messages" in result
    assert result["messages"][-1].content == "Task completed successfully."


@pytest.mark.asyncio
async def test_factory_compilation_and_async_invocation(
    test_double_backend: JarvisSandboxBackend,
) -> None:
    """Validate successful compilation and asynchronous invocation."""
    fake_model = FakeToolModel(responses=["Async result ready."])

    handle = create_governed_deep_agent(
        model=fake_model,
        backend=test_double_backend,
        task_scopes=frozenset({"sandbox:read"}),
        autonomy_level=AutonomyLevel.AUTO_READ_ONLY,
        name="async_worker",
        allow_test_doubles=True,
    )

    result = await handle.ainvoke({"messages": [{"role": "user", "content": "Fetch data"}]})
    assert isinstance(result, dict)
    assert "messages" in result
    assert result["messages"][-1].content == "Async result ready."


def test_factory_compilation_with_governed_subagents(
    test_double_backend: JarvisSandboxBackend,
) -> None:
    """Validate compilation with governed subagents."""
    fake_model = FakeToolModel(responses=["Coordinated with subagent."])

    subagent_spec: SubAgent = {
        "name": "reading_subagent",
        "description": "Performs scoped reading tasks",
        "tools": [helper_read],
    }

    handle = create_governed_deep_agent(
        model=fake_model,
        backend=test_double_backend,
        task_scopes=frozenset({"sandbox:read", "agent:subagent:delegate"}),
        extra_tools=[helper_read],
        subagents=[subagent_spec],
        allow_test_doubles=True,
    )

    assert isinstance(handle, GovernedDeepAgentHandle)
    # The agent compiled successfully with governed subagents


@pytest.mark.asyncio
async def test_governed_agent_termination_with_lifecycle_manager(
    test_double_backend: JarvisSandboxBackend,
) -> None:
    """Validate that terminate() delegates to lifecycle manager and verifies container absence."""
    mock_mgr = MagicMock(spec=SandboxLifecycleManager)
    mock_mgr.terminate_sandbox = AsyncMock(return_value=SandboxState.TERMINATED)

    handle = GovernedDeepAgentHandle(
        graph=MagicMock(),
        backend=test_double_backend,
        lifecycle_manager=mock_mgr,
        name="monitored_agent",
    )

    term_state = await handle.terminate(reason="workflow_finished")
    assert term_state == SandboxState.TERMINATED
    mock_mgr.terminate_sandbox.assert_awaited_once_with(
        test_double_backend.sandbox_id, reason="workflow_finished"
    )


@pytest.mark.asyncio
async def test_governed_agent_direct_provider_termination() -> None:
    """Validate direct provider cleanup and absence verification when lifecycle manager is None."""
    provider = TestDoubleSandboxProvider()
    profile = SandboxProfile(profile_id="test_direct_term")
    identity = await provider.create(task_id=uuid4(), profile=profile)
    await provider.start(identity.sandbox_id)

    backend = JarvisSandboxBackend(
        provider=provider,
        sandbox_id=identity.sandbox_id,
        allow_test_doubles=True,
    )

    handle = GovernedDeepAgentHandle(
        graph=MagicMock(),
        backend=backend,
        lifecycle_manager=None,
        name="direct_agent",
    )

    term_state = await handle.terminate(reason="manual_stop")
    assert term_state == SandboxState.TERMINATED
    # Verify container is absent from provider
    inspected = await provider.inspect(identity.sandbox_id)
    assert inspected == SandboxState.TERMINATED
