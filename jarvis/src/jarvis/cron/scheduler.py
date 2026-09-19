"""Durable Cron Scheduler for Autonomous Tasks.

Manages periodic job registrations, computes deterministic run schedules,
and persists execution states into SQLite storage.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from jarvis.cron.models import ScheduledJob
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CronScheduler:
    """Manages periodic job registration and due-time evaluations."""

    def __init__(self, database: Optional[DatabaseEngine] = None) -> None:
        self.db = database or db

    def register_job(
        self,
        name: str,
        interval_seconds: int,
        raw_intent: str,
        metadata: Optional[Dict[str, Any]] = None,
        job_id: Optional[str] = None,
    ) -> ScheduledJob:
        """Register or update a periodic scheduled job."""
        now = utc_now()
        job = ScheduledJob(
            job_id=job_id
            or ScheduledJob(
                name=name, interval_seconds=interval_seconds, raw_intent=raw_intent
            ).job_id,
            name=name,
            interval_seconds=interval_seconds,
            raw_intent=raw_intent,
            enabled=True,
            next_run_at=now + timedelta(seconds=interval_seconds),
            created_at=now,
            metadata=metadata or {},
        )

        job_dict = {
            "job_id": job.job_id,
            "name": job.name,
            "interval_seconds": job.interval_seconds,
            "raw_intent": job.raw_intent,
            "enabled": job.enabled,
            "last_run_at": None,
            "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
            "created_at": job.created_at.isoformat(),
            "metadata": job.metadata,
        }
        self.db.save_scheduled_job(job_dict)
        logger.info(
            f"Registered scheduled job {job.job_id} ('{job.name}', every {interval_seconds}s)"
        )
        return job

    def get_due_jobs(self, now: Optional[datetime] = None) -> List[ScheduledJob]:
        """Fetch all enabled jobs that are currently due for execution."""
        current_time = now or utc_now()
        rows = self.db.get_scheduled_jobs(enabled_only=True)
        due_jobs: List[ScheduledJob] = []

        for row in rows:
            last_run = (
                datetime.fromisoformat(row["last_run_at"]) if row.get("last_run_at") else None
            )
            next_run = (
                datetime.fromisoformat(row["next_run_at"]) if row.get("next_run_at") else None
            )
            created = (
                datetime.fromisoformat(row["created_at"]) if row.get("created_at") else current_time
            )

            job = ScheduledJob(
                job_id=row["job_id"],
                name=row["name"],
                interval_seconds=row["interval_seconds"],
                raw_intent=row["raw_intent"],
                enabled=row["enabled"],
                last_run_at=last_run,
                next_run_at=next_run,
                created_at=created,
                metadata=row["metadata"],
            )

            if job.is_due(current_time):
                due_jobs.append(job)

        return due_jobs

    def mark_executed(
        self, job_id: str, interval_seconds: int, now: Optional[datetime] = None
    ) -> None:
        """Update last run time and compute the next scheduled execution target."""
        current_time = now or utc_now()
        next_run = current_time + timedelta(seconds=interval_seconds)

        self.db.update_scheduled_job(
            job_id=job_id,
            last_run_at=current_time.isoformat(),
            next_run_at=next_run.isoformat(),
        )
        logger.info(f"Marked scheduled job {job_id} executed. Next run at {next_run.isoformat()}")


# Default singleton instance
cron_scheduler = CronScheduler()
