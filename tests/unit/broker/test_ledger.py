"""Unit tests for IdempotencyLedger and EffectRecord caching."""

from uuid import uuid4

from jarvis.core.broker.ledger import IdempotencyLedger
from jarvis.core.broker.types import EffectState, IdempotencyClass


def test_ledger_record_proposal() -> None:
    """Verify recording proposals creates pending ledger entries."""
    ledger = IdempotencyLedger()
    task_id = uuid4()
    record = ledger.record_proposal(
        logical_effect_id="eff_prop_1",
        task_id=task_id,
        tool_id="filesystem.write",
        idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
        canonical_arguments_hash="hash_1",
        target_resource="/path/to/file.txt",
    )
    assert record.logical_effect_id == "eff_prop_1"
    assert record.state == EffectState.PROPOSED
    assert ledger.is_duplicate("eff_prop_1") is False

    # Calling again returns existing record
    record2 = ledger.record_proposal(
        logical_effect_id="eff_prop_1",
        task_id=task_id,
        tool_id="filesystem.write",
        idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
        canonical_arguments_hash="hash_1",
    )
    assert record2 is record


def test_ledger_attempt_lifecycle_and_caching() -> None:
    """Verify attempts are tracked and verified results are cached."""
    ledger = IdempotencyLedger()
    task_id = uuid4()
    record = ledger.record_proposal(
        logical_effect_id="eff_cache_1",
        task_id=task_id,
        tool_id="database.insert",
        idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
        canonical_arguments_hash="hash_db",
    )
    record.transition_to(EffectState.AUTHORIZED)
    record.transition_to(EffectState.DISPATCHING)

    attempt = ledger.record_attempt_start("eff_cache_1")
    assert attempt.attempt_number == 1
    assert attempt.status == EffectState.DISPATCHING

    # Record success
    record.transition_to(EffectState.EXECUTING)
    result_data = {"rows_inserted": 1, "id": 42}
    ledger.record_attempt_success("eff_cache_1", attempt.attempt_id, result_data)

    assert record.state == EffectState.VERIFIED
    assert record.cached_result == result_data
    assert ledger.is_duplicate("eff_cache_1") is True


def test_ledger_list_by_task() -> None:
    """Verify filtering records by task ID."""
    ledger = IdempotencyLedger()
    task1 = uuid4()
    task2 = uuid4()

    ledger.record_proposal("eff_t1_a", task1, "tool.a", IdempotencyClass.IDEMPOTENT, "h1")
    ledger.record_proposal("eff_t1_b", task1, "tool.b", IdempotencyClass.NON_IDEMPOTENT, "h2")
    ledger.record_proposal("eff_t2_a", task2, "tool.c", IdempotencyClass.IDEMPOTENT, "h3")

    t1_records = ledger.list_by_task(task1)
    assert len(t1_records) == 2
    t2_records = ledger.list_by_task(task2)
    assert len(t2_records) == 1
