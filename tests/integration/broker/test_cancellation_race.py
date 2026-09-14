"""Integration test for cancellation race conditions and recovery."""

from uuid import uuid4

from jarvis.core.broker.broker import ActionBroker
from jarvis.core.broker.ledger import EffectRecord
from jarvis.core.broker.types import EffectState, IdempotencyClass


def _assert_record_state(record: EffectRecord, expected: EffectState) -> None:
    assert record.state == expected


def test_cancel_proposed_and_dispatching_effect() -> None:
    """Canceling an effect in PROPOSED or DISPATCHING cleanly transitions to FAILED."""
    broker = ActionBroker()
    task_id = uuid4()
    args = {"query": "find large files"}
    eff_id = broker.compute_logical_effect_id(task_id, "find_tool", args)

    record = broker.ledger.record_proposal(
        logical_effect_id=eff_id,
        task_id=task_id,
        tool_id="find_tool",
        idempotency_class=IdempotencyClass.IDEMPOTENT,
        canonical_arguments_hash=broker.compute_canonical_hash(args),
    )
    _assert_record_state(record, EffectState.PROPOSED)

    new_state = broker.cancel_effect(eff_id, reason="User clicked stop")
    assert new_state == EffectState.FAILED
    _assert_record_state(record, EffectState.FAILED)


def test_cancel_executing_effect_transitions_to_outcome_unknown() -> None:
    """Canceling while in EXECUTING must transition to OUTCOME_UNKNOWN rather than FAILED.

    This prevents ghost mutations where downstream provider completed work but JARVIS assumed failed.
    """
    broker = ActionBroker()
    task_id = uuid4()
    args = {"target": "user@domain.com"}
    eff_id = broker.compute_logical_effect_id(task_id, "email_tool", args)

    record = broker.ledger.record_proposal(
        logical_effect_id=eff_id,
        task_id=task_id,
        tool_id="email_tool",
        idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
        canonical_arguments_hash=broker.compute_canonical_hash(args),
    )
    record.transition_to(EffectState.AUTHORIZED)
    record.transition_to(EffectState.DISPATCHING)
    record.transition_to(EffectState.EXECUTING)

    new_state = broker.cancel_effect(eff_id, reason="Parent task timed out")
    assert new_state == EffectState.OUTCOME_UNKNOWN
    _assert_record_state(record, EffectState.OUTCOME_UNKNOWN)
