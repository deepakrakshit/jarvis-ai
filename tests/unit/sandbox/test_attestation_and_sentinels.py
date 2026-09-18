"""Unit tests for Sandbox Profile Attestation and Sentinel Isolation Suite.

Verifies:
1. Docker provider health report returns structured evidence (backend, server_version, runtime).
2. Health report distinguishes CLI availability from daemon availability (no false HEALTHY).
3. Test double attestation strictly returns NOT_VERIFIED (cannot fake kernel isolation).
4. Attestation engine detects root user, Docker socket exposure, or secret leaks as FAILED.
5. Attestation engine certifies fully conforming container as VERIFIED.
6. SentinelIsolationVerifier exercises the 9 canonical containment probes.
7. Test double sentinel run strictly produces real_isolation_verified=False.
8. Real provider sentinel run fails closed when Docker daemon is offline.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from jarvis.sandbox.detector import get_system_capabilities
from jarvis.sandbox.manager import SandboxLifecycleManager
from jarvis.sandbox.models import (
    AttestationStatus,
    BackendType,
    CapabilityStatus,
    DockerBackendType,
    IsolationTier,
    NetworkProfile,
    ProviderCategory,
    ResourceLimits,
    SandboxExecutionResult,
    SandboxIdentity,
    SandboxProfile,
    SandboxState,
    SentinelProbeId,
    SentinelProbeVerdict,
)
from jarvis.sandbox.provider import DockerSandboxProvider, TestDoubleSandboxProvider
from jarvis.sandbox.registry import SandboxProviderRegistry
from jarvis.sandbox.sentinels import SentinelIsolationVerifier


@pytest.mark.asyncio
async def test_health_report_structured_evidence() -> None:
    """Verify get_health_report populates structured evidence and distinguishes CLI from daemon."""
    caps = get_system_capabilities()
    provider = DockerSandboxProvider()
    report = await provider.get_health_report(run_smoke_test=True)

    assert report.backend_type == BackendType.DOCKER
    assert "docker_cli" in report.evidence
    assert "docker_daemon" in report.evidence
    assert "docker_backend" in report.evidence
    assert report.docker_backend in (
        DockerBackendType.WSL2,
        DockerBackendType.HYPER_V,
        DockerBackendType.DOCKER_VMM,
        DockerBackendType.NATIVE_LINUX,
        DockerBackendType.UNKNOWN,
        DockerBackendType.UNAVAILABLE,
    )

    if caps.docker_daemon_available != CapabilityStatus.SUPPORTED:
        assert report.is_available is False
        assert report.is_healthy is False
        assert report.capacity_readiness == "UNAVAILABLE"
        assert report.smoke_test_passed is False


@pytest.mark.asyncio
async def test_test_double_attestation_strictly_not_verified() -> None:
    """Verify TestDoubleSandboxProvider.attest strictly returns NOT_VERIFIED."""
    provider = TestDoubleSandboxProvider()
    profile = SandboxProfile(profile_id="test-double-attest")
    identity = await provider.create(task_id=uuid4(), profile=profile)

    attestation = await provider.attest(identity.sandbox_id)
    assert attestation.status == AttestationStatus.NOT_VERIFIED
    assert attestation.provider_category == ProviderCategory.TEST_DOUBLE
    assert (
        "Test double provider does not execute workloads on a real kernel isolation boundary"
        in (attestation.unsatisfied_constraints[0])
    )
    assert attestation.evidence.get("is_test_double") is True


@pytest.mark.asyncio
async def test_docker_attestation_security_failures(tmp_path: Path) -> None:
    """Verify attestation catches root user, Docker socket exposure, or secret leaks."""
    provider = DockerSandboxProvider()
    sandbox_id = uuid4()
    profile = SandboxProfile(profile_id="insecure-inspect-profile")

    # Manually register mock internal sandbox
    now = datetime.now(UTC)
    from jarvis.sandbox.models import SandboxIdentity

    identity = SandboxIdentity(
        sandbox_id=sandbox_id,
        task_id=uuid4(),
        profile_id=profile.profile_id,
        backend_type=BackendType.DOCKER,
        isolation_tier=IsolationTier.TIER_1_CONTAINER,
        provider_category=ProviderCategory.REAL_ISOLATION_PROVIDER,
        created_at=now,
        expires_at=now,
    )
    provider._sandboxes[sandbox_id] = {
        "identity": identity,
        "profile": profile,
        "state": SandboxState.READY,
        "staging_dir": tmp_path,
        "container_name": f"jarvis-test-{sandbox_id.hex[:8]}",
    }

    # Case 1: Docker inspect returns root user + exposed docker socket + secret in env
    fake_inspect = [
        {
            "Config": {
                "User": "root",
                "Env": ["PATH=/usr/bin", "GEMINI_API_KEY=dummy_test_credential_value"],
            },
            "HostConfig": {
                "ReadonlyRootfs": True,
                "SecurityOpt": ["no-new-privileges:true"],
                "CapDrop": ["ALL"],
                "NetworkMode": "none",
                "PidsLimit": 100,
                "Memory": 512 * 1024 * 1024,
                "NanoCpus": 1000000000,
                "PidMode": "",
            },
            "Mounts": [{"Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock"}],
        }
    ]

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (json.dumps(fake_inspect).encode("utf-8"), b"")

    with (
        patch("jarvis.sandbox.provider.DockerSandboxProvider._verify_engine_available"),
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
    ):
        attestation = await provider.attest(sandbox_id)

    assert attestation.status == AttestationStatus.FAILED
    assert attestation.privilege_isolation == CapabilityStatus.UNSUPPORTED
    assert attestation.docker_socket_access == CapabilityStatus.UNSUPPORTED
    assert attestation.credential_exposure == CapabilityStatus.UNSUPPORTED
    assert any("root user" in u for u in attestation.unsatisfied_constraints)
    assert any("Docker socket mounted" in u for u in attestation.unsatisfied_constraints)
    assert any("Credential exposed" in u for u in attestation.unsatisfied_constraints)


@pytest.mark.asyncio
async def test_docker_attestation_verified_conforming_container(tmp_path: Path) -> None:
    """Verify attestation awards VERIFIED when container matches all security constraints."""
    provider = DockerSandboxProvider()
    sandbox_id = uuid4()
    profile = SandboxProfile(
        profile_id="fully-hardened-profile",
        run_as_user="1000:1000",
        read_only_rootfs=True,
        no_new_privileges=True,
        drop_capabilities=("ALL",),
        network_profile=NetworkProfile.NONE,
        resource_limits=ResourceLimits(pids_limit=50, memory_limit_mb=256, cpu_limit=1.0),
    )

    now = datetime.now(UTC)
    from jarvis.sandbox.models import SandboxIdentity

    identity = SandboxIdentity(
        sandbox_id=sandbox_id,
        task_id=uuid4(),
        profile_id=profile.profile_id,
        backend_type=BackendType.DOCKER,
        isolation_tier=IsolationTier.TIER_1_CONTAINER,
        provider_category=ProviderCategory.REAL_ISOLATION_PROVIDER,
        created_at=now,
        expires_at=now,
    )
    provider._sandboxes[sandbox_id] = {
        "identity": identity,
        "profile": profile,
        "state": SandboxState.READY,
        "staging_dir": tmp_path,
        "container_name": f"jarvis-test-{sandbox_id.hex[:8]}",
    }

    # Conforming inspect output
    fake_inspect = [
        {
            "Config": {
                "User": "1000:1000",
                "Env": ["PATH=/usr/local/bin:/usr/bin:/bin"],
            },
            "HostConfig": {
                "ReadonlyRootfs": True,
                "SecurityOpt": ["no-new-privileges:true"],
                "CapDrop": ["ALL"],
                "NetworkMode": "none",
                "PidsLimit": 50,
                "Memory": 256 * 1024 * 1024,
                "NanoCpus": 1000000000,
                "PidMode": "",
            },
            "Mounts": [{"Source": str(tmp_path), "Destination": "/workspace"}],
        }
    ]

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (json.dumps(fake_inspect).encode("utf-8"), b"")

    with (
        patch("jarvis.sandbox.provider.DockerSandboxProvider._verify_engine_available"),
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
    ):
        attestation = await provider.attest(sandbox_id)

    assert attestation.status == AttestationStatus.VERIFIED
    assert attestation.privilege_isolation == CapabilityStatus.SUPPORTED
    assert attestation.filesystem_isolation == CapabilityStatus.SUPPORTED
    assert attestation.network_isolation == CapabilityStatus.SUPPORTED
    assert attestation.process_isolation == CapabilityStatus.SUPPORTED
    assert attestation.resource_controls == CapabilityStatus.SUPPORTED
    assert attestation.docker_socket_access == CapabilityStatus.SUPPORTED
    assert attestation.credential_exposure == CapabilityStatus.SUPPORTED
    assert len(attestation.unsatisfied_constraints) == 0


@pytest.mark.asyncio
async def test_sentinel_suite_test_double_execution() -> None:
    """Verify SentinelIsolationVerifier exercises all 12 probes on a test double."""
    provider = TestDoubleSandboxProvider()
    verifier = SentinelIsolationVerifier(provider)

    report = await verifier.run_suite(task_id=uuid4())

    # Canonical 12 probe identifiers
    assert SentinelProbeId.HOST_FILESYSTEM_BOUNDARY.value in report.probes
    assert SentinelProbeId.WORKSPACE_BOUNDARY.value in report.probes
    assert SentinelProbeId.DOCKER_SOCKET_BOUNDARY.value in report.probes
    assert SentinelProbeId.CREDENTIAL_BOUNDARY.value in report.probes
    assert SentinelProbeId.PROCESS_NAMESPACE_BOUNDARY.value in report.probes
    assert SentinelProbeId.CHILD_PROCESS_CONTAINMENT.value in report.probes
    assert SentinelProbeId.NETWORK_EGRESS_POLICY.value in report.probes
    assert SentinelProbeId.PRIVILEGE_BOUNDARY.value in report.probes
    assert SentinelProbeId.CAPABILITY_RESTRICTION.value in report.probes
    assert SentinelProbeId.READ_ONLY_ROOTFS.value in report.probes
    assert SentinelProbeId.RESOURCE_LIMITS.value in report.probes
    assert SentinelProbeId.LIFECYCLE_CLEANUP.value in report.probes

    # Backward compatibility aliases
    assert "PROBE_A_HOST_FILESYSTEM" in report.probes
    assert "PROBE_B_WORKSPACE_BOUNDARY" in report.probes
    assert "PROBE_C_DOCKER_SOCKET" in report.probes
    assert "PROBE_D_HOST_CREDENTIALS" in report.probes
    assert "PROBE_E_PROCESS_BOUNDARY" in report.probes
    assert "PROBE_F_CHILD_PROCESS" in report.probes
    assert "PROBE_G_NETWORK_NONE" in report.probes
    assert "PROBE_H_PRIVILEGE" in report.probes
    assert "PROBE_I_RESOURCE_LIMITS" in report.probes

    assert len(report.probes) == 21
    assert report.all_probes_passed is True
    # Critical invariant: test double NEVER awards real_isolation_verified
    assert report.real_isolation_verified is False
    assert report.provider_category == ProviderCategory.TEST_DOUBLE


@pytest.mark.asyncio
async def test_sentinel_suite_fail_closed_without_docker_daemon() -> None:
    """Verify SentinelIsolationVerifier fails closed when Docker daemon is offline."""
    caps = get_system_capabilities()
    if caps.docker_daemon_available != CapabilityStatus.SUPPORTED:
        provider = DockerSandboxProvider()
        verifier = SentinelIsolationVerifier(provider)

        report = await verifier.run_suite(task_id=uuid4())

        assert report.all_probes_passed is False
        assert report.real_isolation_verified is False
        assert any("Docker daemon is not operational" in d for d in report.diagnostics)


@pytest.mark.asyncio
async def test_lifecycle_manager_attest_sandbox_integration(tmp_path: Path) -> None:
    """Verify SandboxLifecycleManager.attest_sandbox integrates with registry and providers."""
    registry = SandboxProviderRegistry()
    manager = SandboxLifecycleManager(registry=registry, state_dir=tmp_path / "mgr_state")

    task_id = uuid4()
    profile = SandboxProfile(profile_id="lifecycle-attest-profile")
    identity = await manager.create_sandbox(
        task_id=task_id, profile=profile, allow_test_doubles=True
    )
    await manager.start_sandbox(identity.sandbox_id)

    attestation = await manager.attest_sandbox(identity.sandbox_id)
    assert attestation.sandbox_id == identity.sandbox_id
    assert attestation.status == AttestationStatus.NOT_VERIFIED  # Test double
    assert attestation.provider_category == ProviderCategory.TEST_DOUBLE

    await manager.terminate_sandbox(identity.sandbox_id)


@pytest.mark.asyncio
async def test_test_double_attestation_all_12_properties_not_applicable() -> None:
    """Verify TestDoubleSandboxProvider populates all 12 properties with NOT_APPLICABLE and NOT_VERIFIED."""
    provider = TestDoubleSandboxProvider()
    profile = SandboxProfile(profile_id="test-double-12-props")
    identity = await provider.create(task_id=uuid4(), profile=profile)

    attestation = await provider.attest(identity.sandbox_id)
    assert attestation.status == AttestationStatus.NOT_VERIFIED
    assert attestation.overall_state == AttestationStatus.NOT_VERIFIED
    assert attestation.host_filesystem_boundary == CapabilityStatus.NOT_APPLICABLE
    assert attestation.workspace_boundary == CapabilityStatus.NOT_APPLICABLE
    assert attestation.docker_socket_boundary == CapabilityStatus.NOT_APPLICABLE
    assert attestation.credential_boundary == CapabilityStatus.NOT_APPLICABLE
    assert attestation.process_namespace_boundary == CapabilityStatus.NOT_APPLICABLE
    assert attestation.child_process_containment == CapabilityStatus.NOT_APPLICABLE
    assert attestation.network_egress_policy == CapabilityStatus.NOT_APPLICABLE
    assert attestation.privilege_boundary == CapabilityStatus.NOT_APPLICABLE
    assert attestation.capability_restriction == CapabilityStatus.NOT_APPLICABLE
    assert attestation.read_only_rootfs == CapabilityStatus.NOT_APPLICABLE
    assert attestation.resource_limits == CapabilityStatus.NOT_APPLICABLE
    assert attestation.lifecycle_cleanup == CapabilityStatus.NOT_APPLICABLE


@pytest.mark.asyncio
async def test_docker_attestation_effective_configuration(tmp_path: Path) -> None:
    """Verify DockerSandboxProvider.attest creates EffectiveSecurityConfiguration with masked env."""
    provider = DockerSandboxProvider()
    sandbox_id = uuid4()
    profile = SandboxProfile(
        profile_id="effective-cfg-profile",
        run_as_user="1000:1000",
        read_only_rootfs=True,
        drop_capabilities=("ALL",),
        network_profile=NetworkProfile.NONE,
        resource_limits=ResourceLimits(pids_limit=100, memory_limit_mb=512, cpu_limit=2.0),
    )
    now = datetime.now(UTC)
    identity = SandboxIdentity(
        sandbox_id=sandbox_id,
        task_id=uuid4(),
        profile_id=profile.profile_id,
        backend_type=BackendType.DOCKER,
        isolation_tier=IsolationTier.TIER_1_CONTAINER,
        provider_category=ProviderCategory.REAL_ISOLATION_PROVIDER,
        created_at=now,
        expires_at=now,
    )
    provider._sandboxes[sandbox_id] = {
        "identity": identity,
        "profile": profile,
        "state": SandboxState.READY,
        "staging_dir": tmp_path,
        "container_name": f"jarvis-test-{sandbox_id.hex[:8]}",
    }
    fake_inspect = [
        {
            "Config": {
                "Image": "alpine:latest",
                "User": "1000:1000",
                "Env": ["PATH=/bin", "GEMINI_API_KEY=SUPER_SECRET_KEY_12345", "FOO=BAR"],
            },
            "HostConfig": {
                "Runtime": "runc",
                "ReadonlyRootfs": True,
                "SecurityOpt": ["no-new-privileges:true"],
                "CapDrop": ["ALL"],
                "CapAdd": [],
                "NetworkMode": "none",
                "PidsLimit": 100,
                "Memory": 512 * 1024 * 1024,
                "NanoCpus": 2000000000,
                "PidMode": "",
            },
            "Mounts": [{"Source": str(tmp_path), "Destination": "/workspace"}],
        }
    ]
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (json.dumps(fake_inspect).encode("utf-8"), b"")

    with (
        patch("jarvis.sandbox.provider.DockerSandboxProvider._verify_engine_available"),
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
    ):
        attestation = await provider.attest(sandbox_id)

    assert attestation.effective_configuration is not None
    eff = attestation.effective_configuration
    assert eff.image == "alpine:latest"
    assert eff.runtime == "runc"
    assert eff.network_mode == "none"
    assert eff.readonly_rootfs is True
    assert "ALL" in eff.drop_capabilities
    assert eff.user == "1000:1000"
    assert eff.pids_limit == 100
    assert eff.memory_limit_bytes == 512 * 1024 * 1024
    # Invariant: Secret value MUST NEVER appear in env_keys, only keys
    assert "GEMINI_API_KEY" in eff.env_keys
    assert "FOO" in eff.env_keys
    assert "SUPER_SECRET_KEY_12345" not in str(eff.env_keys)
    # Because GEMINI_API_KEY was exposed in container env, attestation status must be FAILED
    assert attestation.status == AttestationStatus.FAILED
    assert attestation.overall_state == AttestationStatus.FAILED
    assert attestation.credential_boundary == CapabilityStatus.UNSUPPORTED
    assert len(attestation.failures) > 0


@pytest.mark.asyncio
async def test_docker_absence_verification_on_termination(tmp_path: Path) -> None:
    """Verify DockerSandboxProvider.terminate enforces absence verification before TERMINATED."""
    provider = DockerSandboxProvider()
    sandbox_id = uuid4()
    profile = SandboxProfile(profile_id="terminate-profile")
    now = datetime.now(UTC)
    identity = SandboxIdentity(
        sandbox_id=sandbox_id,
        task_id=uuid4(),
        profile_id=profile.profile_id,
        backend_type=BackendType.DOCKER,
        isolation_tier=IsolationTier.TIER_1_CONTAINER,
        provider_category=ProviderCategory.REAL_ISOLATION_PROVIDER,
        created_at=now,
        expires_at=now,
    )
    provider._sandboxes[sandbox_id] = {
        "identity": identity,
        "profile": profile,
        "state": SandboxState.READY,
        "staging_dir": tmp_path,
        "container_name": f"jarvis-test-{sandbox_id.hex[:8]}",
    }

    # Case A: inspect returns non-zero with "no such container" -> absence verified -> TERMINATED
    rm_proc = AsyncMock()
    rm_proc.communicate.return_value = (b"", b"")

    insp_proc_absent = AsyncMock()
    insp_proc_absent.returncode = 1
    insp_proc_absent.communicate.return_value = (
        b"",
        b"Error: No such container: jarvis-test-123",
    )

    with patch("asyncio.create_subprocess_exec", side_effect=[rm_proc, insp_proc_absent]):
        state = await provider.terminate(sandbox_id)
        assert state == SandboxState.TERMINATED

    # Reset state to READY for Case B
    provider._sandboxes[sandbox_id]["state"] = SandboxState.READY

    # Case B: inspect returns 0 (container still exists) -> absence not verified -> UNKNOWN
    insp_proc_present = AsyncMock()
    insp_proc_present.returncode = 0
    insp_proc_present.communicate.return_value = (b"[{}]", b"")

    with patch("asyncio.create_subprocess_exec", side_effect=[rm_proc, insp_proc_present]):
        state = await provider.terminate(sandbox_id)
        assert state == SandboxState.UNKNOWN

    # Reset state to READY for Case C
    provider._sandboxes[sandbox_id]["state"] = SandboxState.READY

    # Case C: inspect throws exception -> UNKNOWN
    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=[rm_proc, RuntimeError("Daemon communication failure")],
    ):
        state = await provider.terminate(sandbox_id)
        assert state == SandboxState.UNKNOWN


@pytest.mark.asyncio
async def test_sentinel_probes_hostile_failure_conditions() -> None:
    """Verify hostile probe failures are correctly recorded as FAILED."""
    provider = TestDoubleSandboxProvider()
    verifier = SentinelIsolationVerifier(provider)
    sandbox_id = uuid4()
    task_id = uuid4()

    # 1. Host Filesystem: If exit code is 0 (file exists inside container), probe fails
    with patch.object(
        provider,
        "execute",
        return_value=SandboxExecutionResult(
            sandbox_id=sandbox_id,
            task_id=task_id,
            exit_code=0,
            stdout="File found!",
            stderr="",
            duration_seconds=0.01,
            timed_out=False,
            state=SandboxState.READY,
            backend_type=BackendType.DOCKER,
            isolation_tier=IsolationTier.TIER_1_CONTAINER,
            provider_category=ProviderCategory.REAL_ISOLATION_PROVIDER,
            network_profile=NetworkProfile.NONE,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        ),
    ):
        res_fs = await verifier._probe_host_filesystem(
            sandbox_id, task_id, Path("C:/Windows/System32/config")
        )
        assert res_fs.passed is False
        assert res_fs.status == CapabilityStatus.FAILED
        assert res_fs.actual_verdict == SentinelProbeVerdict.FAILED

    # 2. Docker Socket: If exit code is 0 (socket found), probe fails
    with patch.object(
        provider,
        "execute",
        return_value=SandboxExecutionResult(
            sandbox_id=sandbox_id,
            task_id=task_id,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.01,
            timed_out=False,
            state=SandboxState.READY,
            backend_type=BackendType.DOCKER,
            isolation_tier=IsolationTier.TIER_1_CONTAINER,
            provider_category=ProviderCategory.REAL_ISOLATION_PROVIDER,
            network_profile=NetworkProfile.NONE,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        ),
    ):
        res_sock = await verifier._probe_docker_socket(sandbox_id, task_id)
        assert res_sock.passed is False
        assert res_sock.status == CapabilityStatus.FAILED

    # 3. Host Credentials: If exit code is 0 (secret found), probe fails
    with patch.object(
        provider,
        "execute",
        return_value=SandboxExecutionResult(
            sandbox_id=sandbox_id,
            task_id=task_id,
            exit_code=0,
            stdout="SECRET_LEAK",
            stderr="",
            duration_seconds=0.01,
            timed_out=False,
            state=SandboxState.READY,
            backend_type=BackendType.DOCKER,
            isolation_tier=IsolationTier.TIER_1_CONTAINER,
            provider_category=ProviderCategory.REAL_ISOLATION_PROVIDER,
            network_profile=NetworkProfile.NONE,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        ),
    ):
        res_cred = await verifier._probe_host_credentials(sandbox_id, task_id, Path("/root/.ssh"))
        assert res_cred.passed is False
        assert res_cred.status == CapabilityStatus.FAILED

    # 4. Network Egress: If exit code is 1 (network connection succeeded), probe fails
    with patch.object(
        provider,
        "execute",
        return_value=SandboxExecutionResult(
            sandbox_id=sandbox_id,
            task_id=task_id,
            exit_code=1,
            stdout="",
            stderr="Connected to 1.1.1.1",
            duration_seconds=0.01,
            timed_out=False,
            state=SandboxState.READY,
            backend_type=BackendType.DOCKER,
            isolation_tier=IsolationTier.TIER_1_CONTAINER,
            provider_category=ProviderCategory.REAL_ISOLATION_PROVIDER,
            network_profile=NetworkProfile.NONE,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        ),
    ):
        res_net = await verifier._probe_network_egress(sandbox_id, task_id)
        assert res_net.passed is False
        assert res_net.status == CapabilityStatus.FAILED

    # 5. Privilege Boundary: If exit code is 1 (running as root or setuid succeeded), probe fails
    with patch.object(
        provider,
        "execute",
        return_value=SandboxExecutionResult(
            sandbox_id=sandbox_id,
            task_id=task_id,
            exit_code=1,
            stdout="",
            stderr="Root privileges active",
            duration_seconds=0.01,
            timed_out=False,
            state=SandboxState.READY,
            backend_type=BackendType.DOCKER,
            isolation_tier=IsolationTier.TIER_1_CONTAINER,
            provider_category=ProviderCategory.REAL_ISOLATION_PROVIDER,
            network_profile=NetworkProfile.NONE,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        ),
    ):
        res_priv = await verifier._probe_privilege_boundary(sandbox_id, task_id)
        assert res_priv.passed is False
        assert res_priv.status == CapabilityStatus.FAILED

    # 6. Capability Restriction: If exit code is 1 (CapEff != 0), probe fails
    with patch.object(
        provider,
        "execute",
        return_value=SandboxExecutionResult(
            sandbox_id=sandbox_id,
            task_id=task_id,
            exit_code=1,
            stdout="",
            stderr="CapEff non-zero",
            duration_seconds=0.01,
            timed_out=False,
            state=SandboxState.READY,
            backend_type=BackendType.DOCKER,
            isolation_tier=IsolationTier.TIER_1_CONTAINER,
            provider_category=ProviderCategory.REAL_ISOLATION_PROVIDER,
            network_profile=NetworkProfile.NONE,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        ),
    ):
        res_caps = await verifier._probe_capability_restriction(sandbox_id, task_id)
        assert res_caps.passed is False
        assert res_caps.status == CapabilityStatus.FAILED

    # 7. Read-Only Rootfs: If exit code is 1 (/etc writable), probe fails
    with patch.object(
        provider,
        "execute",
        return_value=SandboxExecutionResult(
            sandbox_id=sandbox_id,
            task_id=task_id,
            exit_code=1,
            stdout="",
            stderr="Write to /etc succeeded",
            duration_seconds=0.01,
            timed_out=False,
            state=SandboxState.READY,
            backend_type=BackendType.DOCKER,
            isolation_tier=IsolationTier.TIER_1_CONTAINER,
            provider_category=ProviderCategory.REAL_ISOLATION_PROVIDER,
            network_profile=NetworkProfile.NONE,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        ),
    ):
        res_ro = await verifier._probe_read_only_rootfs(sandbox_id, task_id)
        assert res_ro.passed is False
        assert res_ro.status == CapabilityStatus.FAILED

    # 8. Lifecycle cleanup: If absence cannot be confirmed, probe fails
    with (
        patch.object(provider, "terminate", return_value=SandboxState.UNKNOWN),
        patch.object(provider, "cleanup", return_value=None),
        patch.object(provider, "inspect", return_value=SandboxState.UNKNOWN),
    ):
        res_cleanup = await verifier._probe_lifecycle_cleanup(sandbox_id)
        assert res_cleanup.passed is False
        assert res_cleanup.status == CapabilityStatus.FAILED


@pytest.mark.asyncio
async def test_live_docker_real_isolation_integration_guarded() -> None:
    """Live integration test guarded by daemon availability."""
    caps = get_system_capabilities()
    if caps.docker_daemon_available != CapabilityStatus.SUPPORTED:
        pytest.skip("REAL_RUNTIME_TESTS = SKIPPED: Docker daemon unavailable on host")

    provider = DockerSandboxProvider()
    verifier = SentinelIsolationVerifier(provider)
    report = await verifier.run_suite(task_id=uuid4())
    assert report.real_isolation_verified is True
