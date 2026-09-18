"""Comprehensive Unit Tests for JARVIS Durable Event Plane (Milestone 11).

Validates:
1. Causation graph cycle detection, depth limits, and lineage tracing.
2. Distributed mutual-exclusion leases, monotonic fencing tokens, and orphan recovery.
3. Dead Letter Queue (DLQ) quarantine, inspection, and operator replay.
4. Durable Event Bus priority scheduling, wildcard routing, idempotency, and retries.
"""

import asyncio
from pathlib import Path

import pytest

from jarvis.core.broker.types import LeaseAcquisitionError
from jarvis.core.events.bus import DurableEventBus
from jarvis.core.events.dlq import DeadLetterQueue
from jarvis.core.events.lease import DurableLeaseManager, LeaseState
from jarvis.core.events.schemas import (
    CausationGraph,
    EventMessage,
    EventPriority,
)
from jarvis.core.exceptions import (
    CausalCycleError,
    CausalDepthExceededError,
    LeaseFencingError,
)


@pytest.fixture
def temp_db_dir(tmp_path: Path) -> Path:
    """Fixture providing a temporary directory for test databases."""
    db_dir = tmp_path / "event_tests"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir


# ==============================================================================
# 1. Causation Graph & Cycle Defense Tests
# ==============================================================================


def test_causation_child_derivation() -> None:
    """Verify child events correctly inherit correlation_id and increment depth."""
    root = EventMessage(
        event_type="task.created",
        payload={"task": "analyze"},
        priority=EventPriority.NORMAL,
    )
    assert root.depth == 0
    assert root.causation_id is None

    child = root.create_child(
        event_type="policy.evaluated",
        payload={"decision": "ALLOW"},
    )
    assert child.correlation_id == root.correlation_id
    assert child.causation_id == root.event_id
    assert child.depth == 1
    assert child.priority == EventPriority.NORMAL

    grandchild = child.create_child(
        event_type="action.dispatched",
        payload={"tool": "read_file"},
    )
    assert grandchild.correlation_id == root.correlation_id
    assert grandchild.causation_id == child.event_id
    assert grandchild.depth == 2


def test_causation_graph_lineage_and_root_cause() -> None:
    """Verify CausationGraph resolves exact causal lineage and root origin."""
    graph = CausationGraph(max_depth=5)

    ev0 = EventMessage(event_id="ev-0", event_type="origin")
    ev1 = ev0.create_child(event_type="step1")
    ev1.event_id = "ev-1"
    ev2 = ev1.create_child(event_type="step2")
    ev2.event_id = "ev-2"

    graph.record_event(ev0)
    graph.record_event(ev1)
    graph.record_event(ev2)

    lineage = graph.get_lineage("ev-2")
    assert lineage == ["ev-0", "ev-1", "ev-2"]
    assert graph.get_root_cause("ev-2") == "ev-0"
    assert graph.get_root_cause("ev-0") == "ev-0"


def test_causation_graph_depth_limit() -> None:
    """Verify that events exceeding max_depth raise CausalDepthExceededError."""
    graph = CausationGraph(max_depth=3)

    ev = EventMessage(event_type="overflow", depth=4)
    with pytest.raises(CausalDepthExceededError) as exc_info:
        graph.record_event(ev)
    assert "exceeds allowable limit of 3" in str(exc_info.value)


def test_causation_graph_cycle_detection() -> None:
    """Verify that circular causal chains raise CausalCycleError."""
    graph = CausationGraph(max_depth=10)

    ev_a = EventMessage(event_id="node-A", event_type="test")
    ev_b = EventMessage(event_id="node-B", event_type="test", causation_id="node-A")
    ev_c = EventMessage(event_id="node-C", event_type="test", causation_id="node-B")

    graph.record_event(ev_a)
    graph.record_event(ev_b)
    graph.record_event(ev_c)

    # Now simulate a cycle where node-A claims node-C as causation
    ev_a_cycle = EventMessage(event_id="node-A", event_type="test", causation_id="node-C")
    with pytest.raises(CausalCycleError) as exc_info:
        graph.record_event(ev_a_cycle)
    assert "Causal cycle detected" in str(exc_info.value)


# ==============================================================================
# 2. Distributed Leases & Monotonic Fencing Tokens Tests
# ==============================================================================


def test_lease_monotonic_fencing_tokens(temp_db_dir: Path) -> None:
    """Verify that sequential lease acquisitions strictly increment fencing tokens."""
    manager = DurableLeaseManager(db_path=temp_db_dir / "leases.db")
    res_id = "resource:task-100"

    # Worker 1 acquires
    lease1 = manager.acquire(res_id, holder_id="worker-1", ttl_seconds=1.0)
    assert lease1.fencing_token == 1
    assert lease1.state == LeaseState.ACTIVE
    assert manager.validate_fencing_token(res_id, 1) is True

    # Worker 1 releases
    released = manager.release(lease1.lease_id, "worker-1")
    assert released is True

    # Worker 2 acquires: fencing token MUST be strictly greater (token = 2)
    lease2 = manager.acquire(res_id, holder_id="worker-2", ttl_seconds=1.0)
    assert lease2.fencing_token == 2
    assert lease2.state == LeaseState.ACTIVE

    # Validate that worker 1's stale token (1) is now rejected by fencing checks
    assert manager.validate_fencing_token(res_id, 1) is False
    with pytest.raises(LeaseFencingError):
        manager.verify_fencing_or_raise(res_id, fencing_token=1)

    # Worker 2's token is valid
    manager.verify_fencing_or_raise(res_id, fencing_token=2)


def test_lease_mutual_exclusion(temp_db_dir: Path) -> None:
    """Verify that concurrent holders cannot acquire an active lease."""
    manager = DurableLeaseManager(db_path=temp_db_dir / "leases.db")
    res_id = "resource:file-write"

    # Worker 1 acquires for 60s
    manager.acquire(res_id, holder_id="worker-1", ttl_seconds=60.0)

    # Worker 2 tries to acquire same resource
    with pytest.raises(LeaseAcquisitionError) as exc_info:
        manager.acquire(res_id, holder_id="worker-2", ttl_seconds=30.0)
    assert "currently leased by holder 'worker-1'" in str(exc_info.value)


def test_lease_heartbeat_and_reentry(temp_db_dir: Path) -> None:
    """Verify re-entrant lease extension by the same holder and heartbeat renewal."""
    manager = DurableLeaseManager(db_path=temp_db_dir / "leases.db")
    res_id = "resource:long-computation"

    lease = manager.acquire(res_id, holder_id="worker-1", ttl_seconds=10.0)
    original_expiry = lease.expires_at

    # Heartbeat extension
    renewed = manager.heartbeat(lease.lease_id, holder_id="worker-1", extension_seconds=20.0)
    assert renewed.expires_at > original_expiry
    assert renewed.holder_id == "worker-1"

    # Re-entrant acquire by same holder
    reacquired = manager.acquire(res_id, holder_id="worker-1", ttl_seconds=30.0)
    assert reacquired.lease_id == lease.lease_id
    assert reacquired.fencing_token == lease.fencing_token


def test_lease_orphan_recovery(temp_db_dir: Path) -> None:
    """Verify that expired or abandoned leases are reclaimed automatically."""
    manager = DurableLeaseManager(db_path=temp_db_dir / "leases.db")
    res_id = "resource:abandoned"

    # Acquire with tiny TTL
    manager.acquire(res_id, holder_id="worker-crashed", ttl_seconds=0.01)

    # Sleep slightly to ensure expiration
    import time

    time.sleep(0.05)

    reclaimed = manager.reclaim_orphaned_leases()
    assert res_id in reclaimed

    # Now another worker can acquire freely
    new_lease = manager.acquire(res_id, holder_id="worker-recovered", ttl_seconds=30.0)
    assert new_lease.holder_id == "worker-recovered"
    assert new_lease.fencing_token > 1


# ==============================================================================
# 3. Dead Letter Queue (DLQ) Tests
# ==============================================================================


def test_dlq_quarantine_and_replay(temp_db_dir: Path) -> None:
    """Verify quarantine of poison messages, listing, and operator replay."""
    dlq = DeadLetterQueue(db_path=temp_db_dir / "dlq.db")

    poison_event = EventMessage(
        event_type="bad.payload",
        payload={"corrupted": True},
        retry_count=3,
    )

    # Quarantine message
    record = dlq.quarantine(
        event=poison_event,
        reason="Malformed schema in external payload",
        error_trace="ValueError: missing required field 'id'",
    )
    assert record.dlq_id is not None
    assert record.status == "QUARANTINED"
    assert dlq.count(status="QUARANTINED") == 1

    # List records
    records = dlq.list_records(limit=10)
    assert len(records) == 1
    assert records[0].event.event_id == poison_event.event_id

    # Replay message
    replayed = dlq.replay(record.dlq_id)
    assert replayed.event_id == poison_event.event_id
    assert replayed.retry_count == 0  # Retry count must be reset for re-dispatch
    assert dlq.count(status="QUARANTINED") == 0
    assert dlq.count(status="REPLAYED") == 1

    # Purge
    purged_count = dlq.purge()
    assert purged_count >= 1
    assert dlq.count(status="REPLAYED") == 0


# ==============================================================================
# 4. Durable Event Bus Integration & Priority Scheduling Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_event_bus_priority_ordering(temp_db_dir: Path) -> None:
    """Verify that priority 0 (CONTROL) events are popped and processed before priority 1 & 2."""
    bus = DurableEventBus(
        db_path=temp_db_dir / "bus.db",
        num_workers=1,  # Single worker to guarantee deterministic order
    )

    processed_order: list[str] = []

    async def _handler(ev: EventMessage) -> None:
        processed_order.append(ev.event_type)

    bus.subscribe("test.*", _handler)

    msg_low = EventMessage(
        event_type="test.background",
        priority=EventPriority.BACKGROUND,  # 2
    )
    msg_normal = EventMessage(
        event_type="test.normal",
        priority=EventPriority.NORMAL,  # 1
    )
    msg_control = EventMessage(
        event_type="test.interrupt",
        priority=EventPriority.CONTROL,  # 0
    )

    # Publish in reverse order
    await bus.publish(msg_low)
    await bus.publish(msg_normal)
    await bus.publish(msg_control)

    assert bus.queue_size() == 3

    # Start bus and drain
    bus.start()
    await bus.drain(timeout=3.0)
    await bus.stop()

    # Priority 0 must be processed first, then 1, then 2
    assert processed_order == ["test.interrupt", "test.normal", "test.background"]


@pytest.mark.asyncio
async def test_event_bus_wildcard_subscription(temp_db_dir: Path) -> None:
    """Verify wildcard routing patterns (e.g. 'task.*' and '*')."""
    bus = DurableEventBus(db_path=temp_db_dir / "bus.db", num_workers=2)

    task_events: list[str] = []
    all_events: list[str] = []

    async def _task_handler(ev: EventMessage) -> None:
        task_events.append(ev.event_type)

    async def _all_handler(ev: EventMessage) -> None:
        all_events.append(ev.event_type)

    bus.subscribe("task.*", _task_handler)
    bus.subscribe("*", _all_handler)

    await bus.publish(EventMessage(event_type="task.created"))
    await bus.publish(EventMessage(event_type="task.completed"))
    await bus.publish(EventMessage(event_type="system.heartbeat"))

    bus.start()
    await bus.drain(timeout=3.0)
    await bus.stop()

    assert len(task_events) == 2
    assert "task.created" in task_events
    assert "task.completed" in task_events
    assert len(all_events) == 3


@pytest.mark.asyncio
async def test_event_bus_idempotency_deduplication(temp_db_dir: Path) -> None:
    """Verify that duplicate events with the same idempotency_key are processed once."""
    bus = DurableEventBus(db_path=temp_db_dir / "bus.db", num_workers=1)

    executions: list[str] = []

    async def _handler(ev: EventMessage) -> None:
        executions.append(ev.event_id)

    bus.subscribe("idemp.*", _handler)

    idemp_key = "tx-unique-999"
    ev1 = EventMessage(
        event_id="ev-first",
        event_type="idemp.action",
        idempotency_key=idemp_key,
    )
    ev2 = EventMessage(
        event_id="ev-duplicate",
        event_type="idemp.action",
        idempotency_key=idemp_key,
    )

    # Publish first event
    accepted1 = await bus.publish(ev1)
    assert accepted1 is True

    bus.start()
    await bus.drain(timeout=2.0)

    # Publish duplicate event after first is completed
    accepted2 = await bus.publish(ev2)
    assert accepted2 is False  # Must be rejected by idempotency check

    await bus.drain(timeout=2.0)
    await bus.stop()

    assert len(executions) == 1
    assert executions[0] == "ev-first"


@pytest.mark.asyncio
async def test_event_bus_retry_and_dlq_quarantine(temp_db_dir: Path) -> None:
    """Verify that transient failures retry with backoff and exhaust into DLQ."""
    bus = DurableEventBus(
        db_path=temp_db_dir / "bus.db",
        default_max_retries=2,
        num_workers=1,
    )

    attempt_counts: list[int] = []

    async def _failing_handler(ev: EventMessage) -> None:
        attempt_counts.append(ev.retry_count)
        raise RuntimeError(f"Deliberate transient failure at attempt {ev.retry_count}")

    bus.subscribe("fail.*", _failing_handler)

    failing_event = EventMessage(
        event_type="fail.task",
        max_retries=2,
    )
    await bus.publish(failing_event)

    bus.start()
    # Wait for initial attempt + 2 retries to execute and exhaust into DLQ
    await asyncio.sleep(1.0)
    await bus.drain(timeout=3.0)
    await bus.stop()

    # Should have attempted 3 times: initial (0), retry 1 (1), retry 2 (2)
    assert len(attempt_counts) >= 3

    # Check DLQ contains the quarantined event
    dlq_records = bus.dlq.list_records()
    assert len(dlq_records) == 1
    assert dlq_records[0].event.event_id == failing_event.event_id
    assert "Deliberate transient failure" in dlq_records[0].reason


@pytest.mark.asyncio
async def test_event_bus_causal_loop_quarantine(temp_db_dir: Path) -> None:
    """Verify that an event with causal depth exceeding limit is routed directly to DLQ."""
    bus = DurableEventBus(
        db_path=temp_db_dir / "bus.db",
        max_depth=3,
        num_workers=1,
        raise_on_causal_violation=False,
    )

    loop_event = EventMessage(
        event_type="loop.event",
        depth=5,  # Exceeds max_depth 3
    )

    accepted = await bus.publish(loop_event)
    assert accepted is False
    assert bus.queue_size() == 0

    # DLQ must contain the quarantined message
    dlq_records = bus.dlq.list_records()
    assert len(dlq_records) == 1
    assert "Causal violation" in dlq_records[0].reason
