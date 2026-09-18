"""JARVIS Local In-Memory Priority Queue and Event Bus Abstraction.

Implements priority-based scheduling, distributed lease simulation, causal loop protection, and DLQ.
"""

import asyncio
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from jarvis.core.events.schemas import EventMessage
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "EventMessage",
    "InMemoryEventQueue",
    "QueueConsumer",
    "QueueProducer",
]


class QueueProducer(ABC):
    """Abstract interface for publishing events."""

    @abstractmethod
    async def publish(self, message: EventMessage) -> None:
        """Publish an event to the queue."""
        pass


class QueueConsumer(ABC):
    """Abstract interface for consuming events."""

    @abstractmethod
    async def consume(self, timeout: float | None = None) -> EventMessage | None:
        """Consume the next highest-priority message from the queue."""
        pass

    @abstractmethod
    async def ack(self, event_id: str) -> None:
        """Acknowledge successful message processing."""
        pass

    @abstractmethod
    async def nack(self, event_id: str, reason: str, requeue: bool = True) -> None:
        """Reject message processing. If requeue=False or max retries exceeded, route to DLQ."""
        pass


class InMemoryEventQueue(QueueProducer, QueueConsumer):
    """Async priority queue implementation supporting causal depth limits and DLQ."""

    def __init__(self, max_depth: int = 5, max_retries: int = 3) -> None:
        self.max_depth = max_depth
        self.max_retries = max_retries
        # asyncio.PriorityQueue stores tuples: (priority, counter, message)
        self._queue: asyncio.PriorityQueue[tuple[int, int, EventMessage]] = asyncio.PriorityQueue()
        self._in_flight: dict[str, EventMessage] = {}
        self._dlq: list[dict[str, Any]] = []
        self._counter: int = 0
        self._lock = asyncio.Lock()

    async def publish(self, message: EventMessage) -> None:
        """Publish message. Rejects if causal depth exceeds maximum budget."""
        if message.depth > self.max_depth:
            logger.error(
                "causal_loop_detected",
                event_id=message.event_id,
                depth=message.depth,
                max_depth=self.max_depth,
            )
            # Route immediately to DLQ
            async with self._lock:
                self._dlq.append(
                    {
                        "message": message.model_dump(mode="json"),
                        "reason": f"Causal loop defense: depth {message.depth} exceeded max {self.max_depth}",
                        "failed_at": datetime.now(UTC).isoformat(),
                    }
                )
            return

        async with self._lock:
            self._counter += 1
            # Priority 0 is popped first by PriorityQueue
            await self._queue.put((message.priority, self._counter, message))
            logger.debug(
                "event_published",
                event_id=message.event_id,
                event_type=message.event_type,
                priority=message.priority,
            )

    async def consume(self, timeout: float | None = None) -> EventMessage | None:
        """Fetch next highest priority message."""
        try:
            if timeout is not None:
                _, _, message = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            else:
                _, _, message = await self._queue.get()

            async with self._lock:
                self._in_flight[message.event_id] = message
            return message
        except TimeoutError:
            return None

    async def ack(self, event_id: str) -> None:
        """Acknowledge successful completion of in-flight message."""
        async with self._lock:
            if event_id in self._in_flight:
                del self._in_flight[event_id]
                self._queue.task_done()
                logger.debug("event_acknowledged", event_id=event_id)

    async def nack(self, event_id: str, reason: str, requeue: bool = True) -> None:
        """Reject message. Routes to DLQ if max retries reached or requeue is False."""
        async with self._lock:
            message = self._in_flight.pop(event_id, None)
            if not message:
                return

            self._queue.task_done()

            if requeue and message.retry_count < self.max_retries:
                message.retry_count += 1
                self._counter += 1
                logger.warning(
                    "event_requeued",
                    event_id=event_id,
                    retry_count=message.retry_count,
                    reason=reason,
                )
                await self._queue.put((message.priority, self._counter, message))
            else:
                logger.error(
                    "event_routed_to_dlq",
                    event_id=event_id,
                    retries=message.retry_count,
                    reason=reason,
                )
                self._dlq.append(
                    {
                        "message": message.model_dump(mode="json"),
                        "reason": reason,
                        "retries": message.retry_count,
                        "failed_at": datetime.now(UTC).isoformat(),
                    }
                )

    async def get_dlq_messages(self) -> list[dict[str, Any]]:
        """Return all messages currently residing in the Dead Letter Queue."""
        async with self._lock:
            return list(self._dlq)

    def queue_size(self) -> int:
        """Return the current number of pending queue items."""
        return self._queue.qsize()
