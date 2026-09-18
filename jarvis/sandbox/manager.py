"""JARVIS Sandbox Lifecycle Manager.

Manages the formal sandbox lifecycle state machine, enforces valid state transitions,
tracks immutable sandbox identities, provides crash-recovery reconciliation,
propagates task cancellations, enforces execution timeouts, and performs TTL garbage collection.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field

from jarvis.core.exceptions import (
    SandboxBackendUnavailableError,
    SandboxExecutionError,
    SandboxTimeoutError,
    StateTransitionError,
)
from jarvis.core.logging import get_logger
from jarvis.sandbox.detector import evaluate_profile_satisfaction, get_system_capabilities
from jarvis.sandbox.models import (
    ArtifactTransferRecord,
    BackendType,
    IsolationTier,
    LifecycleTransitionEvent,
    ProviderCategory,
    SandboxAttestation,
    SandboxExecutionResult,
    SandboxIdentity,
    SandboxProfile,
    SandboxState,
)
from jarvis.sandbox.registry import SandboxProviderRegistry, get_sandbox_registry

if TYPE_CHECKING:
    from jarvis.sandbox.provider import SandboxProvider

logger = get_logger(__name__)

# Canonical valid state transition matrix
VALID_TRANSITIONS: dict[SandboxState, set[SandboxState]] = {
    SandboxState.CREATED: {
        SandboxState.STARTING,
        SandboxState.FAILED,
        SandboxState.KILLED,
        SandboxState.EXPIRED,
        SandboxState.RECOVERY_PENDING,
        SandboxState.UNKNOWN,
    },
    SandboxState.STARTING: {
        SandboxState.READY,
        SandboxState.FAILED,
        SandboxState.KILLED,
        SandboxState.TIMED_OUT,
        SandboxState.RECOVERY_PENDING,
        SandboxState.UNKNOWN,
    },
    SandboxState.READY: {
        SandboxState.EXECUTING,
        SandboxState.QUIESCING,
        SandboxState.EXPIRED,
        SandboxState.KILLED,
        SandboxState.FAILED,
        SandboxState.RECOVERY_PENDING,
        SandboxState.UNKNOWN,
    },
    SandboxState.EXECUTING: {
        SandboxState.READY,
        SandboxState.TIMED_OUT,
        SandboxState.FAILED,
        SandboxState.KILLED,
        SandboxState.QUIESCING,
        SandboxState.RECOVERY_PENDING,
        SandboxState.UNKNOWN,
    },
    SandboxState.QUIESCING: {
        SandboxState.TERMINATING,
        SandboxState.TERMINATED,
        SandboxState.FAILED,
        SandboxState.KILLED,
        SandboxState.RECOVERY_PENDING,
        SandboxState.UNKNOWN,
    },
    SandboxState.TERMINATING: {
        SandboxState.TERMINATED,
        SandboxState.FAILED,
        SandboxState.RECOVERY_PENDING,
        SandboxState.UNKNOWN,
    },
    SandboxState.TERMINATED: set(),  # Terminal state
    SandboxState.FAILED: {SandboxState.TERMINATED, SandboxState.RECOVERY_PENDING},
    SandboxState.TIMED_OUT: {SandboxState.TERMINATED, SandboxState.RECOVERY_PENDING},
    SandboxState.KILLED: {SandboxState.TERMINATED, SandboxState.RECOVERY_PENDING},
    SandboxState.EXPIRED: {SandboxState.TERMINATED, SandboxState.RECOVERY_PENDING},
    SandboxState.RECOVERY_PENDING: {
        SandboxState.QUIESCING,
        SandboxState.TERMINATING,
        SandboxState.TERMINATED,
        SandboxState.UNKNOWN,
    },
    SandboxState.UNKNOWN: {
        SandboxState.RECOVERY_PENDING,
        SandboxState.TERMINATING,
        SandboxState.TERMINATED,
    },
}

TERMINAL_STATES: set[SandboxState] = {
    SandboxState.TERMINATED,
}


class ManagedSandboxRecord(BaseModel):
    """Durable state record for an instantiated sandbox."""

    model_config = ConfigDict(frozen=False)

    identity: SandboxIdentity
    profile: SandboxProfile
    state: SandboxState
    created_at: datetime
    expires_at: datetime
    last_active_at: datetime
    provider_category: ProviderCategory
    backend_type: BackendType
    isolation_tier: IsolationTier
    transition_history: list[LifecycleTransitionEvent] = Field(default_factory=list)
    active_execution_id: UUID | None = None
    termination_reason: str | None = None
    is_recovered_orphan: bool = False


class SandboxLifecycleManager:
    """Orchestrates sandbox lifecycles, crash recovery, timeouts, and cleanup."""

    def __init__(
        self,
        registry: SandboxProviderRegistry | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.registry = registry or get_sandbox_registry()
        self.state_dir = state_dir or Path(".jarvis/sandbox_state")
        self._records: dict[UUID, ManagedSandboxRecord] = {}
        self._providers: dict[UUID, SandboxProvider] = {}
        self._lock = asyncio.Lock()

    def _get_persistence_path(self, sandbox_id: UUID) -> Path:
        """Resolve file path for a sandbox's persisted state."""
        return self.state_dir / f"{sandbox_id}.json"

    def _persist_record(self, record: ManagedSandboxRecord) -> None:
        """Persist record to disk for crash-resilience."""
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            p = self._get_persistence_path(record.identity.sandbox_id)
            p.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(
                "sandbox_state_persist_failed",
                sandbox_id=str(record.identity.sandbox_id),
                error=str(e),
            )

    async def initialize(self) -> int:
        """Reconcile orphaned sandboxes after process restart by inspecting real provider state."""
        async with self._lock:
            if not self.state_dir.exists():
                return 0

            orphans_recovered = 0
            for json_file in self.state_dir.glob("*.json"):
                try:
                    raw = json_file.read_text(encoding="utf-8")
                    data = json.loads(raw)
                    record = ManagedSandboxRecord.model_validate(data)
                    self._records[record.identity.sandbox_id] = record

                    # If the sandbox was not cleanly terminated before process died
                    if record.state not in TERMINAL_STATES:
                        logger.warning(
                            "reconciling_orphaned_sandbox",
                            sandbox_id=str(record.identity.sandbox_id),
                            prior_state=record.state.value,
                        )
                        record.is_recovered_orphan = True
                        prev_state = record.state

                        # 1. Look up / resolve provider
                        allow_td = record.provider_category == ProviderCategory.TEST_DOUBLE
                        provider: SandboxProvider | None = None
                        try:
                            provider = self.registry.resolve_provider(
                                tier=record.identity.isolation_tier,
                                profile=record.profile,
                                allow_test_doubles=allow_td,
                            )
                            self._providers[record.identity.sandbox_id] = provider
                        except Exception as e:
                            logger.error(
                                "provider_unavailable_during_recovery",
                                sandbox_id=str(record.identity.sandbox_id),
                                error=str(e),
                            )
                            # Provider unavailable: fail closed into RECOVERY_PENDING, do NOT falsely claim cleaned
                            record.state = SandboxState.RECOVERY_PENDING
                            record.termination_reason = (
                                f"recovery_pending: provider_unavailable ({e})"
                            )
                            event = LifecycleTransitionEvent(
                                sandbox_id=record.identity.sandbox_id,
                                task_id=record.identity.task_id,
                                previous_state=prev_state,
                                new_state=SandboxState.RECOVERY_PENDING,
                                reason=f"provider_unavailable_during_recovery: {e}",
                            )
                            record.transition_history.append(event)
                            self._persist_record(record)
                            continue

                        # 2. Query actual backend state
                        try:
                            actual_backend_state = await provider.inspect(
                                record.identity.sandbox_id
                            )
                        except SandboxBackendUnavailableError as e:
                            logger.warning(
                                "provider_unavailable_during_inspect",
                                sandbox_id=str(record.identity.sandbox_id),
                                error=str(e),
                            )
                            record.state = SandboxState.RECOVERY_PENDING
                            record.termination_reason = (
                                f"recovery_pending: provider_unavailable ({e})"
                            )
                            event = LifecycleTransitionEvent(
                                sandbox_id=record.identity.sandbox_id,
                                task_id=record.identity.task_id,
                                previous_state=prev_state,
                                new_state=SandboxState.RECOVERY_PENDING,
                                reason=f"provider_unavailable_during_inspect: {e}",
                            )
                            record.transition_history.append(event)
                            self._persist_record(record)
                            continue
                        except Exception as e:
                            logger.warning(
                                "inspect_failed_during_recovery",
                                sandbox_id=str(record.identity.sandbox_id),
                                error=str(e),
                            )
                            actual_backend_state = SandboxState.UNKNOWN

                        if actual_backend_state == SandboxState.TERMINATED:
                            # Sandbox absent from backend -> reconcile record to TERMINATED
                            record.state = SandboxState.TERMINATED
                            record.termination_reason = "reconciled_absent_backend_after_restart"
                            event = LifecycleTransitionEvent(
                                sandbox_id=record.identity.sandbox_id,
                                task_id=record.identity.task_id,
                                previous_state=prev_state,
                                new_state=SandboxState.TERMINATED,
                                reason="reconciled_absent_backend_after_restart",
                            )
                            record.transition_history.append(event)
                            self._persist_record(record)
                            orphans_recovered += 1
                        elif actual_backend_state == SandboxState.UNKNOWN:
                            # State is ambiguous
                            record.state = SandboxState.RECOVERY_PENDING
                            record.termination_reason = "recovery_pending: backend_state_unknown"
                            event = LifecycleTransitionEvent(
                                sandbox_id=record.identity.sandbox_id,
                                task_id=record.identity.task_id,
                                previous_state=prev_state,
                                new_state=SandboxState.RECOVERY_PENDING,
                                reason="backend_state_unknown_during_recovery",
                            )
                            record.transition_history.append(event)
                            self._persist_record(record)
                        else:
                            # Sandbox container exists in backend! Terminate, cleanup, and VERIFY absence
                            try:
                                await provider.terminate(
                                    record.identity.sandbox_id, reason="crash_recovery_cleanup"
                                )
                                await provider.cleanup(record.identity.sandbox_id)
                                verified_state = await provider.inspect(record.identity.sandbox_id)
                                if verified_state == SandboxState.TERMINATED:
                                    record.state = SandboxState.TERMINATED
                                    record.termination_reason = (
                                        "recovered_orphan_terminated_and_verified"
                                    )
                                    event = LifecycleTransitionEvent(
                                        sandbox_id=record.identity.sandbox_id,
                                        task_id=record.identity.task_id,
                                        previous_state=prev_state,
                                        new_state=SandboxState.TERMINATED,
                                        reason="recovered_orphan_terminated_and_verified",
                                    )
                                    record.transition_history.append(event)
                                    self._persist_record(record)
                                    orphans_recovered += 1
                                else:
                                    record.state = SandboxState.RECOVERY_PENDING
                                    record.termination_reason = (
                                        "recovery_pending: termination_unverified"
                                    )
                                    event = LifecycleTransitionEvent(
                                        sandbox_id=record.identity.sandbox_id,
                                        task_id=record.identity.task_id,
                                        previous_state=prev_state,
                                        new_state=SandboxState.RECOVERY_PENDING,
                                        reason="termination_unverified_after_cleanup",
                                    )
                                    record.transition_history.append(event)
                                    self._persist_record(record)
                            except Exception as e:
                                logger.error(
                                    "cleanup_failed_during_recovery",
                                    sandbox_id=str(record.identity.sandbox_id),
                                    error=str(e),
                                )
                                record.state = SandboxState.RECOVERY_PENDING
                                record.termination_reason = f"recovery_pending: cleanup_error ({e})"
                                event = LifecycleTransitionEvent(
                                    sandbox_id=record.identity.sandbox_id,
                                    task_id=record.identity.task_id,
                                    previous_state=prev_state,
                                    new_state=SandboxState.RECOVERY_PENDING,
                                    reason=f"cleanup_error_during_recovery: {e}",
                                )
                                record.transition_history.append(event)
                                self._persist_record(record)
                except Exception as exc:
                    logger.error(
                        "sandbox_recovery_parse_error", file=str(json_file), error=str(exc)
                    )

            if orphans_recovered > 0:
                logger.info("sandbox_crash_recovery_complete", orphans_recovered=orphans_recovered)
            return orphans_recovered

    def _transition(
        self,
        record: ManagedSandboxRecord,
        new_state: SandboxState,
        reason: str,
        request_id: UUID | None = None,
    ) -> None:
        """Validate and execute a lifecycle state transition."""
        curr_state = record.state
        allowed = VALID_TRANSITIONS.get(curr_state, set())
        if new_state not in allowed:
            raise StateTransitionError(
                f"Invalid sandbox state transition from '{curr_state.value}' to '{new_state.value}'. "
                f"Allowed target states: {[s.value for s in allowed]}."
            )

        record.state = new_state
        record.last_active_at = datetime.now(UTC)
        event = LifecycleTransitionEvent(
            sandbox_id=record.identity.sandbox_id,
            task_id=record.identity.task_id,
            request_id=request_id,
            previous_state=curr_state,
            new_state=new_state,
            reason=reason,
        )
        record.transition_history.append(event)
        self._persist_record(record)
        logger.debug(
            "sandbox_lifecycle_transition",
            sandbox_id=str(record.identity.sandbox_id),
            from_state=curr_state.value,
            to_state=new_state.value,
            reason=reason,
        )

    async def create_sandbox(
        self,
        task_id: UUID,
        profile: SandboxProfile,
        request_id: UUID | None = None,
        session_id: UUID | None = None,
        agent_id: str = "coding",
        allow_test_doubles: bool = False,
    ) -> SandboxIdentity:
        """Create and register a managed sandbox instance under explicit governance."""
        async with self._lock:
            # 1. Evaluate requirement satisfaction
            caps = get_system_capabilities()
            sat = evaluate_profile_satisfaction(profile, caps)
            if not sat.is_satisfied and not allow_test_doubles:
                raise SandboxExecutionError(
                    f"Profile '{profile.profile_id}' unsatisfied: {'; '.join(sat.unsatisfied_reasons)}"
                )

            # 2. Resolve capable provider (fail-closed if unavailable)
            provider = self.registry.resolve_provider(
                tier=profile.isolation_tier,
                profile=profile,
                allow_test_doubles=allow_test_doubles,
            )

            # 3. Instantiate through provider
            identity = await provider.create(
                task_id=task_id,
                profile=profile,
                request_id=request_id,
                session_id=session_id,
                agent_id=agent_id,
            )

            record = ManagedSandboxRecord(
                identity=identity,
                profile=profile,
                state=SandboxState.CREATED,
                created_at=identity.created_at,
                expires_at=identity.expires_at,
                last_active_at=identity.created_at,
                provider_category=identity.provider_category,
                backend_type=identity.backend_type,
                isolation_tier=identity.isolation_tier,
            )

            self._records[identity.sandbox_id] = record
            self._providers[identity.sandbox_id] = provider
            self._persist_record(record)
            return identity

    async def start_sandbox(self, sandbox_id: UUID, request_id: UUID | None = None) -> SandboxState:
        """Start an instantiated sandbox container."""
        async with self._lock:
            record = self._get_record(sandbox_id)
            provider = self._get_provider(sandbox_id)

            self._transition(
                record, SandboxState.STARTING, "initiating_container_start", request_id
            )
            try:
                state = await provider.start(sandbox_id)
                self._transition(
                    record, SandboxState.READY, "container_started_healthy", request_id
                )
                return state
            except Exception as exc:
                self._transition(record, SandboxState.FAILED, f"startup_failed: {exc}", request_id)
                raise

    async def execute_command(
        self,
        sandbox_id: UUID,
        command: list[str],
        task_id: UUID,
        capability_id: str,
        logical_effect_id: str | None = None,
        attempt_id: str | None = None,
        timeout_seconds: float | None = None,
        request_id: UUID | None = None,
    ) -> SandboxExecutionResult:
        """Execute a command within sandbox with lifecycle and timeout management."""
        record = self._get_record(sandbox_id)
        provider = self._get_provider(sandbox_id)

        # Check TTL
        if datetime.now(UTC) > record.expires_at:
            self._transition(record, SandboxState.EXPIRED, "ttl_expired", request_id)
            await self.terminate_sandbox(sandbox_id, reason="ttl_expired")
            raise SandboxExecutionError(f"Sandbox {sandbox_id} has expired (TTL reached).")

        async with self._lock:
            self._transition(
                record, SandboxState.EXECUTING, f"executing_command: {command[0]}", request_id
            )

        timeout = timeout_seconds or record.profile.resource_limits.timeout_seconds
        try:
            result = await provider.execute(
                sandbox_id=sandbox_id,
                command=command,
                task_id=task_id,
                capability_id=capability_id,
                logical_effect_id=logical_effect_id,
                attempt_id=attempt_id,
                timeout_seconds=timeout,
            )
            async with self._lock:
                self._transition(
                    record, SandboxState.READY, "execution_completed_successfully", request_id
                )
            return result
        except SandboxTimeoutError as timeout_err:
            async with self._lock:
                self._transition(
                    record,
                    SandboxState.TIMED_OUT,
                    f"command_timed_out_after_{timeout}s",
                    request_id,
                )
                record.termination_reason = "execution_timed_out"
            await self.terminate_sandbox(sandbox_id, reason="execution_timed_out")
            raise timeout_err
        except Exception as exc:
            async with self._lock:
                self._transition(record, SandboxState.FAILED, f"execution_error: {exc}", request_id)
                record.termination_reason = f"error: {exc}"
            raise

    async def cancel_task_sandboxes(
        self, task_id: UUID, reason: str = "task_cancelled"
    ) -> list[UUID]:
        """Propagate task cancellation to all associated active sandboxes with termination verification."""
        async with self._lock:
            cancelled_ids: list[UUID] = []
            for sbx_id, record in list(self._records.items()):
                if record.identity.task_id == task_id and record.state not in TERMINAL_STATES:
                    try:
                        self._transition(record, SandboxState.KILLED, reason)
                        record.termination_reason = reason
                        provider = self._providers.get(sbx_id)
                        if not provider:
                            try:
                                allow_td = record.provider_category == ProviderCategory.TEST_DOUBLE
                                provider = self.registry.resolve_provider(
                                    tier=record.identity.isolation_tier,
                                    profile=record.profile,
                                    allow_test_doubles=allow_td,
                                )
                                self._providers[sbx_id] = provider
                            except Exception:
                                provider = None

                        if provider:
                            await provider.terminate(sbx_id, reason=reason)
                            await provider.cleanup(sbx_id)
                            verified_state = await provider.inspect(sbx_id)
                            if verified_state == SandboxState.TERMINATED:
                                if SandboxState.TERMINATED in VALID_TRANSITIONS.get(
                                    record.state, set()
                                ):
                                    self._transition(
                                        record,
                                        SandboxState.TERMINATED,
                                        "cancellation_verified_cleaned",
                                    )
                            else:
                                if SandboxState.RECOVERY_PENDING in VALID_TRANSITIONS.get(
                                    record.state, set()
                                ):
                                    self._transition(
                                        record,
                                        SandboxState.RECOVERY_PENDING,
                                        f"cancellation_unverified: backend reported {verified_state.value}",
                                    )
                                record.termination_reason = f"cancellation_unverified: backend reported {verified_state.value}"
                        else:
                            if SandboxState.RECOVERY_PENDING in VALID_TRANSITIONS.get(
                                record.state, set()
                            ):
                                self._transition(
                                    record,
                                    SandboxState.RECOVERY_PENDING,
                                    "cancellation_provider_unavailable",
                                )
                            record.termination_reason = "cancellation_provider_unavailable"
                        cancelled_ids.append(sbx_id)
                    except Exception as e:
                        logger.error(
                            "error_cancelling_sandbox", sandbox_id=str(sbx_id), error=str(e)
                        )
                        if SandboxState.RECOVERY_PENDING in VALID_TRANSITIONS.get(
                            record.state, set()
                        ):
                            self._transition(
                                record, SandboxState.RECOVERY_PENDING, f"cancellation_error: {e}"
                            )
                        record.termination_reason = f"cancellation_error: {e}"
                        cancelled_ids.append(sbx_id)
            return cancelled_ids

    async def terminate_sandbox(
        self, sandbox_id: UUID, reason: str = "normal_completion"
    ) -> SandboxState:
        """Quiesce, terminate, clean up, and verify absence of a sandbox."""
        async with self._lock:
            record = self._get_record(sandbox_id)
            if record.state == SandboxState.TERMINATED:
                return record.state

            provider: SandboxProvider | None = self._providers.get(sandbox_id)
            if provider is None:
                try:
                    allow_td = record.provider_category == ProviderCategory.TEST_DOUBLE
                    provider = self.registry.resolve_provider(
                        tier=record.identity.isolation_tier,
                        profile=record.profile,
                        allow_test_doubles=allow_td,
                    )
                    self._providers[sandbox_id] = provider
                except Exception as e:
                    logger.warning(
                        "provider_unavailable_during_terminate",
                        sandbox_id=str(sandbox_id),
                        error=str(e),
                    )
                    if SandboxState.RECOVERY_PENDING in VALID_TRANSITIONS.get(record.state, set()):
                        self._transition(
                            record,
                            SandboxState.RECOVERY_PENDING,
                            f"termination_uncertain: provider_unavailable ({e})",
                        )
                    record.termination_reason = f"termination_uncertain: provider_unavailable ({e})"
                    return SandboxState.RECOVERY_PENDING

            # Move to QUIESCING -> TERMINATING
            if SandboxState.QUIESCING in VALID_TRANSITIONS.get(record.state, set()):
                self._transition(record, SandboxState.QUIESCING, reason)
            if SandboxState.TERMINATING in VALID_TRANSITIONS.get(record.state, set()):
                self._transition(record, SandboxState.TERMINATING, reason)

            try:
                await provider.terminate(sandbox_id, reason=reason)
                await provider.cleanup(sandbox_id)

                # Termination verification
                verified_state = await provider.inspect(sandbox_id)
                if verified_state == SandboxState.TERMINATED:
                    if SandboxState.TERMINATED in VALID_TRANSITIONS.get(record.state, set()):
                        self._transition(record, SandboxState.TERMINATED, reason)
                    record.termination_reason = reason
                    return SandboxState.TERMINATED
                else:
                    if SandboxState.RECOVERY_PENDING in VALID_TRANSITIONS.get(record.state, set()):
                        self._transition(
                            record,
                            SandboxState.RECOVERY_PENDING,
                            f"termination_unverified: backend reported {verified_state.value}",
                        )
                    record.termination_reason = (
                        f"termination_unverified: backend reported {verified_state.value}"
                    )
                    return SandboxState.RECOVERY_PENDING
            except Exception as e:
                logger.error(
                    "error_during_terminate_and_verify",
                    sandbox_id=str(sandbox_id),
                    error=str(e),
                )
                if SandboxState.RECOVERY_PENDING in VALID_TRANSITIONS.get(record.state, set()):
                    self._transition(
                        record,
                        SandboxState.RECOVERY_PENDING,
                        f"termination_error: {e}",
                    )
                record.termination_reason = f"termination_error: {e}"
                return SandboxState.RECOVERY_PENDING

    async def reap_expired_sandboxes(self) -> int:
        """Garbage collect all expired or abandoned sandboxes with termination verification."""
        now = datetime.now(UTC)
        reaped_count = 0
        async with self._lock:
            for sbx_id, record in list(self._records.items()):
                if record.state not in TERMINAL_STATES and now >= record.expires_at:
                    provider = self._providers.get(sbx_id)
                    if not provider:
                        try:
                            allow_td = record.provider_category == ProviderCategory.TEST_DOUBLE
                            provider = self.registry.resolve_provider(
                                tier=record.identity.isolation_tier,
                                profile=record.profile,
                                allow_test_doubles=allow_td,
                            )
                            self._providers[sbx_id] = provider
                        except Exception as e:
                            logger.warning(
                                "provider_unavailable_during_reap",
                                sandbox_id=str(sbx_id),
                                error=str(e),
                            )
                            if SandboxState.RECOVERY_PENDING in VALID_TRANSITIONS.get(
                                record.state, set()
                            ):
                                self._transition(
                                    record,
                                    SandboxState.RECOVERY_PENDING,
                                    f"ttl_reap_provider_unavailable: {e}",
                                )
                            record.termination_reason = f"ttl_expired_provider_unavailable: {e}"
                            continue

                    try:
                        logger.info(
                            "reaping_expired_sandbox",
                            sandbox_id=str(sbx_id),
                            expires_at=str(record.expires_at),
                        )
                        self._transition(record, SandboxState.EXPIRED, "ttl_expired_reaper")
                        await provider.terminate(sbx_id, reason="ttl_expired")
                        await provider.cleanup(sbx_id)

                        verified_state = await provider.inspect(sbx_id)
                        if verified_state == SandboxState.TERMINATED:
                            if SandboxState.TERMINATED in VALID_TRANSITIONS.get(
                                record.state, set()
                            ):
                                self._transition(
                                    record, SandboxState.TERMINATED, "ttl_expired_verified_cleaned"
                                )
                            record.termination_reason = "ttl_expired_cleaned"
                            reaped_count += 1
                        else:
                            if SandboxState.RECOVERY_PENDING in VALID_TRANSITIONS.get(
                                record.state, set()
                            ):
                                self._transition(
                                    record,
                                    SandboxState.RECOVERY_PENDING,
                                    f"ttl_reap_unverified: backend reported {verified_state.value}",
                                )
                            record.termination_reason = (
                                f"ttl_expired_unverified: backend reported {verified_state.value}"
                            )
                    except Exception as e:
                        logger.error("error_reaping_sandbox", sandbox_id=str(sbx_id), error=str(e))
                        if SandboxState.RECOVERY_PENDING in VALID_TRANSITIONS.get(
                            record.state, set()
                        ):
                            self._transition(
                                record, SandboxState.RECOVERY_PENDING, f"reap_error: {e}"
                            )
                        record.termination_reason = f"ttl_expired_error: {e}"
        return reaped_count

    def get_sandbox_state(self, sandbox_id: UUID) -> SandboxState:
        """Query the current state of a sandbox."""
        record = self._get_record(sandbox_id)
        return record.state

    def get_sandbox_record(self, sandbox_id: UUID) -> ManagedSandboxRecord:
        """Retrieve the complete durable audit record of a sandbox."""
        return self._get_record(sandbox_id)

    def _get_record(self, sandbox_id: UUID) -> ManagedSandboxRecord:
        record = self._records.get(sandbox_id)
        if not record:
            raise SandboxExecutionError(f"Managed sandbox record '{sandbox_id}' not found.")
        return record

    def _get_provider(self, sandbox_id: UUID) -> SandboxProvider:
        provider = self._providers.get(sandbox_id)
        if not provider:
            raise SandboxExecutionError(f"Provider for sandbox '{sandbox_id}' is not registered.")
        return provider

    async def attest_sandbox(self, sandbox_id: UUID) -> SandboxAttestation:
        """Inspect and attest the empirical security properties of an active managed sandbox."""
        async with self._lock:
            record = self._get_record(sandbox_id)
            provider = self._providers.get(sandbox_id)
            if not provider:
                allow_td = record.provider_category == ProviderCategory.TEST_DOUBLE
                provider = self.registry.resolve_provider(
                    tier=record.identity.isolation_tier,
                    profile=record.profile,
                    allow_test_doubles=allow_td,
                )
                self._providers[sandbox_id] = provider
            return await provider.attest(sandbox_id)

    async def upload_artifacts(
        self,
        sandbox_id: UUID,
        task_id: UUID,
        artifacts: list[tuple[Path, str]],
    ) -> list[ArtifactTransferRecord]:
        """Upload host artifacts into sandbox workspace via underlying provider."""
        async with self._lock:
            provider = self._get_provider(sandbox_id)
            return await provider.upload_artifacts(
                sandbox_id=sandbox_id,
                task_id=task_id,
                artifacts=artifacts,
            )

    async def download_artifacts(
        self,
        sandbox_id: UUID,
        task_id: UUID,
        artifacts: list[tuple[str, Path]],
    ) -> list[ArtifactTransferRecord]:
        """Download artifacts from sandbox workspace back to host via underlying provider."""
        async with self._lock:
            provider = self._get_provider(sandbox_id)
            return await provider.download_artifacts(
                sandbox_id=sandbox_id,
                task_id=task_id,
                artifacts=artifacts,
            )
