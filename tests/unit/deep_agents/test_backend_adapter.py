"""Unit tests for JarvisSandboxBackend adapter.

Validates:
1. BaseSandbox and SandboxBackendProtocol compliance.
2. Zero-Trust policy engine evaluation and action broker integration.
3. Fail-closed host isolation invariants (never falls back to host execution).
4. Scope checking and path traversal defenses.
5. Timeout handling and output truncation protection.
6. Empirical attestation invariant: test double strictly yields real_isolation_verified = False.
"""

from __future__ import annotations

import pytest
from deepagents.backends.sandbox import BaseSandbox

from jarvis.core.capabilities.manifest import CapabilityManifest
from jarvis.core.exceptions import (
    SandboxBackendUnavailableError,
)
from jarvis.core.policy.decision import (
    AutonomyLevel,
    PolicyDecision,
    PolicyDecisionType,
)
from jarvis.core.policy.engine import PolicyEngine
from jarvis.deep_agents.backend import JarvisSandboxBackend
from jarvis.deep_agents.models import (
    HumanApprovalRequiredError,
    UnauthorizedToolInvocationError,
)
from jarvis.sandbox.models import (
    BackendType,
    IsolationTier,
    ProviderCategory,
)
from jarvis.sandbox.provider import DockerSandboxProvider, TestDoubleSandboxProvider


class MockPolicyEngine(PolicyEngine):
    """Mock PolicyEngine to simulate specific authorization verdicts."""

    def __init__(self, decision: PolicyDecisionType = PolicyDecisionType.ALLOW) -> None:
        super().__init__()
        self.mock_decision = decision
        self.last_evaluated_manifest: CapabilityManifest | None = None

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
        self.last_evaluated_manifest = manifest
        return PolicyDecision(
            decision=self.mock_decision,
            reason=f"Mock decision: {self.mock_decision.value}",
            risk_score=0.1 if self.mock_decision == PolicyDecisionType.ALLOW else 0.9,
        )


@pytest.fixture
def test_double_provider() -> TestDoubleSandboxProvider:
    """Fixture returning an initialized test double provider."""
    return TestDoubleSandboxProvider(supported_tier=IsolationTier.TIER_1_CONTAINER)


@pytest.mark.asyncio
async def test_backend_contract_and_properties(
    test_double_provider: TestDoubleSandboxProvider,
) -> None:
    """Validate that JarvisSandboxBackend adheres to BaseSandbox interface and properties."""
    backend = JarvisSandboxBackend(
        provider=test_double_provider,
        allow_test_doubles=True,
    )

    assert isinstance(backend, BaseSandbox)
    assert backend.id == str(backend.sandbox_id)
    assert backend.provider_category == ProviderCategory.TEST_DOUBLE
    assert backend.backend_type == BackendType.TEST_DOUBLE
    # Invariant: Test double strictly yields real_isolation_verified = False
    assert backend.real_isolation_verified is False


def test_backend_fail_closed_when_test_double_prohibited(
    test_double_provider: TestDoubleSandboxProvider,
) -> None:
    """Validate that using a test double fails closed if allow_test_doubles is False."""
    with pytest.raises(SandboxBackendUnavailableError) as exc_info:
        JarvisSandboxBackend(
            provider=test_double_provider,
            allow_test_doubles=False,
        )
    assert "Cannot bind test double provider" in str(exc_info.value)


@pytest.mark.asyncio
async def test_backend_fail_closed_on_inactive_docker_daemon() -> None:
    """Validate that Docker provider fails closed when daemon is offline, prohibiting host fallback."""
    docker_provider = DockerSandboxProvider()
    # On this host, Docker daemon is offline
    backend = JarvisSandboxBackend(
        provider=docker_provider,
        allow_test_doubles=False,
    )

    with pytest.raises(SandboxBackendUnavailableError) as exc_info:
        await backend.aexecute("echo 'should not run'")
    assert "Docker container daemon is offline" in str(exc_info.value)


@pytest.mark.asyncio
async def test_backend_command_execution_async_and_sync(
    test_double_provider: TestDoubleSandboxProvider,
) -> None:
    """Validate asynchronous and synchronous command execution mapping."""
    backend = JarvisSandboxBackend(
        provider=test_double_provider,
        allow_test_doubles=True,
    )

    # Async execution
    res_async = await backend.aexecute("python3 script.py --check")
    assert res_async.exit_code == 0
    assert not res_async.truncated
    assert "script.py --check" in res_async.output

    # Sync execution
    res_sync = backend.execute("ls -la /app")
    assert res_sync.exit_code == 0
    assert not res_sync.truncated
    assert "ls -la /app" in res_sync.output


@pytest.mark.asyncio
async def test_backend_timeout_handling(
    test_double_provider: TestDoubleSandboxProvider,
) -> None:
    """Validate that command timeout is cleanly reported as exit code 124."""
    timeout_provider = TestDoubleSandboxProvider(simulate_timeout=True)
    backend = JarvisSandboxBackend(
        provider=timeout_provider,
        allow_test_doubles=True,
    )

    res = await backend.aexecute("sleep 100", timeout=2)
    assert res.exit_code == 124
    assert "timed out after 2.0 seconds" in res.output


@pytest.mark.asyncio
async def test_backend_output_truncation_protection(
    test_double_provider: TestDoubleSandboxProvider,
) -> None:
    """Validate output length cap and truncation warning."""
    backend = JarvisSandboxBackend(
        provider=test_double_provider,
        allow_test_doubles=True,
        max_output_bytes=25,
    )

    res = await backend.aexecute("generate_large_output")
    assert res.truncated is True
    assert len(res.output) > 25
    assert "...[OUTPUT TRUNCATED DUE TO SIZE LIMIT]" in res.output


@pytest.mark.asyncio
async def test_backend_scope_checking(
    test_double_provider: TestDoubleSandboxProvider,
) -> None:
    """Validate that missing scopes reject execute, upload, and download."""
    # 1. Missing sandbox:execute
    no_exec_backend = JarvisSandboxBackend(
        provider=test_double_provider,
        task_scopes=frozenset({"sandbox:read"}),
        allow_test_doubles=True,
    )
    with pytest.raises(UnauthorizedToolInvocationError) as exc_info:
        await no_exec_backend.aexecute("echo 'blocked'")
    assert "sandbox:execute" in str(exc_info.value)

    # 2. Missing sandbox:write on upload
    no_write_backend = JarvisSandboxBackend(
        provider=test_double_provider,
        task_scopes=frozenset({"sandbox:read"}),
        allow_test_doubles=True,
    )
    upload_res = await no_write_backend.aupload_files([("file.txt", b"data")])
    assert len(upload_res) == 1
    assert upload_res[0].error == "permission_denied"

    # 3. Missing sandbox:read on download
    no_read_backend = JarvisSandboxBackend(
        provider=test_double_provider,
        task_scopes=frozenset({"sandbox:write"}),
        allow_test_doubles=True,
    )
    download_res = await no_read_backend.adownload_files(["file.txt"])
    assert len(download_res) == 1
    assert download_res[0].error == "permission_denied"


@pytest.mark.asyncio
async def test_backend_path_traversal_protection(
    test_double_provider: TestDoubleSandboxProvider,
) -> None:
    """Validate directory traversal defense on file transfers."""
    backend = JarvisSandboxBackend(
        provider=test_double_provider,
        task_scopes=frozenset({"sandbox:read", "sandbox:write"}),
        allow_test_doubles=True,
    )

    # Upload path traversal
    upload_res = await backend.aupload_files([("../etc/evil.conf", b"malicious")])
    assert upload_res[0].error == "permission_denied"

    # Download path traversal
    download_res = await backend.adownload_files(["../../etc/shadow"])
    assert download_res[0].error == "permission_denied"


@pytest.mark.asyncio
async def test_backend_file_upload_and_download_flow(
    test_double_provider: TestDoubleSandboxProvider,
) -> None:
    """Validate successful file staging and retrieval."""
    backend = JarvisSandboxBackend(
        provider=test_double_provider,
        task_scopes=frozenset({"sandbox:read", "sandbox:write"}),
        allow_test_doubles=True,
    )

    # Upload
    content = b"configuration payload data"
    up_res = await backend.aupload_files([("config/settings.json", content)])
    assert len(up_res) == 1
    assert up_res[0].error is None

    # Download
    dl_res = await backend.adownload_files(["config/settings.json"])
    assert len(dl_res) == 1
    assert dl_res[0].error is None
    assert dl_res[0].content == content


@pytest.mark.asyncio
async def test_backend_policy_engine_enforcement(
    test_double_provider: TestDoubleSandboxProvider,
) -> None:
    """Validate PolicyEngine integration for HITL pause and policy denial."""
    # 1. HITL Required
    hitl_policy = MockPolicyEngine(decision=PolicyDecisionType.REQUIRE_HITL)
    hitl_backend = JarvisSandboxBackend(
        provider=test_double_provider,
        policy_engine=hitl_policy,
        allow_test_doubles=True,
    )
    with pytest.raises(HumanApprovalRequiredError) as hitl_exc:
        await hitl_backend.aexecute("rm -rf /")
    assert "requires human-in-the-loop approval" in str(hitl_exc.value)

    # 2. Deny Verdict
    deny_policy = MockPolicyEngine(decision=PolicyDecisionType.DENY)
    deny_backend = JarvisSandboxBackend(
        provider=test_double_provider,
        policy_engine=deny_policy,
        allow_test_doubles=True,
    )
    res = await deny_backend.aexecute("drop_table")
    assert res.exit_code == 126
    assert "Execution blocked by Policy Engine" in res.output
