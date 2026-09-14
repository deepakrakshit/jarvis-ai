"""Integration test for Ambiguous-Outcome Defense and Non-Idempotent Tool Re-entry Prevention.

CRITICAL ACCEPTANCE GATE:
Simulate timeout immediately after provider-side acceptance.
JARVIS must not blindly execute the same non-idempotent operation again
(ARCHITECTURE.md Layer 14, Failure Modes #3 & #46).
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.broker.broker import ActionBroker
from jarvis.core.broker.types import (
    AmbiguousOutcomeError,
    EffectState,
    IdempotencyClass,
)
from jarvis.core.capabilities.manifest import (
    CapabilityManifest,
    RiskClass,
    SideEffectClass,
    ToolType,
)
from jarvis.core.policy.decision import EffectAuthorization


@pytest.mark.asyncio
async def test_ambiguous_outcome_blocks_blind_retry() -> None:
    """Exit Gate Verification:

    1. Invoke non-idempotent tool.
    2. Simulate network timeout immediately after provider accepts request.
    3. Action Broker moves state to OUTCOME_UNKNOWN and raises AmbiguousOutcomeError.
    4. Subsequent execution attempts with identical parameters are strictly blocked
       from calling the provider again.
    """
    broker = ActionBroker()
    task_id = uuid4()
    args = {"recipient": "alice@example.com", "amount": 100.0, "currency": "USD"}
    tool_id = "external:wire:transfer"

    non_idempotent_manifest = CapabilityManifest(
        capability_id=tool_id,
        description="Transfers funds to external bank",
        risk_class=RiskClass.DANGEROUS,
        side_effect_class=SideEffectClass.NON_IDEMPOTENT,
        tool_type=ToolType.NATIVE,
    )

    args_hash = broker.compute_canonical_hash(args)
    provider_invocation_count = 0

    def mock_provider(params: dict[str, Any]) -> Any:
        nonlocal provider_invocation_count
        provider_invocation_count += 1
        # Simulate: Provider accepted wire transfer, but connection dropped before response returned
        raise TimeoutError("504 Gateway Timeout: provider took too long to respond")

    auth = EffectAuthorization(
        task_id=task_id,
        user_id="operator_1",
        session_id=uuid4(),
        agent_id="finance_agent",
        tool_id=tool_id,
        canonical_arguments_hash=args_hash,
        target_resource="bank://transfers/external",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        nonce="nonce_tx_001",
    )

    # 1. First execution attempt -> should fail with AmbiguousOutcomeError
    with pytest.raises(AmbiguousOutcomeError) as exc_info:
        await broker.execute_action(
            task_id=task_id,
            tool_id=tool_id,
            arguments=args,
            executor_fn=mock_provider,
            manifest=non_idempotent_manifest,
            authorization=auth,
        )

    assert "OUTCOME_UNKNOWN" in str(exc_info.value)
    assert provider_invocation_count == 1

    # 2. Verify ledger state is OUTCOME_UNKNOWN
    logical_effect_id = broker.compute_logical_effect_id(task_id, tool_id, args)
    record = broker.ledger.get(logical_effect_id)
    assert record is not None
    assert record.state == EffectState.OUTCOME_UNKNOWN
    assert record.idempotency_class == IdempotencyClass.NON_IDEMPOTENT

    # 3. Second execution attempt: simulate an agent or retry loop blindly re-invoking
    # Create fresh authorization to prove that even with new auth, Action Broker blocks duplicate
    auth2 = EffectAuthorization(
        task_id=task_id,
        user_id="operator_1",
        session_id=uuid4(),
        agent_id="finance_agent",
        tool_id=tool_id,
        canonical_arguments_hash=args_hash,
        target_resource="bank://transfers/external",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        nonce="nonce_tx_002",
    )

    with pytest.raises(AmbiguousOutcomeError) as exc_retry:
        await broker.execute_action(
            task_id=task_id,
            tool_id=tool_id,
            arguments=args,
            executor_fn=mock_provider,
            manifest=non_idempotent_manifest,
            authorization=auth2,
        )

    assert "Previous attempt ended in OUTCOME_UNKNOWN" in str(exc_retry.value)
    assert "External verification required" in str(exc_retry.value)

    # CRITICAL: The provider was NOT called a second time!
    # Provider invocation count remains strictly 1!
    assert provider_invocation_count == 1


@pytest.mark.asyncio
async def test_idempotent_with_key_allows_safe_retry() -> None:
    """Compare with IDEMPOTENT_WITH_KEY tool: can safely retry with identical logical_effect_id."""
    broker = ActionBroker()
    task_id = uuid4()
    args = {"charge_id": "ch_999", "amount": 50}
    tool_id = "payment:stripe:charge"

    idempotent_key_manifest = CapabilityManifest(
        capability_id=tool_id,
        description="Charges credit card with idempotency key",
        risk_class=RiskClass.BOUNDED_MUTATION,
        side_effect_class=SideEffectClass.IDEMPOTENT,
        tool_type=ToolType.NATIVE,
    )

    call_count = 0

    def mock_stripe(params: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TimeoutError("Network blip on first attempt")
        return {"charge_id": params["charge_id"], "status": "succeeded"}

    auth1 = EffectAuthorization(
        task_id=task_id,
        user_id="customer",
        session_id=uuid4(),
        agent_id="billing_agent",
        tool_id=tool_id,
        canonical_arguments_hash=broker.compute_canonical_hash(args),
        target_resource="stripe://charges/ch_999",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        nonce="nonce_stripe_1",
    )

    # First attempt fails with transient TimeoutError (classified as RETRYABLE_SAFE)
    with pytest.raises(TimeoutError):
        await broker.execute_action(
            task_id=task_id,
            tool_id=tool_id,
            arguments=args,
            executor_fn=mock_stripe,
            manifest=idempotent_key_manifest,
            authorization=auth1,
            idempotency_class=IdempotencyClass.IDEMPOTENT_WITH_KEY,
        )

    assert call_count == 1

    # Second attempt succeeds because IDEMPOTENT_WITH_KEY is safe to retry
    auth2 = EffectAuthorization(
        task_id=task_id,
        user_id="customer",
        session_id=uuid4(),
        agent_id="billing_agent",
        tool_id=tool_id,
        canonical_arguments_hash=broker.compute_canonical_hash(args),
        target_resource="stripe://charges/ch_999",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        nonce="nonce_stripe_2",
    )

    result = await broker.execute_action(
        task_id=task_id,
        tool_id=tool_id,
        arguments=args,
        executor_fn=mock_stripe,
        manifest=idempotent_key_manifest,
        authorization=auth2,
        idempotency_class=IdempotencyClass.IDEMPOTENT_WITH_KEY,
    )

    assert call_count == 2
    assert result == {"charge_id": "ch_999", "status": "succeeded"}
