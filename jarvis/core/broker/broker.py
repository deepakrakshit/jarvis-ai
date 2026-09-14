"""JARVIS Central Action Broker.

Coordinates the write/effect execution path:
1. Deterministic logical_effect_id computation
2. Idempotency ledger caching and duplicate protection
3. Commit-time EffectAuthorization enforcement
4. Distributed lease acquisition & concurrency deduplication
5. Centralized circuit breaker evaluation
6. 10-state effect lifecycle transitions
7. Ambiguous-outcome defense for non-idempotent operations
8. Saga compensation workflows upon failure
(ARCHITECTURE.md Layer 14).
"""

import hashlib
import inspect
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

from jarvis.core.broker.circuit_breaker import CircuitBreakerRegistry
from jarvis.core.broker.lease import LeaseManager
from jarvis.core.broker.ledger import IdempotencyLedger
from jarvis.core.broker.retry import RetryClassifier
from jarvis.core.broker.saga import CompensationRegistry, SagaCompensationPlan
from jarvis.core.broker.types import (
    ActionBrokerError,
    AmbiguousOutcomeError,
    CompensationFailedError,
    EffectAuthorizationRequiredError,
    EffectState,
    IdempotencyClass,
    IdempotencyConflictError,
    LeaseAcquisitionError,
    RetryClassification,
)
from jarvis.core.capabilities.manifest import CapabilityManifest, RiskClass, SideEffectClass
from jarvis.core.logging import get_logger
from jarvis.core.policy.decision import EffectAuthorization

logger = get_logger(__name__)


class ActionBroker:
    """The central Action Broker governing all real-world effects and tool invocations."""

    def __init__(
        self,
        ledger: IdempotencyLedger | None = None,
        lease_manager: LeaseManager | None = None,
        circuit_breaker_registry: CircuitBreakerRegistry | None = None,
        compensation_registry: CompensationRegistry | None = None,
    ) -> None:
        self.ledger = ledger or IdempotencyLedger()
        self.lease_manager = lease_manager or LeaseManager()
        self.circuit_breakers = circuit_breaker_registry or CircuitBreakerRegistry()
        self.compensation_registry = compensation_registry or CompensationRegistry()

    @staticmethod
    def compute_canonical_hash(arguments: dict[str, Any]) -> str:
        """Compute deterministic SHA-256 hash of normalized argument JSON."""
        canonical_json = json.dumps(arguments, sort_keys=True, default=str)
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    @classmethod
    def compute_logical_effect_id(
        cls,
        task_id: UUID | str,
        tool_id: str,
        arguments: dict[str, Any],
        intent_counter: int = 0,
    ) -> str:
        """Compute the deterministic logical_effect_id for an intended mutation.

        Binds task identity, tool identity, canonical arguments, and step counter.
        """
        args_hash = cls.compute_canonical_hash(arguments)
        seed = f"{task_id}:{tool_id}:{args_hash}:{intent_counter}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
        return f"eff_{digest}"

    @staticmethod
    def resolve_idempotency_class(
        manifest: CapabilityManifest | None = None,
        explicit_class: IdempotencyClass | None = None,
    ) -> IdempotencyClass:
        """Resolve idempotency class from manifest or explicit override."""
        if explicit_class is not None:
            return explicit_class

        if manifest is not None:
            if manifest.risk_class == RiskClass.READ_ONLY:
                return IdempotencyClass.IDEMPOTENT
            if manifest.side_effect_class == SideEffectClass.NONE:
                return IdempotencyClass.IDEMPOTENT
            if manifest.side_effect_class == SideEffectClass.IDEMPOTENT:
                return IdempotencyClass.IDEMPOTENT
            if manifest.side_effect_class == SideEffectClass.NON_IDEMPOTENT:
                return IdempotencyClass.NON_IDEMPOTENT

        return IdempotencyClass.UNKNOWN

    async def execute_action(
        self,
        task_id: UUID,
        tool_id: str,
        arguments: dict[str, Any],
        executor_fn: Callable[[dict[str, Any]], Any],
        manifest: CapabilityManifest | None = None,
        authorization: EffectAuthorization | None = None,
        worker_id: str = "broker_worker",
        idempotency_class: IdempotencyClass | None = None,
        target_resource: str | None = None,
        intent_counter: int = 0,
        lease_ttl_seconds: float = 60.0,
        compensation_plan: SagaCompensationPlan | None = None,
    ) -> Any:
        """Execute a tool action through the hardened Action Broker pipeline."""
        canonical_args_hash = self.compute_canonical_hash(arguments)
        logical_effect_id = self.compute_logical_effect_id(
            task_id=task_id,
            tool_id=tool_id,
            arguments=arguments,
            intent_counter=intent_counter,
        )
        resolved_idempotency = self.resolve_idempotency_class(manifest, idempotency_class)

        # 1. Idempotency Ledger Pre-Check
        existing_record = self.ledger.get(logical_effect_id)
        if existing_record:
            if existing_record.state == EffectState.VERIFIED:
                logger.info(
                    "action_broker_idempotency_cache_hit",
                    logical_effect_id=logical_effect_id,
                    tool_id=tool_id,
                )
                return existing_record.cached_result

            if (
                existing_record.state == EffectState.OUTCOME_UNKNOWN
                and resolved_idempotency == IdempotencyClass.NON_IDEMPOTENT
            ):
                # CRITICAL EXIT GATE: Refuse to blindly re-execute non-idempotent tool in ambiguous state
                logger.error(
                    "action_broker_ambiguous_outcome_reentry_blocked",
                    logical_effect_id=logical_effect_id,
                    tool_id=tool_id,
                )
                raise AmbiguousOutcomeError(
                    logical_effect_id=logical_effect_id,
                    tool_id=tool_id,
                    message=(
                        f"Refusing to re-execute non-idempotent tool '{tool_id}' for effect '{logical_effect_id}'. "
                        "Previous attempt ended in OUTCOME_UNKNOWN. External verification required."
                    ),
                )

        # 2. Authorization Enforcement (Layer 13 & 14 boundary)
        requires_auth = False
        if manifest:
            if manifest.risk_class != RiskClass.READ_ONLY or manifest.approval_requirement:
                requires_auth = True
        elif resolved_idempotency != IdempotencyClass.IDEMPOTENT:
            requires_auth = True

        if requires_auth:
            if authorization is None:
                raise EffectAuthorizationRequiredError(
                    tool_id=tool_id,
                    message=f"Action '{tool_id}' modifies state and requires a valid EffectAuthorization.",
                )
            if not authorization.is_valid():
                raise ActionBrokerError(
                    f"EffectAuthorization for '{tool_id}' is expired or already consumed."
                )
            if authorization.canonical_arguments_hash != canonical_args_hash:
                raise ActionBrokerError(
                    f"EffectAuthorization arguments hash mismatch for tool '{tool_id}'."
                )
            # Mark authorization as used atomically
            authorization.mark_used()

        # 3. Circuit Breaker Check
        breaker = self.circuit_breakers.get_breaker(tool_id)
        breaker.check_and_raise()

        # 4. Acquire Execution Lease (Deduplication Lock)
        try:
            lease = await self.lease_manager.acquire_async(
                resource_id=logical_effect_id,
                holder_id=worker_id,
                ttl_seconds=lease_ttl_seconds,
            )
        except LeaseAcquisitionError as err:
            logger.warning(
                "action_broker_lease_conflict",
                logical_effect_id=logical_effect_id,
                holder_id=worker_id,
            )
            raise IdempotencyConflictError(
                logical_effect_id=logical_effect_id,
                message=f"Concurrent execution in flight for effect '{logical_effect_id}': {err}",
            ) from err

        # 5. Record Proposal & Transition to AUTHORIZED -> DISPATCHING
        record = self.ledger.record_proposal(
            logical_effect_id=logical_effect_id,
            task_id=task_id,
            tool_id=tool_id,
            idempotency_class=resolved_idempotency,
            canonical_arguments_hash=canonical_args_hash,
            target_resource=target_resource,
        )

        if record.state in (EffectState.PROPOSED, EffectState.FAILED):
            record.transition_to(EffectState.AUTHORIZED)

        if record.state == EffectState.AUTHORIZED:
            record.transition_to(EffectState.DISPATCHING)

        attempt = self.ledger.record_attempt_start(logical_effect_id)
        dispatched_to_provider = False

        # 6. Dispatch Execution
        try:
            record.transition_to(EffectState.EXECUTING)
            dispatched_to_provider = True

            # Execute tool logic
            if inspect.iscoroutinefunction(executor_fn):
                result = await executor_fn(arguments)
            else:
                result = executor_fn(arguments)

            # 7. Success Path -> Transition to VERIFIED
            self.ledger.record_attempt_success(
                logical_effect_id=logical_effect_id,
                attempt_id=attempt.attempt_id,
                result=result,
            )
            breaker.record_success()

            # If Saga plan is provided and tool has compensator, register compensating step
            if compensation_plan is not None:
                comp = self.compensation_registry.resolve(tool_id, arguments, result)
                if comp:
                    comp_tool, comp_args = comp
                    compensation_plan.add_step(
                        logical_effect_id=logical_effect_id,
                        tool_id=tool_id,
                        forward_arguments=arguments,
                        compensating_tool_id=comp_tool,
                        compensating_arguments=comp_args,
                    )

            return result

        except Exception as exc:
            logger.error(
                "action_broker_execution_failure",
                logical_effect_id=logical_effect_id,
                tool_id=tool_id,
                error=str(exc),
            )
            breaker.record_failure(exc)

            classification = RetryClassifier.classify(
                tool_id=tool_id,
                idempotency_class=resolved_idempotency,
                error=exc,
                dispatched_to_provider=dispatched_to_provider,
            )

            if classification == RetryClassification.AMBIGUOUS_OUTCOME:
                # CRITICAL: Record OUTCOME_UNKNOWN and halt; never blind retry!
                self.ledger.record_attempt_failure(
                    logical_effect_id=logical_effect_id,
                    attempt_id=attempt.attempt_id,
                    error=str(exc),
                    state=EffectState.OUTCOME_UNKNOWN,
                )
                raise AmbiguousOutcomeError(
                    logical_effect_id=logical_effect_id,
                    tool_id=tool_id,
                    attempt_id=attempt.attempt_id,
                    message=(
                        f"Invocation of '{tool_id}' resulted in an ambiguous outcome ({exc}). "
                        "State is OUTCOME_UNKNOWN. Blind retries are strictly prohibited."
                    ),
                ) from exc

            # Standard failure path
            if compensation_plan and len(compensation_plan.steps) > 0:
                record.transition_to(EffectState.COMPENSATION_PENDING)
                try:
                    # Execute reverse rollback
                    await compensation_plan.compensate_all(
                        lambda c_tool, c_args: executor_fn(c_args)
                    )
                    record.transition_to(EffectState.COMPENSATED)
                except CompensationFailedError as comp_err:
                    record.transition_to(
                        EffectState.QUARANTINED, reason=f"Saga rollback failed: {comp_err}"
                    )
                    raise
            else:
                self.ledger.record_attempt_failure(
                    logical_effect_id=logical_effect_id,
                    attempt_id=attempt.attempt_id,
                    error=str(exc),
                    state=EffectState.FAILED,
                )
            raise

        finally:
            # Always release the execution lease upon completion or failure
            await self.lease_manager.release_async(lease.lease_id, worker_id)

    def cancel_effect(
        self,
        logical_effect_id: str,
        reason: str = "Task canceled",
    ) -> EffectState:
        """Handle cancellation race condition for an in-flight or proposed effect."""
        record = self.ledger.get(logical_effect_id)
        if not record:
            return EffectState.FAILED

        if record.state in (EffectState.PROPOSED, EffectState.AUTHORIZED):
            record.transition_to(EffectState.FAILED, reason=reason)
            return EffectState.FAILED

        if record.state == EffectState.DISPATCHING:
            record.transition_to(EffectState.FAILED, reason=reason)
            return EffectState.FAILED

        if record.state == EffectState.EXECUTING:
            # Already sent to provider; outcome is unknown until verified
            record.transition_to(EffectState.OUTCOME_UNKNOWN, reason=reason)
            return EffectState.OUTCOME_UNKNOWN

        return record.state
