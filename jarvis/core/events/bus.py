"""JARVIS Durable Event Bus, Priority Dispatcher, and Worker Fabric.

Implements ARCHITECTURE.md Layer 12: Asynchronous event plane with priority scheduling,
durable SQLite persistence, distributed lease protection, exponential backoff retries,
causation loop defense, and poison message quarantine.
"""

import asyncio
import fnmatch
import random
import sqlite3
import traceback
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from jarvis.core.config import get_settings
from jarvis.core.events.dlq import DeadLetterQueue
from jarvis.core.events.lease import DurableLeaseManager
from jarvis.core.events.schemas import (
    CausationGraph,
    EventMessage,
)
from jarvis.core.exceptions import (
    CausalCycleError,
    CausalDepthExceededError,
)
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

EventHandler = Callable[[EventMessage], Awaitable[None]]


class DurableEventBus:
    """Enterprise-grade durable event bus with priority lanes, causation tracking, and DLQ."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        max_depth: int = 5,
        default_max_retries: int = 3,
        num_workers: int = 2,
        raise_on_causal_violation: bool = False,
    ) -> None:
        self.max_depth = max_depth
        self.default_max_retries = default_max_retries
        self.num_workers = num_workers
        self.raise_on_causal_violation = raise_on_causal_violation

        self._db_path: Path | None = None
        if db_path is not None:
            self._db_path = Path(db_path) if isinstance(db_path, str) else db_path
        else:
            settings = get_settings()
            self._db_path = settings.DATA_DIR / "events.db"

        # Shared components
        self.causation_graph = CausationGraph(max_depth=max_depth)
        self.dlq = DeadLetterQueue(
            db_path=self._db_path.parent / "dlq.db" if self._db_path else None
        )
        self.lease_manager = DurableLeaseManager(
            db_path=self._db_path.parent / "leases.db" if self._db_path else None
        )

        # Priority queue stores tuples: (priority: int, sequence: int, event: EventMessage)
        self._queue: asyncio.PriorityQueue[tuple[int, int, EventMessage]] = asyncio.PriorityQueue()
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._in_flight: dict[str, EventMessage] = {}
        self._processed_idempotency_keys: set[str] = set()

        self._counter: int = 0
        self._sync_lock = Lock()
        self._async_lock = asyncio.Lock()
        self._workers: list[asyncio.Task[None]] = []
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._running: bool = False

        if self._db_path:
            self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite persistence tables."""
        if not self._db_path:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self._db_path), timeout=30.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS durable_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    causation_id TEXT,
                    depth INTEGER NOT NULL,
                    priority INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    idempotency_key TEXT,
                    event_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    processed_at TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_corr
                ON durable_events(correlation_id);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_idemp
                ON durable_events(idempotency_key);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_status
                ON durable_events(status);
                """
            )
            conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        if not self._db_path:
            raise RuntimeError("Database path not configured")
        conn = sqlite3.connect(str(self._db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def subscribe(self, pattern: str, handler: EventHandler) -> None:
        """Register an async event handler for an event type pattern (supports fnmatch e.g. 'task.*')."""
        with self._sync_lock:
            if pattern not in self._subscribers:
                self._subscribers[pattern] = []
            self._subscribers[pattern].append(handler)
            logger.debug("event_handler_subscribed", pattern=pattern)

    def unsubscribe(self, pattern: str, handler: EventHandler) -> bool:
        """Unregister an async event handler."""
        with self._sync_lock:
            if pattern in self._subscribers and handler in self._subscribers[pattern]:
                self._subscribers[pattern].remove(handler)
                return True
            return False

    async def publish(self, event: EventMessage) -> bool:
        """Publish an event to the durable priority bus.

        Returns True if accepted and enqueued, False if deduplicated or quarantined.
        """
        # 1. Causal validation (Depth + Cycle Defense)
        try:
            self.causation_graph.record_event(event)
        except (CausalDepthExceededError, CausalCycleError) as err:
            reason = f"Causal violation: {err}"
            logger.error("causal_defense_triggered", reason=reason, event_id=event.event_id)
            self.dlq.quarantine(event=event, reason=reason)
            if self.raise_on_causal_violation:
                raise
            return False

        # 2. Idempotency validation
        if event.idempotency_key:
            with self._sync_lock:
                if event.idempotency_key in self._processed_idempotency_keys:
                    logger.info(
                        "event_deduplicated_memory",
                        idempotency_key=event.idempotency_key,
                        event_id=event.event_id,
                    )
                    return False

            if self._db_path:
                with self._get_connection() as conn:
                    row = conn.execute(
                        "SELECT status FROM durable_events WHERE idempotency_key = ? AND status = 'COMPLETED';",
                        (event.idempotency_key,),
                    ).fetchone()
                    if row:
                        logger.info(
                            "event_deduplicated_sqlite",
                            idempotency_key=event.idempotency_key,
                            event_id=event.event_id,
                        )
                        return False

        # 3. Persistent recording
        if self._db_path:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO durable_events (
                        event_id, event_type, correlation_id, causation_id,
                        depth, priority, source, idempotency_key, event_json,
                        status, published_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        event.event_id,
                        event.event_type,
                        event.correlation_id,
                        event.causation_id,
                        event.depth,
                        event.priority,
                        event.source,
                        event.idempotency_key,
                        event.model_dump_json(),
                        "PENDING",
                        datetime.now(UTC).isoformat(),
                    ),
                )
                conn.commit()

        # 4. Enqueue into priority queue
        async with self._async_lock:
            self._counter += 1
            await self._queue.put((event.priority, self._counter, event))

        logger.debug(
            "event_published",
            event_id=event.event_id,
            event_type=event.event_type,
            priority=event.priority,
            depth=event.depth,
        )
        return True

    def _find_matching_handlers(self, event_type: str) -> list[EventHandler]:
        handlers: list[EventHandler] = []
        with self._sync_lock:
            for pattern, pattern_handlers in self._subscribers.items():
                if fnmatch.fnmatch(event_type, pattern):
                    handlers.extend(pattern_handlers)
        return handlers

    async def _process_event(self, event: EventMessage, worker_id: str) -> None:
        """Execute handlers for a single consumed event under a distributed lease."""
        resource_id = f"event:{event.event_id}"
        lease = None
        try:
            # Acquire lease to safeguard execution
            lease = await self.lease_manager.acquire_async(
                resource_id=resource_id,
                holder_id=worker_id,
                ttl_seconds=event.ttl_seconds or 30.0,
            )
        except Exception as lease_err:
            logger.warning(
                "event_lease_acquisition_failed",
                event_id=event.event_id,
                error=str(lease_err),
            )
            # Re-enqueue if lease conflict
            async with self._async_lock:
                self._counter += 1
                await self._queue.put((event.priority, self._counter, event))
            return

        handlers = self._find_matching_handlers(event.event_type)
        if not handlers:
            # No handlers registered: mark completed
            await self._mark_completed(event)
            if lease:
                await self.lease_manager.release_async(lease.lease_id, worker_id)
            return

        # Execute handlers
        handler_success = True
        last_error = None
        last_trace = None

        for handler in handlers:
            try:
                await handler(event)
            except Exception as exc:
                handler_success = False
                last_error = str(exc)
                last_trace = traceback.format_exc()
                logger.error(
                    "event_handler_failed",
                    event_id=event.event_id,
                    handler=getattr(handler, "__name__", str(handler)),
                    error=last_error,
                )
                break

        if handler_success:
            await self._mark_completed(event)
        else:
            await self._handle_failure(event, last_error or "Unknown failure", last_trace)

        if lease:
            try:
                await self.lease_manager.release_async(lease.lease_id, worker_id)
            except Exception as rel_err:
                logger.debug("lease_release_failed", error=str(rel_err))

    async def _mark_completed(self, event: EventMessage) -> None:
        """Mark event successfully processed."""
        if event.idempotency_key:
            with self._sync_lock:
                self._processed_idempotency_keys.add(event.idempotency_key)

        if self._db_path:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE durable_events
                    SET status = 'COMPLETED', processed_at = ?
                    WHERE event_id = ?;
                    """,
                    (datetime.now(UTC).isoformat(), event.event_id),
                )
                conn.commit()

        logger.debug("event_completed", event_id=event.event_id)

    async def _handle_failure(
        self, event: EventMessage, error_reason: str, error_trace: str | None
    ) -> None:
        """Handle execution failure via exponential backoff retry or DLQ quarantine."""
        max_retries = (
            event.max_retries if event.max_retries is not None else self.default_max_retries
        )
        if event.retry_count < max_retries:
            event.retry_count += 1
            # Exponential backoff with jitter: 0.1 * 2^retry + jitter
            backoff = min(0.1 * (2**event.retry_count) + random.uniform(0.01, 0.05), 5.0)
            logger.warning(
                "event_requeued_with_backoff",
                event_id=event.event_id,
                retry_count=event.retry_count,
                backoff_seconds=backoff,
                reason=error_reason,
            )

            # Asynchronously wait for backoff window, then re-put
            async def _delayed_retry() -> None:
                await asyncio.sleep(backoff)
                async with self._async_lock:
                    self._counter += 1
                    await self._queue.put((event.priority, self._counter, event))

            retry_task = asyncio.create_task(_delayed_retry())
            self._background_tasks.add(retry_task)
            retry_task.add_done_callback(self._background_tasks.discard)
        else:
            # Reached max retries: quarantine in Dead Letter Queue
            logger.error(
                "event_exhausted_retries_quarantining",
                event_id=event.event_id,
                retries=event.retry_count,
                reason=error_reason,
            )
            self.dlq.quarantine(event=event, reason=error_reason, error_trace=error_trace)
            if self._db_path:
                with self._get_connection() as conn:
                    conn.execute(
                        """
                        UPDATE durable_events
                        SET status = 'QUARANTINED', processed_at = ?
                        WHERE event_id = ?;
                        """,
                        (datetime.now(UTC).isoformat(), event.event_id),
                    )
                    conn.commit()

    async def _worker_loop(self, worker_id: str) -> None:
        """Background worker thread consuming events from priority queue."""
        while self._running:
            try:
                try:
                    _, _, event = await asyncio.wait_for(self._queue.get(), timeout=0.25)
                except TimeoutError:
                    continue

                async with self._async_lock:
                    self._in_flight[event.event_id] = event

                try:
                    await self._process_event(event, worker_id)
                finally:
                    async with self._async_lock:
                        self._in_flight.pop(event.event_id, None)
                    self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("worker_loop_error", worker_id=worker_id, error=str(e))
                await asyncio.sleep(0.1)

    def start(self) -> None:
        """Start background consumer worker tasks."""
        if self._running:
            return
        self._running = True
        for i in range(self.num_workers):
            task = asyncio.create_task(self._worker_loop(worker_id=f"worker-{i}"))
            self._workers.append(task)
        logger.info("event_bus_started", num_workers=self.num_workers)

    async def drain(self, timeout: float = 5.0) -> None:
        """Wait until all queued and in-flight events have completed or timeout expires."""
        start_time = datetime.now(UTC)
        while True:
            qsize = self._queue.qsize()
            async with self._async_lock:
                inflight = len(self._in_flight)
            if qsize == 0 and inflight == 0:
                break
            if (datetime.now(UTC) - start_time).total_seconds() > timeout:
                logger.warning("event_bus_drain_timeout", remaining_queued=qsize, inflight=inflight)
                break
            await asyncio.sleep(0.05)

    async def stop(self, drain_first: bool = True, timeout: float = 5.0) -> None:
        """Stop worker tasks cleanly."""
        if not self._running:
            return
        if drain_first:
            await self.drain(timeout=timeout)
        self._running = False
        all_tasks = list(self._workers) + list(self._background_tasks)
        for task in all_tasks:
            task.cancel()
        await asyncio.gather(*all_tasks, return_exceptions=True)
        self._workers.clear()
        self._background_tasks.clear()
        logger.info("event_bus_stopped")

    def queue_size(self) -> int:
        """Return the number of events pending in the priority queue."""
        return self._queue.qsize()
