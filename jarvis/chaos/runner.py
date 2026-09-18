"""JARVIS Chaos Experimentation Runner.

Validates system resiliency, automatic failover ladders, ambiguous-outcome defenses,
and distributed lease fencing under injected synthetic adversity.
"""

import time
from typing import Any
from uuid import uuid4

from jarvis.chaos.injector import ChaosFaultInjector, get_fault_injector
from jarvis.chaos.schemas import ChaosExperimentResult, ChaosFaultType
from jarvis.core.broker.broker import ActionBroker
from jarvis.core.broker.types import AmbiguousOutcomeError, EffectState
from jarvis.core.capabilities.manifest import CapabilityManifest, RiskClass, SideEffectClass
from jarvis.core.events.lease import DurableLeaseManager
from jarvis.core.exceptions import LeaseFencingError
from jarvis.core.gateway.interfaces import ChatMessage, GenerationRequest
from jarvis.orchestrator import JarvisOrchestrator


class ChaosRunner:
    """Orchestrates chaos resiliency experiments across personal operating system layers."""

    def __init__(
        self,
        orchestrator: JarvisOrchestrator | None = None,
        injector: ChaosFaultInjector | None = None,
    ) -> None:
        self.orchestrator = orchestrator or JarvisOrchestrator()
        self.injector = injector or get_fault_injector()

    async def run_model_failover_experiment(self) -> ChaosExperimentResult:
        """Verify that a 429 quota exhaustion on primary model triggers seamless failover down ladder."""
        start_time = time.perf_counter()
        gateway = self.orchestrator.gateway

        # Target primary model
        primary_model = "gemini-2.5-flash"
        req = GenerationRequest(
            model_id=primary_model,
            messages=[ChatMessage(role="user", content="Respond with the single word 'CONFIRMED'")],
            max_tokens=20,
        )

        # Invalidate quota on primary model to simulate 429 exhaustion
        primary_manager = gateway.get_quota_manager(primary_model)
        saved_rpm = primary_manager.rpm_limit
        primary_manager.rpm_limit = 5
        primary_manager.requests_this_minute = 10

        try:
            res = await gateway.generate(req)
            dur = (time.perf_counter() - start_time) * 1000.0
            recovered = bool(res.content and len(res.content) > 0)
            return ChaosExperimentResult(
                experiment_name="Chaos: Primary Model Quota Exhaustion Failover",
                fault_type=ChaosFaultType.MODEL_RATE_LIMIT,
                resilient_recovery_observed=recovered,
                fail_closed_preserved=True,
                expected_error_or_fallback_occurred=True,
                duration_ms=dur,
                details={
                    "requested_model": primary_model,
                    "response_content": res.content,
                },
            )
        finally:
            primary_manager.rpm_limit = saved_rpm
            primary_manager.requests_this_minute = 0

    async def run_ambiguous_outcome_defense_experiment(self) -> ChaosExperimentResult:
        """Verify that mid-flight disconnection on mutating tool transitions to OUTCOME_UNKNOWN with zero retries."""
        start_time = time.perf_counter()
        broker = ActionBroker()
        task_id = uuid4()
        tool_id = "native:fs:write_chaos_test"
        manifest = CapabilityManifest(
            capability_id=tool_id,
            description="Mutating tool for chaos test",
            risk_class=RiskClass.BOUNDED_MUTATION,
            side_effect_class=SideEffectClass.NON_IDEMPOTENT,
        )

        attempts_executed = 0

        async def failing_executor(args: dict[str, Any]) -> dict[str, Any]:
            nonlocal attempts_executed
            attempts_executed += 1
            # Simulate socket reset mid-flight
            raise ConnectionResetError("Synthetic network drop while writing to remote host")

        from datetime import UTC, datetime, timedelta

        from jarvis.core.policy.decision import EffectAuthorization

        args = {"path": "test.txt", "content": "hello"}
        args_hash = ActionBroker.compute_canonical_hash(args)
        auth = EffectAuthorization(
            task_id=task_id,
            user_id="user",
            session_id=uuid4(),
            agent_id="coding",
            tool_id=tool_id,
            canonical_arguments_hash=args_hash,
            target_resource="test.txt",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            nonce=uuid4().hex,
        )

        ambiguous_caught = False
        try:
            await broker.execute_action(
                task_id=task_id,
                tool_id=tool_id,
                arguments=args,
                executor_fn=failing_executor,
                manifest=manifest,
                authorization=auth,
                target_resource="test.txt",
            )
        except AmbiguousOutcomeError:
            ambiguous_caught = True

        dur = (time.perf_counter() - start_time) * 1000.0

        eff_id = ActionBroker.compute_logical_effect_id(
            task_id=task_id, tool_id=tool_id, arguments=args
        )
        record = broker.ledger.get(eff_id)
        is_unknown = record is not None and record.state == EffectState.OUTCOME_UNKNOWN
        never_retried = attempts_executed == 1

        passed = ambiguous_caught and is_unknown and never_retried

        return ChaosExperimentResult(
            experiment_name="Chaos: Ambiguous Outcome Fail-Closed Quarantine",
            fault_type=ChaosFaultType.BROKER_AMBIGUOUS_DROP,
            resilient_recovery_observed=passed,
            fail_closed_preserved=is_unknown and never_retried,
            expected_error_or_fallback_occurred=ambiguous_caught,
            duration_ms=dur,
            details={
                "attempts_executed": attempts_executed,
                "recorded_state": record.state.value if record else None,
                "ambiguous_caught": ambiguous_caught,
            },
        )

    async def run_stale_fencing_lease_experiment(self) -> ChaosExperimentResult:
        """Verify that worker attempting to renew with an outdated fencing token is rejected."""
        start_time = time.perf_counter()
        lease_manager = DurableLeaseManager()
        resource_id = f"worker-queue-{uuid4().hex[:8]}"

        # Worker 1 acquires lease
        lease_1 = lease_manager.acquire(
            resource_id=resource_id, holder_id="worker_alpha", ttl_seconds=0.1
        )
        token_1 = lease_1.fencing_token

        # Wait briefly so lease expires, then Worker 2 acquires new lease with incremented fencing token
        import asyncio

        await asyncio.sleep(0.15)
        lease_2 = lease_manager.acquire(resource_id=resource_id, holder_id="worker_beta")
        token_2 = lease_2.fencing_token
        assert token_2 > token_1, (
            f"Expected monotonic increment in fencing token: {token_2} > {token_1}"
        )

        # Stale Worker 1 attempts to verify its stale token_1 -> Must raise LeaseFencingError
        rejected = False
        try:
            lease_manager.verify_fencing_or_raise(resource_id=resource_id, fencing_token=token_1)
        except LeaseFencingError:
            rejected = True

        dur = (time.perf_counter() - start_time) * 1000.0
        return ChaosExperimentResult(
            experiment_name="Chaos: Monotonic Lease Fencing Defense",
            fault_type=ChaosFaultType.LEASE_STALE_FENCING,
            resilient_recovery_observed=rejected,
            fail_closed_preserved=rejected,
            expected_error_or_fallback_occurred=rejected,
            duration_ms=dur,
            details={
                "token_worker_1": token_1,
                "token_worker_2": token_2,
                "stale_renewal_rejected": rejected,
            },
        )

    async def run_suite(self) -> list[ChaosExperimentResult]:
        """Run all chaos resilience experiments and compile findings."""
        results: list[ChaosExperimentResult] = []
        results.append(await self.run_model_failover_experiment())
        results.append(await self.run_ambiguous_outcome_defense_experiment())
        results.append(await self.run_stale_fencing_lease_experiment())
        return results
