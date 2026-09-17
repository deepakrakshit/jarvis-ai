"""JARVIS Background Task Manager and Event Dispatcher.

Decouples long-running asynchronous execution lifecycles from the realtime voice connection,
enabling non-blocking user interaction, truthful progress tracking, and conversational control.
"""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from jarvis.core.logging import get_logger
from jarvis.core.state.status import TaskStatus

logger = get_logger(__name__)


class VoiceTaskEventType(StrEnum):
    """Event types emitted during the background task lifecycle."""

    TASK_STARTED = "TASK_STARTED"
    TASK_PROGRESS = "TASK_PROGRESS"
    TASK_PHASE_CHANGED = "TASK_PHASE_CHANGED"
    TASK_BLOCKED = "TASK_BLOCKED"
    TASK_AWAITING_APPROVAL = "TASK_AWAITING_APPROVAL"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_CANCELLED = "TASK_CANCELLED"


class VoiceTaskEvent(BaseModel):
    """Immutable event record emitted to voice and telemetry observers."""

    event_id: str = Field(default_factory=lambda: f"vte_{uuid4().hex[:8]}")
    task_id: str
    event_type: VoiceTaskEventType
    title: str
    phase: str
    message: str
    is_milestone: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ActiveVoiceTask:
    """Stateful container representing a managed background execution task."""

    def __init__(
        self,
        task_id: str,
        title: str,
        session_id: str,
        coro_fn: Callable[..., Coroutine[Any, Any, Any]],
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.task_id = task_id
        self.title = title
        self.session_id = session_id
        self.coro_fn = coro_fn
        self.args = args
        self.kwargs = kwargs or {}

        self.status = TaskStatus.CREATED
        self.current_phase = "INITIATION"
        self.progress_message = "Task initialized"
        self.result: Any = None
        self.error: str | None = None

        self.created_at = datetime.now(UTC)
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None

        self._asyncio_task: asyncio.Task[Any] | None = None
        self._cancellation_requested = False

    @property
    def is_running(self) -> bool:
        """Return True if the task is actively executing."""
        return self.status in (
            TaskStatus.CREATED,
            TaskStatus.EXECUTING,
            TaskStatus.PLANNING,
            TaskStatus.WAITING_TOOL,
            TaskStatus.VERIFYING,
        )

    @property
    def is_terminal(self) -> bool:
        """Return True if the task has concluded."""
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
            TaskStatus.EXPIRED,
        )


class BackgroundTaskManager:
    """Orchestrates asynchronous background tasks and dispatches truthful events."""

    def __init__(self) -> None:
        self._tasks: dict[str, ActiveVoiceTask] = {}
        self._event_subscribers: list[Callable[[VoiceTaskEvent], Coroutine[Any, Any, None]]] = []
        self._lock = asyncio.Lock()

    def subscribe(
        self,
        callback: Callable[[VoiceTaskEvent], Coroutine[Any, Any, None]],
    ) -> None:
        """Register an asynchronous listener for background task milestone events."""
        if callback not in self._event_subscribers:
            self._event_subscribers.append(callback)

    def unsubscribe(
        self,
        callback: Callable[[VoiceTaskEvent], Coroutine[Any, Any, None]],
    ) -> None:
        """Remove a previously registered task event listener."""
        if callback in self._event_subscribers:
            self._event_subscribers.remove(callback)

    async def _emit_event(self, event: VoiceTaskEvent) -> None:
        """Dispatch event to all registered listeners asynchronously."""
        logger.debug(
            "emitting_voice_task_event",
            task_id=event.task_id,
            event_type=event.event_type.value,
            msg=event.message,
        )
        for subscriber in list(self._event_subscribers):
            try:
                await subscriber(event)
            except Exception as e:
                logger.warning(
                    "voice_task_event_subscriber_error",
                    error=str(e),
                    task_id=event.task_id,
                )

    async def submit_task(
        self,
        title: str,
        session_id: str,
        coro_fn: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> ActiveVoiceTask:
        """Instantiate and launch an asynchronous background task without blocking."""
        tid = task_id or f"task_{uuid4().hex[:8]}"
        task = ActiveVoiceTask(
            task_id=tid,
            title=title,
            session_id=session_id,
            coro_fn=coro_fn,
            args=args,
            kwargs=kwargs,
        )

        async with self._lock:
            self._tasks[tid] = task

        # Launch execution in background asyncio task
        task._asyncio_task = asyncio.create_task(
            self._run_task_wrapper(task),
            name=f"jarvis_bg_task_{tid}",
        )
        return task

    async def _run_task_wrapper(self, task: ActiveVoiceTask) -> None:
        """Internal execution supervisor managing status transitions and event emission."""
        task.status = TaskStatus.EXECUTING
        task.started_at = datetime.now(UTC)
        task.progress_message = "Task execution started"

        await self._emit_event(
            VoiceTaskEvent(
                task_id=task.task_id,
                event_type=VoiceTaskEventType.TASK_STARTED,
                title=task.title,
                phase=task.current_phase,
                message=f"I've started working on: {task.title}",
                is_milestone=True,
            )
        )

        try:
            # Execute actual coroutine with task instance access if requested
            result = await task.coro_fn(*task.args, **task.kwargs)
            task.result = result
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(UTC)
            task.progress_message = "Task completed successfully"

            await self._emit_event(
                VoiceTaskEvent(
                    task_id=task.task_id,
                    event_type=VoiceTaskEventType.TASK_COMPLETED,
                    title=task.title,
                    phase="COMPLETION",
                    message=f"Task '{task.title}' has completed successfully.",
                    is_milestone=True,
                    payload={"result": result},
                )
            )

        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELED
            task.completed_at = datetime.now(UTC)
            task.progress_message = "Task was cancelled"

            await self._emit_event(
                VoiceTaskEvent(
                    task_id=task.task_id,
                    event_type=VoiceTaskEventType.TASK_CANCELLED,
                    title=task.title,
                    phase="CANCELLED",
                    message=f"Task '{task.title}' has been cancelled.",
                    is_milestone=True,
                )
            )

        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            task.completed_at = datetime.now(UTC)
            task.progress_message = f"Task failed: {exc}"

            await self._emit_event(
                VoiceTaskEvent(
                    task_id=task.task_id,
                    event_type=VoiceTaskEventType.TASK_FAILED,
                    title=task.title,
                    phase="FAILED",
                    message=f"Task '{task.title}' failed: {exc}",
                    is_milestone=True,
                    payload={"error": str(exc)},
                )
            )

    async def update_progress(
        self,
        task_id: str,
        message: str,
        phase: str | None = None,
        is_milestone: bool = True,
    ) -> None:
        """Update task progress and emit event if a meaningful milestone was reached."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return

            task.progress_message = message
            if phase:
                task.current_phase = phase

        if is_milestone:
            await self._emit_event(
                VoiceTaskEvent(
                    task_id=task.task_id,
                    event_type=VoiceTaskEventType.TASK_PROGRESS,
                    title=task.title,
                    phase=task.current_phase,
                    message=message,
                    is_milestone=True,
                )
            )

    async def cancel_task(self, task_id: str) -> bool:
        """Request cancellation of an active task, triggering graceful termination."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.is_terminal:
                return False

            task._cancellation_requested = True
            task.status = TaskStatus.CANCELED
            task.completed_at = datetime.now(UTC)
            task.progress_message = "Task cancelled by user"
            if task._asyncio_task and not task._asyncio_task.done():
                task._asyncio_task.cancel()
            return True

    def get_task(self, task_id: str) -> ActiveVoiceTask | None:
        """Retrieve task handle by ID."""
        return self._tasks.get(task_id)

    def list_tasks(self, session_id: str | None = None) -> list[ActiveVoiceTask]:
        """List all managed tasks, optionally filtered by session."""
        if session_id:
            return [t for t in self._tasks.values() if t.session_id == session_id]
        return list(self._tasks.values())
