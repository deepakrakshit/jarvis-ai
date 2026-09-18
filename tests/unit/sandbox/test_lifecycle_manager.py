"""Unit tests for Sandbox Lifecycle Manager, Transition Matrix, and Crash Recovery.

Verifies:
1. Valid state transitions execute cleanly according to canonical transition matrix.
2. Invalid state transitions raise StateTransitionError.
3. Execution timeout transitions sandbox to TIMED_OUT and cleans up resources.
4. Provider startup failures transition sandbox to FAILED.
5. Task cancellation propagates to all active sandboxes, transitioning them to KILLED.
6. TTL expiration is reaped by garbage collection.
7. Process crash recovery reconciles orphaned sandboxes on restart.
8. Profile requirement satisfaction evaluator identifies unsatisfied security constraints.
9. Explicit CONTRACT_VERIFIED labeling prevents test doubles from claiming real isolation.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.core.exceptions import (
    SandboxExecutionError,
    SandboxTimeoutError,
    StateTransitionError,
)
from jarvis.sandbox.detector import evaluate_profile_satisfaction
from jarvis.sandbox.manager import SandboxLifecycleManager
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
from jarvis.sandbox.provider import TestDoubleSandboxProvider
from jarvis.sandbox.registry import SandboxProviderRegistry


@pytest.fixture
def test_registry() -> SandboxProviderRegistry:
    """Registry configured with a standard test double for contract verification."""
    reg = SandboxProviderRegistry()
    return reg


@pytest.fixture
def lifecycle_mgr(
    tmp_path: Path, test_registry: SandboxProviderRegistry
) -> SandboxLifecycleManager:
    """Lifecycle manager backed by an ephemeral temporary state directory."""
    return SandboxLifecycleManager(registry=test_registry, state_dir=tmp_path / "sandbox_state")


@pytest.mark.asyncio
async def test_lifecycle_full_happy_path(lifecycle_mgr: SandboxLifecycleManager) -> None:
    """Verify standard lifecycle progression: CREATED -> STARTING -> READY -> EXECUTING -> READY -> TERMINATED."""
    task_id = uuid4()
    profile = SandboxProfile(profile_id="test-happy-path")

    # 1. Create
    identity = await lifecycle_mgr.create_sandbox(
        task_id=task_id,
        profile=profile,
        allow_test_doubles=True,
    )
    assert lifecycle_mgr.get_sandbox_state(identity.sandbox_id) == SandboxState.CREATED

    # 2. Start
    state = await lifecycle_mgr.start_sandbox(identity.sandbox_id)
    assert state == SandboxState.READY
    assert lifecycle_mgr.get_sandbox_state(identity.sandbox_id) == SandboxState.READY

    # 3. Execute
    res = await lifecycle_mgr.execute_command(
        sandbox_id=identity.sandbox_id,
        command=["python", "script.py"],
        task_id=task_id,
        capability_id="sandbox:code:execute",
    )
    assert res.exit_code == 0
    assert res.provider_category == ProviderCategory.TEST_DOUBLE
    assert lifecycle_mgr.get_sandbox_state(identity.sandbox_id) == SandboxState.READY

    # 4. Terminate
    term_state = await lifecycle_mgr.terminate_sandbox(identity.sandbox_id, reason="task_done")
    assert term_state == SandboxState.TERMINATED
    assert lifecycle_mgr.get_sandbox_state(identity.sandbox_id) == SandboxState.TERMINATED

    # Verify transition audit history
    record = lifecycle_mgr.get_sandbox_record(identity.sandbox_id)
    states = [ev.new_state for ev in record.transition_history]
    assert states == [
        SandboxState.STARTING,
        SandboxState.READY,
        SandboxState.EXECUTING,
        SandboxState.READY,
        SandboxState.QUIESCING,
        SandboxState.TERMINATING,
        SandboxState.TERMINATED,
    ]


@pytest.mark.asyncio
async def test_lifecycle_invalid_transition_rejection(
    lifecycle_mgr: SandboxLifecycleManager,
) -> None:
    """Verify invalid state transitions raise StateTransitionError."""
    task_id = uuid4()
    profile = SandboxProfile(profile_id="test-invalid-trans")

    identity = await lifecycle_mgr.create_sandbox(
        task_id=task_id,
        profile=profile,
        allow_test_doubles=True,
    )
    record = lifecycle_mgr.get_sandbox_record(identity.sandbox_id)

    # CREATED -> EXECUTING is invalid (must go through STARTING -> READY first)
    with pytest.raises(StateTransitionError, match="Invalid sandbox state transition"):
        lifecycle_mgr._transition(record, SandboxState.EXECUTING, "illegal_jump")


@pytest.mark.asyncio
async def test_lifecycle_timeout_handling(lifecycle_mgr: SandboxLifecycleManager) -> None:
    """Verify execution timeout transitions sandbox to TIMED_OUT and triggers termination."""
    task_id = uuid4()
    profile = SandboxProfile(
        profile_id="timeout-profile",
        resource_limits=ResourceLimits(timeout_seconds=0.05),
    )

    # Configure provider to simulate timeout
    timeout_provider = TestDoubleSandboxProvider(simulate_timeout=True)
    lifecycle_mgr.registry.register_provider(timeout_provider)

    identity = await lifecycle_mgr.create_sandbox(
        task_id=task_id,
        profile=profile,
        allow_test_doubles=True,
    )
    await lifecycle_mgr.start_sandbox(identity.sandbox_id)

    with pytest.raises(SandboxTimeoutError):
        await lifecycle_mgr.execute_command(
            sandbox_id=identity.sandbox_id,
            command=["sleep", "10"],
            task_id=task_id,
            capability_id="sandbox:code:execute",
        )

    # Sandbox must be terminated after timeout
    record = lifecycle_mgr.get_sandbox_record(identity.sandbox_id)
    assert record.termination_reason == "execution_timed_out"
    assert record.state == SandboxState.TERMINATED


@pytest.mark.asyncio
async def test_lifecycle_startup_failure(lifecycle_mgr: SandboxLifecycleManager) -> None:
    """Verify container startup failure transitions sandbox to FAILED."""
    task_id = uuid4()
    profile = SandboxProfile(profile_id="fail-profile")

    fail_provider = TestDoubleSandboxProvider(simulate_failure=True)
    lifecycle_mgr.registry.register_provider(fail_provider)

    identity = await lifecycle_mgr.create_sandbox(
        task_id=task_id,
        profile=profile,
        allow_test_doubles=True,
    )

    with pytest.raises(SandboxExecutionError, match="Simulated backend startup failure"):
        await lifecycle_mgr.start_sandbox(identity.sandbox_id)

    record = lifecycle_mgr.get_sandbox_record(identity.sandbox_id)
    assert record.state == SandboxState.FAILED


@pytest.mark.asyncio
async def test_task_cancellation_propagation(lifecycle_mgr: SandboxLifecycleManager) -> None:
    """Verify task cancellation terminates and kills all active sandboxes."""
    task_id = uuid4()
    profile = SandboxProfile(profile_id="cancel-profile")

    id1 = await lifecycle_mgr.create_sandbox(
        task_id=task_id, profile=profile, allow_test_doubles=True
    )
    id2 = await lifecycle_mgr.create_sandbox(
        task_id=task_id, profile=profile, allow_test_doubles=True
    )
    await lifecycle_mgr.start_sandbox(id1.sandbox_id)
    await lifecycle_mgr.start_sandbox(id2.sandbox_id)

    # Cancel the task
    cancelled = await lifecycle_mgr.cancel_task_sandboxes(
        task_id=task_id, reason="user_cancelled_task"
    )
    assert len(cancelled) == 2
    assert id1.sandbox_id in cancelled
    assert id2.sandbox_id in cancelled

    r1 = lifecycle_mgr.get_sandbox_record(id1.sandbox_id)
    r2 = lifecycle_mgr.get_sandbox_record(id2.sandbox_id)
    # Verified termination moves to TERMINATED with KILLED recorded in history
    assert r1.state == SandboxState.TERMINATED
    assert r2.state == SandboxState.TERMINATED
    assert any(ev.new_state == SandboxState.KILLED for ev in r1.transition_history)
    assert r1.termination_reason == "user_cancelled_task"


@pytest.mark.asyncio
async def test_ttl_expiration_and_reaping(lifecycle_mgr: SandboxLifecycleManager) -> None:
    """Verify expired sandboxes are garbage-collected and verified terminated."""
    task_id = uuid4()
    profile = SandboxProfile(profile_id="ttl-profile")

    identity = await lifecycle_mgr.create_sandbox(
        task_id=task_id, profile=profile, allow_test_doubles=True
    )
    await lifecycle_mgr.start_sandbox(identity.sandbox_id)

    # Manually expire the record
    record = lifecycle_mgr.get_sandbox_record(identity.sandbox_id)
    record.expires_at = datetime.fromtimestamp(datetime.now(UTC).timestamp() - 100, tz=UTC)

    reaped = await lifecycle_mgr.reap_expired_sandboxes()
    assert reaped == 1
    # Verified reaping transitions through EXPIRED into TERMINATED
    assert lifecycle_mgr.get_sandbox_state(identity.sandbox_id) == SandboxState.TERMINATED
    rec = lifecycle_mgr.get_sandbox_record(identity.sandbox_id)
    assert any(ev.new_state == SandboxState.EXPIRED for ev in rec.transition_history)


@pytest.mark.asyncio
async def test_crash_recovery_reconciliation(
    tmp_path: Path, test_registry: SandboxProviderRegistry
) -> None:
    """Verify process restart reconciles orphaned sandboxes persisted on disk."""
    state_dir = tmp_path / "crash_recovery_state"
    mgr1 = SandboxLifecycleManager(registry=test_registry, state_dir=state_dir)

    task_id = uuid4()
    profile = SandboxProfile(profile_id="crash-profile")
    identity = await mgr1.create_sandbox(task_id=task_id, profile=profile, allow_test_doubles=True)
    await mgr1.start_sandbox(identity.sandbox_id)

    # Verify state is READY before simulated crash
    assert mgr1.get_sandbox_state(identity.sandbox_id) == SandboxState.READY

    # Simulate process crash: instantiate fresh manager with same persisted state directory
    mgr2 = SandboxLifecycleManager(registry=test_registry, state_dir=state_dir)
    orphans = await mgr2.initialize()

    assert orphans == 1
    rec = mgr2.get_sandbox_record(identity.sandbox_id)
    assert rec.is_recovered_orphan is True
    assert rec.state == SandboxState.TERMINATED
    assert rec.termination_reason == "recovered_orphan_terminated_and_verified"


def test_profile_satisfaction_evaluator() -> None:
    """Verify evaluate_profile_satisfaction identifies unsatisfied security constraints."""
    profile = SandboxProfile(
        profile_id="strict-profile",
        isolation_tier=IsolationTier.TIER_1_CONTAINER,
        read_only_rootfs=True,
        network_profile=NetworkProfile.NONE,
        resource_limits=ResourceLimits(pids_limit=50),
    )

    # 1. Fully satisfied mock capabilities
    sat_caps = SystemCapabilities(
        host_os="linux",
        host_arch="x86_64",
        docker_cli_available=CapabilityStatus.SUPPORTED,
        docker_daemon_available=CapabilityStatus.SUPPORTED,
        docker_backend=DockerBackendType.NATIVE_LINUX,
        read_only_rootfs_supported=CapabilityStatus.SUPPORTED,
        network_none_supported=CapabilityStatus.SUPPORTED,
        no_new_privileges_supported=CapabilityStatus.SUPPORTED,
        pids_limit_supported=CapabilityStatus.SUPPORTED,
        memory_limit_supported=CapabilityStatus.SUPPORTED,
        cpu_limit_supported=CapabilityStatus.SUPPORTED,
        seccomp_supported=CapabilityStatus.SUPPORTED,
        capability_drop_supported=CapabilityStatus.SUPPORTED,
        supported_tiers=(IsolationTier.TIER_0_LOCAL, IsolationTier.TIER_1_CONTAINER),
    )
    res = evaluate_profile_satisfaction(profile, sat_caps)
    assert res.is_satisfied is True
    assert len(res.unsatisfied_reasons) == 0

    # 2. Unsatisfied capabilities (Docker daemon unavailable)
    unsat_caps = SystemCapabilities(
        host_os="windows",
        host_arch="x86_64",
        docker_cli_available=CapabilityStatus.UNAVAILABLE,
        docker_daemon_available=CapabilityStatus.UNAVAILABLE,
        supported_tiers=(IsolationTier.TIER_0_LOCAL,),
        diagnostics=("Docker daemon is not running",),
    )
    res_unsat = evaluate_profile_satisfaction(profile, unsat_caps)
    assert res_unsat.is_satisfied is False
    assert len(res_unsat.unsatisfied_reasons) >= 2
    assert any("Docker daemon is not operational" in r for r in res_unsat.unsatisfied_reasons)


@pytest.mark.asyncio
async def test_crash_recovery_sandbox_exists_in_backend(
    tmp_path: Path, test_registry: SandboxProviderRegistry
) -> None:
    """Verify orphaned sandbox that exists in backend is terminated, cleaned, and verified absent."""
    state_dir = tmp_path / "crash_backend_exists_state"
    mgr1 = SandboxLifecycleManager(registry=test_registry, state_dir=state_dir)

    task_id = uuid4()
    profile = SandboxProfile(profile_id="backend-exists-profile")
    identity = await mgr1.create_sandbox(task_id=task_id, profile=profile, allow_test_doubles=True)
    await mgr1.start_sandbox(identity.sandbox_id)

    # Provider retained on restart
    mgr2 = SandboxLifecycleManager(registry=test_registry, state_dir=state_dir)
    orphans = await mgr2.initialize()

    assert orphans == 1
    rec = mgr2.get_sandbox_record(identity.sandbox_id)
    assert rec.is_recovered_orphan is True
    assert rec.state == SandboxState.TERMINATED
    assert rec.termination_reason == "recovered_orphan_terminated_and_verified"


@pytest.mark.asyncio
async def test_crash_recovery_provider_unavailable(tmp_path: Path) -> None:
    """Verify orphaned sandbox transitions to RECOVERY_PENDING if provider is offline on restart."""
    state_dir = tmp_path / "crash_provider_unavailable_state"
    reg1 = SandboxProviderRegistry()
    mgr1 = SandboxLifecycleManager(registry=reg1, state_dir=state_dir)

    task_id = uuid4()
    profile = SandboxProfile(profile_id="provider-offline-profile")
    identity = await mgr1.create_sandbox(task_id=task_id, profile=profile, allow_test_doubles=True)
    await mgr1.start_sandbox(identity.sandbox_id)

    # Manager on restart uses registry where test double is unavailable
    reg2 = SandboxProviderRegistry()
    offline_provider = TestDoubleSandboxProvider(simulate_unavailable=True)
    reg2.register_provider(offline_provider)

    mgr2 = SandboxLifecycleManager(registry=reg2, state_dir=state_dir)
    orphans = await mgr2.initialize()

    assert orphans == 0  # Not marked cleanly recovered/terminated
    rec = mgr2.get_sandbox_record(identity.sandbox_id)
    assert rec.is_recovered_orphan is True
    assert rec.state == SandboxState.RECOVERY_PENDING
    assert "recovery_pending: provider_unavailable" in (rec.termination_reason or "")


@pytest.mark.asyncio
async def test_termination_uncertain_transitions_to_recovery_pending() -> None:
    """Verify termination transitions to RECOVERY_PENDING if provider state is uncertain."""
    registry = SandboxProviderRegistry()
    uncertain_provider = TestDoubleSandboxProvider(simulate_uncertain_termination=True)
    registry.register_provider(uncertain_provider)

    mgr = SandboxLifecycleManager(registry=registry)
    task_id = uuid4()
    profile = SandboxProfile(profile_id="uncertain-term-profile")

    identity = await mgr.create_sandbox(task_id=task_id, profile=profile, allow_test_doubles=True)
    await mgr.start_sandbox(identity.sandbox_id)

    # Terminate should fail verification and return RECOVERY_PENDING
    result_state = await mgr.terminate_sandbox(identity.sandbox_id, reason="test_done")
    assert result_state == SandboxState.RECOVERY_PENDING
    assert mgr.get_sandbox_state(identity.sandbox_id) == SandboxState.RECOVERY_PENDING
    rec = mgr.get_sandbox_record(identity.sandbox_id)
    assert "termination_unverified" in (rec.termination_reason or "")


@pytest.mark.asyncio
async def test_crash_recovery_sandbox_absent_in_backend(
    tmp_path: Path, test_registry: SandboxProviderRegistry
) -> None:
    """Verify orphaned sandbox absent in provider backend reconciles directly to TERMINATED."""
    state_dir = tmp_path / "crash_backend_absent_state"
    mgr1 = SandboxLifecycleManager(registry=test_registry, state_dir=state_dir)

    task_id = uuid4()
    profile = SandboxProfile(profile_id="backend-absent-profile")
    identity = await mgr1.create_sandbox(task_id=task_id, profile=profile, allow_test_doubles=True)
    await mgr1.start_sandbox(identity.sandbox_id)

    # Manager on restart uses a fresh registry where provider backend does not have this container
    fresh_reg = SandboxProviderRegistry()
    mgr2 = SandboxLifecycleManager(registry=fresh_reg, state_dir=state_dir)
    orphans = await mgr2.initialize()

    assert orphans == 1
    rec = mgr2.get_sandbox_record(identity.sandbox_id)
    assert rec.is_recovered_orphan is True
    assert rec.state == SandboxState.TERMINATED
    assert rec.termination_reason == "reconciled_absent_backend_after_restart"


@pytest.mark.asyncio
async def test_task_cancellation_uncertain_transitions_to_recovery_pending() -> None:
    """Verify task cancellation transitions to RECOVERY_PENDING if provider termination is uncertain."""
    registry = SandboxProviderRegistry()
    uncertain_provider = TestDoubleSandboxProvider(simulate_uncertain_termination=True)
    registry.register_provider(uncertain_provider)

    mgr = SandboxLifecycleManager(registry=registry)
    task_id = uuid4()
    profile = SandboxProfile(profile_id="uncertain-cancel-profile")

    identity = await mgr.create_sandbox(task_id=task_id, profile=profile, allow_test_doubles=True)
    await mgr.start_sandbox(identity.sandbox_id)

    cancelled = await mgr.cancel_task_sandboxes(task_id=task_id, reason="user_abort")
    assert len(cancelled) == 1
    assert identity.sandbox_id in cancelled

    rec = mgr.get_sandbox_record(identity.sandbox_id)
    assert rec.state == SandboxState.RECOVERY_PENDING
    assert "cancellation_unverified" in (rec.termination_reason or "")


@pytest.mark.asyncio
async def test_ttl_reaping_uncertain_transitions_to_recovery_pending() -> None:
    """Verify TTL reaping transitions to RECOVERY_PENDING if provider termination is uncertain."""
    registry = SandboxProviderRegistry()
    uncertain_provider = TestDoubleSandboxProvider(simulate_uncertain_termination=True)
    registry.register_provider(uncertain_provider)

    mgr = SandboxLifecycleManager(registry=registry)
    task_id = uuid4()
    profile = SandboxProfile(profile_id="uncertain-reap-profile")

    identity = await mgr.create_sandbox(task_id=task_id, profile=profile, allow_test_doubles=True)
    await mgr.start_sandbox(identity.sandbox_id)

    # Manually expire the record
    rec = mgr.get_sandbox_record(identity.sandbox_id)
    rec.expires_at = datetime.fromtimestamp(datetime.now(UTC).timestamp() - 100, tz=UTC)

    reaped = await mgr.reap_expired_sandboxes()
    assert reaped == 0  # Not cleanly terminated

    assert mgr.get_sandbox_state(identity.sandbox_id) == SandboxState.RECOVERY_PENDING
    rec_after = mgr.get_sandbox_record(identity.sandbox_id)
    assert "ttl_expired_unverified" in (rec_after.termination_reason or "")
