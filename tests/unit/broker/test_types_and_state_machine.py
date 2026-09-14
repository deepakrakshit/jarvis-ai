"""Unit tests for Action Broker types, enums, and EffectState transitions."""

from uuid import uuid4

import pytest

from jarvis.core.broker.ledger import EffectRecord
from jarvis.core.broker.types import (
    EffectState,
    IdempotencyClass,
    InvalidEffectStateTransitionError,
    RetryClassification,
)


def _assert_record_state(record: EffectRecord, expected: EffectState) -> None:
    assert record.state == expected


def test_idempotency_taxonomies_and_retry_classes() -> None:
    """Verify all idempotency taxonomy and retry classification members exist."""
    assert IdempotencyClass.IDEMPOTENT == "IDEMPOTENT"
    assert IdempotencyClass.IDEMPOTENT_WITH_KEY == "IDEMPOTENT_WITH_KEY"
    assert IdempotencyClass.NON_IDEMPOTENT == "NON_IDEMPOTENT"
    assert IdempotencyClass.TRANSACTIONAL == "TRANSACTIONAL"
    assert IdempotencyClass.UNKNOWN == "UNKNOWN"

    assert RetryClassification.RETRYABLE_SAFE == "RETRYABLE_SAFE"
    assert RetryClassification.NON_RETRYABLE_FATAL == "NON_RETRYABLE_FATAL"
    assert RetryClassification.AMBIGUOUS_OUTCOME == "AMBIGUOUS_OUTCOME"
    assert RetryClassification.REQUIRES_VERIFICATION == "REQUIRES_VERIFICATION"


def test_valid_effect_state_machine_happy_path() -> None:
    """Test standard forward lifecycle: PROPOSED -> AUTHORIZED -> DISPATCHING -> EXECUTING -> VERIFIED."""
    record = EffectRecord(
        logical_effect_id="eff_12345",
        task_id=uuid4(),
        tool_id="filesystem.write",
        idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
        canonical_arguments_hash="hash_abc",
    )
    _assert_record_state(record, EffectState.PROPOSED)

    record.transition_to(EffectState.AUTHORIZED)
    _assert_record_state(record, EffectState.AUTHORIZED)

    record.transition_to(EffectState.DISPATCHING)
    _assert_record_state(record, EffectState.DISPATCHING)

    record.transition_to(EffectState.EXECUTING)
    _assert_record_state(record, EffectState.EXECUTING)

    record.transition_to(EffectState.VERIFIED)
    _assert_record_state(record, EffectState.VERIFIED)


def test_effect_state_machine_ambiguous_outcome_to_quarantined() -> None:
    """Test transition through OUTCOME_UNKNOWN into QUARANTINED."""
    record = EffectRecord(
        logical_effect_id="eff_67890",
        task_id=uuid4(),
        tool_id="bank.transfer",
        idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
        canonical_arguments_hash="hash_def",
        state=EffectState.EXECUTING,
    )
    record.transition_to(EffectState.OUTCOME_UNKNOWN)
    _assert_record_state(record, EffectState.OUTCOME_UNKNOWN)

    record.transition_to(EffectState.QUARANTINED, reason="Timeout after provider accepted payload")
    _assert_record_state(record, EffectState.QUARANTINED)
    assert record.quarantine_reason == "Timeout after provider accepted payload"


def test_effect_state_machine_compensation_path() -> None:
    """Test transitions into COMPENSATION_PENDING and COMPENSATED."""
    record = EffectRecord(
        logical_effect_id="eff_saga1",
        task_id=uuid4(),
        tool_id="multi.step",
        idempotency_class=IdempotencyClass.TRANSACTIONAL,
        canonical_arguments_hash="hash_saga",
        state=EffectState.EXECUTING,
    )
    record.transition_to(EffectState.COMPENSATION_PENDING)
    _assert_record_state(record, EffectState.COMPENSATION_PENDING)

    record.transition_to(EffectState.COMPENSATED)
    _assert_record_state(record, EffectState.COMPENSATED)


def test_illegal_state_transitions_raise_error() -> None:
    """Ensure invalid transitions are rejected deterministically."""
    record = EffectRecord(
        logical_effect_id="eff_illegal",
        task_id=uuid4(),
        tool_id="test.tool",
        idempotency_class=IdempotencyClass.IDEMPOTENT,
        canonical_arguments_hash="hash_test",
        state=EffectState.PROPOSED,
    )

    # Cannot skip directly to VERIFIED or EXECUTING from PROPOSED
    with pytest.raises(InvalidEffectStateTransitionError):
        record.transition_to(EffectState.VERIFIED)

    with pytest.raises(InvalidEffectStateTransitionError):
        record.transition_to(EffectState.EXECUTING)

    # Cannot transition out of terminal states
    record.state = EffectState.VERIFIED
    with pytest.raises(InvalidEffectStateTransitionError):
        record.transition_to(EffectState.EXECUTING)

    with pytest.raises(InvalidEffectStateTransitionError):
        record.transition_to(EffectState.PROPOSED)
