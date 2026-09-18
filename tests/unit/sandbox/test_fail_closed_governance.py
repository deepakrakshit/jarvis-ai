"""Unit tests for Sandbox Fail-Closed Governance and Isolation Contracts.

Verifies:
1. Capability detection reports accurate host state.
2. Mandatory fail-closed enforcement when container engines are unavailable.
3. Test double categorization prevents confusion with real isolation.
4. Security constraints on SandboxProfile (zero Docker socket, zero credentials).
5. Path traversal protection during artifact upload and download.
6. Hardened Docker argument construction matches canonical security specifications.
"""

from datetime import UTC
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.core.exceptions import (
    SandboxBackendUnavailableError,
    SandboxSecurityViolationError,
)
from jarvis.sandbox.detector import (
    CapabilityDetector,
    evaluate_profile_satisfaction,
    get_system_capabilities,
)
from jarvis.sandbox.models import (
    CapabilityStatus,
    DockerBackendType,
    IsolationTier,
    NetworkProfile,
    ProviderCategory,
    ResourceLimits,
    SandboxProfile,
    SandboxState,
    SystemCapabilities,
)
from jarvis.sandbox.provider import DockerSandboxProvider, TestDoubleSandboxProvider
from jarvis.sandbox.registry import SandboxProviderRegistry


def test_capability_detector_host_discovery() -> None:
    """Verify CapabilityDetector discovers real host environment and supported tiers."""
    detector = CapabilityDetector()
    caps = detector.detect()

    assert caps.host_os in ("windows", "linux", "darwin")
    assert caps.host_arch != ""
    assert IsolationTier.TIER_0_LOCAL in caps.supported_tiers
    # On this development machine, Docker daemon is not running
    if caps.docker_daemon_available != CapabilityStatus.SUPPORTED:
        assert IsolationTier.TIER_1_CONTAINER not in caps.supported_tiers
        assert len(caps.diagnostics) > 0


def test_sandbox_profile_security_invariants() -> None:
    """Verify SandboxProfile strictly forbids Docker socket and credential exposure."""
    # 1. Default hardened profile
    profile = SandboxProfile(profile_id="default-isolated")
    assert profile.read_only_rootfs is True
    assert profile.no_new_privileges is True
    assert "ALL" in profile.drop_capabilities
    assert profile.network_profile == NetworkProfile.NONE
    assert profile.allow_docker_socket is False
    assert profile.allow_credential_injection is False

    # 2. Attempting to enable Docker socket must be rejected
    with pytest.raises(ValueError, match="Docker socket exposure is strictly prohibited"):
        SandboxProfile(profile_id="insecure-socket", allow_docker_socket=True)

    # 3. Attempting to inject credentials must be rejected
    with pytest.raises(
        ValueError, match="Credential injection into sandbox is strictly prohibited"
    ):
        SandboxProfile(profile_id="insecure-creds", allow_credential_injection=True)


def test_registry_fail_closed_enforcement() -> None:
    """Verify registry strictly fails closed when Tier-1 isolation is unavailable."""
    registry = SandboxProviderRegistry()
    caps = get_system_capabilities()

    if caps.docker_daemon_available != CapabilityStatus.SUPPORTED:
        # Default production resolution MUST fail closed
        with pytest.raises(SandboxBackendUnavailableError) as exc_info:
            registry.resolve_provider(tier=IsolationTier.TIER_1_CONTAINER, allow_test_doubles=False)
        assert "Fail-closed invariant enforced" in str(exc_info.value)
        assert "Automatic unisolated host fallback is strictly prohibited" in str(exc_info.value)

        # Test double resolution is permitted ONLY when explicitly requested
        provider = registry.resolve_provider(
            tier=IsolationTier.TIER_1_CONTAINER, allow_test_doubles=True
        )
        assert provider.provider_category == ProviderCategory.TEST_DOUBLE

    # Requesting TIER_0_LOCAL as a sandbox provider must be rejected
    with pytest.raises(SandboxBackendUnavailableError):
        registry.resolve_provider(tier=IsolationTier.TIER_0_LOCAL)


@pytest.mark.asyncio
async def test_docker_provider_fail_closed_without_daemon() -> None:
    """Verify DockerSandboxProvider fails closed at create() if daemon is offline."""
    caps = get_system_capabilities()
    if caps.docker_daemon_available != CapabilityStatus.SUPPORTED:
        provider = DockerSandboxProvider()
        profile = SandboxProfile(profile_id="test-tier1")
        with pytest.raises(SandboxBackendUnavailableError) as exc_info:
            await provider.create(task_id=uuid4(), profile=profile)
        assert "Fail-closed invariant triggered" in str(exc_info.value)


def test_docker_run_args_hardening_construction() -> None:
    """Verify Docker run arguments strictly contain all mandatory security flags."""
    provider = DockerSandboxProvider()
    sandbox_id = uuid4()
    profile = SandboxProfile(
        profile_id="hardened-test-profile",
        run_as_user="1000:1000",
        read_only_rootfs=True,
        no_new_privileges=True,
        drop_capabilities=("ALL",),
        seccomp_profile="default",
        network_profile=NetworkProfile.NONE,
        resource_limits=ResourceLimits(
            cpu_limit=1.5,
            memory_limit_mb=256,
            pids_limit=50,
        ),
    )

    # Set up sandbox internal record
    from tempfile import mkdtemp

    tmp_dir = Path(mkdtemp())
    try:
        from datetime import datetime

        from jarvis.sandbox.models import BackendType, SandboxIdentity

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
            "state": SandboxState.CREATED,
            "staging_dir": tmp_dir,
            "container_name": f"jarvis-sbx-{sandbox_id.hex[:12]}",
        }

        args = provider._build_docker_run_args(sandbox_id)

        # Assert mandatory security flags
        assert "--security-opt" in args
        assert "no-new-privileges=true" in args
        assert "--cap-drop" in args
        assert "ALL" in args
        assert "--user" in args
        assert "1000:1000" in args
        assert "--read-only" in args
        assert "--tmpfs" in args
        assert "/tmp:rw,noexec,nosuid,size=64m" in args
        assert "--pids-limit" in args
        assert "50" in args
        assert "--memory" in args
        assert "256m" in args
        assert "--cpus" in args
        assert "1.5" in args
        assert "--network" in args
        assert "none" in args
        assert "/var/run/docker.sock" not in " ".join(args)
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_artifact_staging_path_traversal_defense(tmp_path: Path) -> None:
    """Verify artifact transfer strictly rejects directory traversal attacks."""
    provider = TestDoubleSandboxProvider()
    profile = SandboxProfile(profile_id="staging-test")
    identity = await provider.create(task_id=uuid4(), profile=profile)

    # Create a legitimate host file
    host_file = tmp_path / "valid_script.py"
    host_file.write_text("print('hello')", encoding="utf-8")

    # 1. Valid upload
    transfers = await provider.upload_artifacts(
        sandbox_id=identity.sandbox_id,
        task_id=identity.task_id,
        artifacts=[(host_file, "scripts/test.py")],
    )
    assert len(transfers) == 1
    assert transfers[0].sandbox_path == "scripts/test.py"
    assert transfers[0].sha256_digest != ""

    # 2. Path traversal attack during upload (must be blocked)
    with pytest.raises(SandboxSecurityViolationError, match="Path traversal detected"):
        await provider.upload_artifacts(
            sandbox_id=identity.sandbox_id,
            task_id=identity.task_id,
            artifacts=[(host_file, "../../../etc/shadow")],
        )

    # 3. Path traversal attack during download (must be blocked)
    with pytest.raises(SandboxSecurityViolationError, match="Path traversal detected"):
        await provider.download_artifacts(
            sandbox_id=identity.sandbox_id,
            task_id=identity.task_id,
            artifacts=[("../../../escaped.txt", tmp_path / "escaped.txt")],
        )

    await provider.cleanup(identity.sandbox_id)


@pytest.mark.asyncio
async def test_test_double_lifecycle_and_execution() -> None:
    """Verify TestDoubleSandboxProvider provides observable lifecycle transitions."""
    provider = TestDoubleSandboxProvider()
    profile = SandboxProfile(profile_id="sim-profile")
    identity = await provider.create(task_id=uuid4(), profile=profile)

    assert await provider.inspect(identity.sandbox_id) == SandboxState.CREATED

    # Start
    state = await provider.start(identity.sandbox_id)
    assert state == SandboxState.READY
    assert await provider.health_check(identity.sandbox_id) is True

    # Execute
    res = await provider.execute(
        sandbox_id=identity.sandbox_id,
        command=["python", "test.py"],
        task_id=identity.task_id,
        capability_id="sandbox:code:execute",
    )
    assert res.exit_code == 0
    assert res.provider_category == ProviderCategory.TEST_DOUBLE
    assert "TEST_DOUBLE_SIMULATED_OUTPUT" in res.stdout

    # Terminate and cleanup
    term_state = await provider.terminate(identity.sandbox_id)
    assert term_state == SandboxState.TERMINATED
    await provider.cleanup(identity.sandbox_id)
    assert await provider.inspect(identity.sandbox_id) == SandboxState.TERMINATED


def test_capability_truth_cgroup_and_seccomp_missing() -> None:
    """Verify missing cgroup or seccomp controllers fail closed even if Docker is active."""
    profile = SandboxProfile(
        profile_id="strict-cgroup-profile",
        isolation_tier=IsolationTier.TIER_1_CONTAINER,
        resource_limits=ResourceLimits(memory_limit_mb=512, pids_limit=50, cpu_limit=1.0),
        seccomp_profile="default",
    )

    # Backend reports Docker daemon online, but missing memory and seccomp controllers
    caps = SystemCapabilities(
        host_os="linux",
        host_arch="x86_64",
        docker_cli_available=CapabilityStatus.SUPPORTED,
        docker_daemon_available=CapabilityStatus.SUPPORTED,
        docker_backend=DockerBackendType.NATIVE_LINUX,
        memory_limit_supported=CapabilityStatus.UNSUPPORTED,
        pids_limit_supported=CapabilityStatus.SUPPORTED,
        cpu_limit_supported=CapabilityStatus.SUPPORTED,
        seccomp_supported=CapabilityStatus.UNSUPPORTED,
        read_only_rootfs_supported=CapabilityStatus.SUPPORTED,
        network_none_supported=CapabilityStatus.SUPPORTED,
        supported_tiers=(IsolationTier.TIER_0_LOCAL, IsolationTier.TIER_1_CONTAINER),
    )

    eval_result = evaluate_profile_satisfaction(profile, caps)
    assert eval_result.is_satisfied is False
    assert any(
        "Memory quota enforcement is UNSUPPORTED" in r for r in eval_result.unsatisfied_reasons
    )
    assert any(
        "Seccomp syscall filtering is UNSUPPORTED" in r for r in eval_result.unsatisfied_reasons
    )


def test_unknown_capability_status_fails_closed() -> None:
    """Verify capabilities with UNKNOWN status strictly fail closed."""
    profile = SandboxProfile(
        profile_id="unknown-cap-profile",
        isolation_tier=IsolationTier.TIER_1_CONTAINER,
        resource_limits=ResourceLimits(pids_limit=100),
    )

    caps = SystemCapabilities(
        host_os="linux",
        host_arch="x86_64",
        docker_cli_available=CapabilityStatus.SUPPORTED,
        docker_daemon_available=CapabilityStatus.SUPPORTED,
        docker_backend=DockerBackendType.NATIVE_LINUX,
        pids_limit_supported=CapabilityStatus.UNKNOWN,
        read_only_rootfs_supported=CapabilityStatus.SUPPORTED,
        network_none_supported=CapabilityStatus.SUPPORTED,
        supported_tiers=(IsolationTier.TIER_0_LOCAL, IsolationTier.TIER_1_CONTAINER),
    )

    eval_result = evaluate_profile_satisfaction(profile, caps)
    assert eval_result.is_satisfied is False
    assert any("PIDs quota enforcement is UNKNOWN" in r for r in eval_result.unsatisfied_reasons)


@pytest.mark.asyncio
async def test_provider_health_contract() -> None:
    """Verify SandboxProvider reports structured health, version, features, and smoke test status."""
    # 1. TestDoubleSandboxProvider healthy
    td_provider = TestDoubleSandboxProvider()
    report = await td_provider.get_health_report(run_smoke_test=True)
    assert report.is_available is True
    assert report.is_healthy is True
    assert report.capacity_readiness == "READY"
    assert report.smoke_test_passed is True
    assert IsolationTier.TIER_1_CONTAINER in report.supported_tiers
    assert report.supported_features.get("seccomp") == CapabilityStatus.SUPPORTED

    # 2. TestDoubleSandboxProvider simulated offline
    offline_provider = TestDoubleSandboxProvider(simulate_unavailable=True)
    offline_report = await offline_provider.get_health_report(run_smoke_test=True)
    assert offline_report.is_available is False
    assert offline_report.is_healthy is False
    assert offline_report.capacity_readiness == "UNAVAILABLE"

    # 3. DockerSandboxProvider reports real host status without false claims
    docker_provider = DockerSandboxProvider()
    docker_report = await docker_provider.get_health_report(run_smoke_test=False)
    caps = get_system_capabilities()
    if caps.docker_daemon_available != CapabilityStatus.SUPPORTED:
        assert docker_report.is_available is False
        assert docker_report.is_healthy is False
        assert docker_report.capacity_readiness == "UNAVAILABLE"


def test_provider_selection_tier_mismatch_and_rejection() -> None:
    """Verify registry rejects wrong tiers, mismatched profiles, and unconfigured backends."""
    registry = SandboxProviderRegistry()

    # 1. Mismatched profile tier vs requested tier
    profile_tier2 = SandboxProfile(
        profile_id="tier2-profile",
        isolation_tier=IsolationTier.TIER_2_APPLICATION_SANDBOX,
    )
    with pytest.raises(SandboxBackendUnavailableError, match="does not match profile tier"):
        registry.resolve_provider(tier=IsolationTier.TIER_1_CONTAINER, profile=profile_tier2)

    # 2. Requesting Tier 2 when no Tier 2 provider is registered
    with pytest.raises(SandboxBackendUnavailableError, match="TIER_2_APPLICATION_SANDBOX"):
        registry.resolve_provider(tier=IsolationTier.TIER_2_APPLICATION_SANDBOX)

    # 3. Requesting Tier 3 when no Tier 3 provider is registered
    with pytest.raises(SandboxBackendUnavailableError, match="TIER_3_MICROVM"):
        registry.resolve_provider(tier=IsolationTier.TIER_3_MICROVM)
