"""Tests for JARVIS In-Memory Priority Queue and Event Bus."""

import pytest

from jarvis.core.queue.in_memory import EventMessage, InMemoryEventQueue


@pytest.mark.asyncio
async def test_queue_priority_ordering() -> None:
    """Verify that priority 0 (high) messages are consumed before priority 1 (normal)."""
    q = InMemoryEventQueue()

    msg_low = EventMessage(event_type="background_sync", priority=2)
    msg_normal = EventMessage(event_type="model_request", priority=1)
    msg_high = EventMessage(event_type="voice_interrupt", priority=0)

    # Publish in reverse priority order
    await q.publish(msg_low)
    await q.publish(msg_normal)
    await q.publish(msg_high)

    assert q.queue_size() == 3

    # High priority must be popped first
    first = await q.consume()
    assert first is not None
    assert first.event_type == "voice_interrupt"
    await q.ack(first.event_id)

    # Normal priority second
    second = await q.consume()
    assert second is not None
    assert second.event_type == "model_request"
    await q.ack(second.event_id)

    # Low priority third
    third = await q.consume()
    assert third is not None
    assert third.event_type == "background_sync"
    await q.ack(third.event_id)


@pytest.mark.asyncio
async def test_queue_nack_and_dlq() -> None:
    """Verify that messages exceeding max retries are routed to the DLQ."""
    q = InMemoryEventQueue(max_retries=2)
    msg = EventMessage(event_type="failing_task")
    await q.publish(msg)

    # Attempt 1: consume and nack
    item1 = await q.consume()
    assert item1 is not None
    assert item1.retry_count == 0
    await q.nack(item1.event_id, reason="Temporary failure 1", requeue=True)

    # Attempt 2: consume and nack
    item2 = await q.consume()
    assert item2 is not None
    assert item2.retry_count == 1
    await q.nack(item2.event_id, reason="Temporary failure 2", requeue=True)

    # Attempt 3: consume and nack -> reaches max retries (2), routes to DLQ
    item3 = await q.consume()
    assert item3 is not None
    assert item3.retry_count == 2
    await q.nack(item3.event_id, reason="Fatal failure 3", requeue=True)

    # Queue should now be empty
    empty_item = await q.consume(timeout=0.1)
    assert empty_item is None

    # Verify DLQ contains the message
    dlq = await q.get_dlq_messages()
    assert len(dlq) == 1
    assert dlq[0]["reason"] == "Fatal failure 3"
    assert dlq[0]["retries"] == 2


@pytest.mark.asyncio
async def test_causal_loop_defense() -> None:
    """Verify that messages with depth exceeding max_depth are rejected into DLQ."""
    q = InMemoryEventQueue(max_depth=3)
    loop_msg = EventMessage(
        event_type="proactive_event",
        depth=4,  # Exceeds max_depth of 3
        causation_id="cause-123",
    )

    await q.publish(loop_msg)

    # Message must not be in the active queue
    assert q.queue_size() == 0

    # Message must be in the DLQ
    dlq = await q.get_dlq_messages()
    assert len(dlq) == 1
    assert "Causal loop defense" in dlq[0]["reason"]
