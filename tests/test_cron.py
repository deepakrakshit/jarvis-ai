"""Comprehensive Test Suite for JARVIS Cron and Proactive Heartbeat Architecture.

Tests Section 32 requirements:
- Cron job registration, due-time calculations, and state advancement.
- Proactive heartbeat pulse evaluating standing intents and scheduled jobs.
- Tasks dispatched by heartbeat enter normal Control Plane state machine.
- Policy invariants: Heartbeat tasks cannot bypass ordinary policy gates.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jarvis.contracts.task import TaskState
from jarvis.core.control_plane import ControlPlane
from jarvis.cron.heartbeat import HeartbeatMonitor
from jarvis.cron.scheduler import CronScheduler
from jarvis.execution.windows.host import windows_node
from jarvis.memory.manager import MemoryManager
from jarvis.storage.database import DatabaseEngine


@pytest.fixture(autouse=True)
def setup_windows() -> None:
    """Ensure Windows node capabilities are registered for action execution."""
    windows_node.register_capabilities()


@pytest.fixture
def temp_cron_db(tmp_path: Path) -> DatabaseEngine:
    """Provide isolated SQLite database for cron tests."""
    return DatabaseEngine(db_path=tmp_path / "cron_test.db")


def test_cron_scheduler_due_evaluation(temp_cron_db: DatabaseEngine) -> None:
    """Verify cron scheduler registers jobs and calculates due targets accurately."""
    scheduler = CronScheduler(database=temp_cron_db)
    now = datetime.now(timezone.utc)

    job1 = scheduler.register_job(
        name="Hourly health check",
        interval_seconds=3600,
        raw_intent="Check system health",
    )

    # Immediately after registration, next_run_at is 1 hour in the future
    due_immediate = scheduler.get_due_jobs(now=now)
    assert len(due_immediate) == 0

    # Advance time past 1 hour
    future_now = now + timedelta(seconds=3605)
    due_future = scheduler.get_due_jobs(now=future_now)
    assert len(due_future) == 1
    assert due_future[0].job_id == job1.job_id

    # Mark executed and verify next_run_at moves another hour forward
    scheduler.mark_executed(job1.job_id, interval_seconds=3600, now=future_now)
    due_after_exec = scheduler.get_due_jobs(now=future_now)
    assert len(due_after_exec) == 0


@pytest.mark.asyncio
async def test_heartbeat_pulse_and_task_lifecycle(
    temp_cron_db: DatabaseEngine,
) -> None:
    """Verify heartbeat pulse evaluates intents and triggers policy-gated tasks."""
    scheduler = CronScheduler(database=temp_cron_db)
    memory = MemoryManager(database=temp_cron_db)
    control = ControlPlane(database=temp_cron_db)

    monitor = HeartbeatMonitor(
        control=control,
        memory=memory,
        scheduler=scheduler,
        database=temp_cron_db,
    )

    # 1. Register a standing intent listening for heartbeat ticks
    memory.register_standing_intent(
        description="Heartbeat intent: log periodic awareness check",
        trigger_keywords=["heartbeat"],
        max_fires=1,
    )

    # 2. Register a due scheduled job
    job = scheduler.register_job(
        name="Scheduled disk space audit",
        interval_seconds=60,
        raw_intent="Inspect system info",
    )
    # Force next_run_at to past so it triggers on pulse
    temp_cron_db.update_scheduled_job(
        job_id=job.job_id,
        last_run_at=(datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(),
        next_run_at=(datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(),
    )

    # 3. Execute pulse
    report = await monitor.pulse()

    assert report.status == "COMPLETED"
    assert report.standing_intents_fired == 1
    assert report.jobs_triggered == 1
    assert len(report.dispatched_tasks) == 2

    # 4. Verify dispatched tasks completed through ControlPlane
    for task_id in report.dispatched_tasks:
        persisted_task = temp_cron_db.get_task(task_id)
        assert persisted_task is not None
        assert persisted_task.state in {TaskState.COMPLETED, TaskState.NEEDS_APPROVAL}


@pytest.mark.asyncio
async def test_heartbeat_never_bypasses_policy(
    temp_cron_db: DatabaseEngine,
) -> None:
    """Verify tasks initiated by heartbeat must satisfy normal policy checks."""
    scheduler = CronScheduler(database=temp_cron_db)
    memory = MemoryManager(database=temp_cron_db)
    control = ControlPlane(database=temp_cron_db)
    monitor = HeartbeatMonitor(
        control=control,
        memory=memory,
        scheduler=scheduler,
        database=temp_cron_db,
    )

    # Register a job with intent that triggers a dangerous shell action
    job = scheduler.register_job(
        name="Dangerous clean job",
        interval_seconds=60,
        raw_intent="exec powershell Remove-Item C:\\Windows -Recurse -Force",
    )
    temp_cron_db.update_scheduled_job(
        job_id=job.job_id,
        last_run_at=None,
        next_run_at=(datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
    )

    report = await monitor.pulse()
    assert len(report.dispatched_tasks) == 1

    dispatched_task = temp_cron_db.get_task(report.dispatched_tasks[0])
    assert dispatched_task is not None
    # Task must enter normal policy lifecycle and pause at operator approval
    assert dispatched_task.state == TaskState.NEEDS_APPROVAL
