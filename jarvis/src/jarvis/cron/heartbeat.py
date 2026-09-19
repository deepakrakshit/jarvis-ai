"""JARVIS Proactive Heartbeat Architecture.

Enforces Section 32 of ARCHITECTURE.md:
- Awareness mechanism, not unrestricted autonomy.
- Periodically checks standing intents and scheduled cron jobs.
- Tasks are created ONLY through the normal policy lifecycle.
- Heartbeats cannot bypass ordinary authorization gates.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from jarvis.contracts.task import Task, TaskPriority, TaskState, TaskType
from jarvis.core.control_plane import ControlPlane, control_plane
from jarvis.cron.models import HeartbeatReport
from jarvis.cron.scheduler import CronScheduler, cron_scheduler
from jarvis.memory.manager import MemoryManager, memory_manager
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class HeartbeatMonitor:
    """Orchestrates periodic awareness pulses and policy-gated proactive dispatch."""

    def __init__(
        self,
        control: Optional[ControlPlane] = None,
        memory: Optional[MemoryManager] = None,
        scheduler: Optional[CronScheduler] = None,
        database: Optional[DatabaseEngine] = None,
    ) -> None:
        self.control = control or control_plane
        self.memory = memory or memory_manager
        self.scheduler = scheduler or cron_scheduler
        self.db = database or db

        self._stop_event: asyncio.Event = asyncio.Event()
        self._loop_task: Optional[asyncio.Task[None]] = None

    async def pulse(self, now: Optional[datetime] = None) -> HeartbeatReport:
        """Execute a single deterministic heartbeat awareness cycle.

        Enforces Section 32 invariants:
        - Check standing intents
        - Check conditions / scheduled jobs
        - Create task only when appropriate
        - Task enters normal policy lifecycle
        """
        current_time = now or utc_now()
        report = HeartbeatReport(timestamp=current_time)

        # 1. Check Standing Intents against periodic awareness event
        awareness_event = f"heartbeat.tick at {current_time.isoformat()}"
        triggered_intents = self.memory.evaluate_standing_intents(awareness_event)
        report.standing_intents_checked = 1
        report.standing_intents_fired = len(triggered_intents)

        for intent in triggered_intents:
            intent_task = Task(
                task_id=f"TASK-INTENT-{uuid4().hex[:8].upper()}",
                session_id=intent.session_id or "HEARTBEAT-SESSION",
                raw_intent=intent.description,
                task_type=TaskType.BACKGROUND_WORKER,
                priority=TaskPriority.LOW,
                state=TaskState.CREATED,
            )
            # Route task strictly through normal LangGraph control plane and policy gates
            await self.control.execute_task(intent_task)
            report.dispatched_tasks.append(intent_task.task_id)

        # 2. Check Due Scheduled Jobs
        due_jobs = self.scheduler.get_due_jobs(now=current_time)
        report.jobs_checked = len(self.db.get_scheduled_jobs(enabled_only=True))
        report.jobs_triggered = len(due_jobs)

        for job in due_jobs:
            job_task = Task(
                task_id=f"TASK-CRON-{uuid4().hex[:8].upper()}",
                session_id="HEARTBEAT-SESSION",
                raw_intent=job.raw_intent,
                task_type=TaskType.BACKGROUND_WORKER,
                priority=TaskPriority.LOW,
                state=TaskState.CREATED,
            )
            # Submit through normal control plane lifecycle
            await self.control.execute_task(job_task)
            report.dispatched_tasks.append(job_task.task_id)

            # Advance schedule
            self.scheduler.mark_executed(
                job_id=job.job_id,
                interval_seconds=job.interval_seconds,
                now=current_time,
            )

        # 3. Log Heartbeat Audit Record
        summary_msg = (
            f"Heartbeat pulse executed: {report.standing_intents_fired} intents triggered, "
            f"{report.jobs_triggered} cron jobs dispatched."
        )
        report.summary = summary_msg
        self.db.log_heartbeat_run(
            status="SUCCESS",
            standing_intents_checked=report.standing_intents_checked,
            standing_intents_fired=report.standing_intents_fired,
            jobs_checked=report.jobs_checked,
            jobs_triggered=report.jobs_triggered,
            summary=summary_msg,
        )

        logger.info(summary_msg)
        return report

    async def run_loop(self, interval_seconds: int = 60) -> None:
        """Run the periodic heartbeat pulse loop in background."""
        logger.info(f"Heartbeat monitor loop started (interval={interval_seconds}s)")
        self._stop_event.clear()
        while not self._stop_event.is_set():
            try:
                await self.pulse()
            except Exception as err:
                logger.error(f"Heartbeat pulse encountered error: {err}")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass

        logger.info("Heartbeat monitor loop terminated cleanly.")

    def stop(self) -> None:
        """Signal the background loop to stop."""
        self._stop_event.set()


# Default singleton instance
heartbeat_monitor = HeartbeatMonitor()
