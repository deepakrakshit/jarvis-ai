"""JARVIS Cascading Cancellation Engine.

Implements Layer 5 cascading cancellation propagation across parent tasks, subagents, and active runs.
"""

from jarvis.core.logging import get_logger
from jarvis.core.state.status import TaskStatus
from jarvis.storage.task_store import TaskStore

logger = get_logger(__name__)


class CancellationManager:
    """Coordinates graceful task abortion and recursive child propagation."""

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    def cancel_task(self, task_id: str, reason: str = "User cancellation requested") -> list[str]:
        """Record cancellation for task and cascade to all direct and indirect children.

        Returns the list of all task IDs affected by this cancellation cascade.
        """
        affected_ids = [task_id]
        self.store.record_cancellation(task_id, reason)
        logger.info("task_cancellation_recorded", task_id=task_id, reason=reason)

        # Update parent task status if present
        task = self.store.get_task(task_id)
        if task and not task.is_terminal():
            event = task.transition_to(TaskStatus.CANCELED, reason=reason)
            self.store.save_task(task)
            self.store.record_event(event)

        # Recursively cascade to child subagents/tasks
        child_ids = self._cascade_to_children(task_id, reason)
        affected_ids.extend(child_ids)

        return affected_ids

    def _cascade_to_children(self, parent_task_id: str, reason: str) -> list[str]:
        """Find and cancel all child tasks linked by parent_task_id."""
        canceled_children = []
        with self.store.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT task_id FROM tasks WHERE parent_task_id = ?;",
                (parent_task_id,),
            ).fetchall()

            for r in rows:
                child_id = r["task_id"]
                self.store.record_cancellation(
                    child_id, f"Parent task {parent_task_id} canceled: {reason}"
                )
                child_task = self.store.get_task(child_id)
                if child_task and not child_task.is_terminal():
                    event = child_task.transition_to(
                        TaskStatus.CANCELED,
                        reason=f"Cascading parent cancellation: {reason}",
                    )
                    self.store.save_task(child_task)
                    self.store.record_event(event)

                canceled_children.append(child_id)
                # Recurse for deeper tree
                deeper_children = self._cascade_to_children(child_id, reason)
                canceled_children.extend(deeper_children)

        return canceled_children

    def is_canceled(self, task_id: str) -> tuple[bool, str | None]:
        """Check if task or any of its parents has been canceled."""
        # 1. Check direct cancellation
        canceled, reason = self.store.is_task_canceled(task_id)
        if canceled:
            return True, reason

        # 2. Check parent cancellation recursively
        task = self.store.get_task(task_id)
        if task and task.parent_task_id:
            return self.is_canceled(task.parent_task_id)

        return False, None
