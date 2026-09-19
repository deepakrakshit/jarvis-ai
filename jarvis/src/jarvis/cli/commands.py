"""Command Handlers for the JARVIS Unified CLI.

Implements CLI commands for:
- status: Diagnostics and system health overview
- run: Policy-gated task submission and execution
- gateway: WebSocket daemon startup
- cron: Heartbeat and periodic job execution
- acp: External coding agent session management
- serve: Unified server daemon (Gateway + Heartbeat + Control Plane)
"""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from jarvis.acp import (
    AcpSessionSpec,
    JarvisAgentBroker,
)
from jarvis.config import settings
from jarvis.contracts.task import TaskType
from jarvis.core.control_plane import ControlPlane, control_plane
from jarvis.cron import HeartbeatMonitor, heartbeat_monitor
from jarvis.execution.browser.host import browser_node
from jarvis.execution.windows.host import windows_node
from jarvis.execution.windows.system import get_system_info
from jarvis.gateway.server import GatewayServer
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger


def ensure_nodes_registered() -> None:
    """Ensure host and browser execution nodes are registered with the action broker."""
    windows_node.register_capabilities()
    browser_node.register_capabilities()


def handle_status(database: Optional[DatabaseEngine] = None) -> Dict[str, Any]:
    """Gather and report comprehensive JARVIS system diagnostics."""
    target_db = database or db

    # 1. System telemetry
    try:
        sys_info = get_system_info()
    except Exception as err:
        sys_info = {"error": str(err)}

    # 2. Database statistics
    db_stats: Dict[str, Any] = {}
    try:
        with target_db.transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM sessions;")
            db_stats["sessions_count"] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM tasks;")
            db_stats["tasks_count"] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM memory_records;")
            db_stats["memories_count"] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM scheduled_jobs;")
            db_stats["jobs_count"] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM artifacts;")
            db_stats["artifacts_count"] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM acp_sessions;")
            db_stats["acp_sessions_count"] = cursor.fetchone()[0]
    except Exception as db_err:
        db_stats["error"] = str(db_err)

    status_report = {
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "database_path": str(target_db.db_path),
        "workspace_dir": str(settings.WORKSPACE_DIR),
        "database_stats": db_stats,
        "system_info": sys_info,
        "status": "OPERATIONAL",
    }
    return status_report


async def handle_run(
    intent: str,
    task_type: TaskType = TaskType.WINDOWS_CONTROL,
    session_id: Optional[str] = None,
    cp: Optional[ControlPlane] = None,
) -> Dict[str, Any]:
    """Submit a task to the Control Plane and execute until completion."""
    ensure_nodes_registered()
    plane = cp or control_plane
    logger.info(f"Submitting intent via CLI: '{intent}'")

    sess_id = session_id or "SESS-CLI"
    final_task = await plane.submit_intent(raw_intent=intent, session_id=sess_id)

    outcome = {
        "task_id": final_task.task_id,
        "state": final_task.state.value,
        "result_summary": final_task.result_summary,
        "verification_passed": final_task.verification_passed,
        "error_message": final_task.error_message,
    }
    return outcome


async def handle_gateway(
    host: Optional[str] = None,
    port: Optional[int] = None,
    server_instance: Optional[GatewayServer] = None,
) -> None:
    """Run the persistent WebSocket Gateway daemon."""
    server = server_instance or GatewayServer(host=host, port=port)
    await server.start()
    logger.info("Gateway daemon started. Press Ctrl+C to terminate.")
    try:
        while True:
            await asyncio.sleep(1.0)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Gateway shutdown signal received.")
    finally:
        await server.stop()


async def handle_cron(
    once: bool = True,
    interval_seconds: float = 60.0,
    monitor: Optional[HeartbeatMonitor] = None,
) -> List[Dict[str, Any]]:
    """Execute heartbeat and scheduled tasks."""
    mon = monitor or heartbeat_monitor
    results: List[Dict[str, Any]] = []

    if once:
        report = await mon.pulse()
        results.append(report.model_dump(mode="json"))
        return results

    logger.info(f"Starting continuous Heartbeat monitor (interval={interval_seconds}s)...")
    try:
        while True:
            report = await mon.pulse()
            results.append(report.model_dump(mode="json"))
            await asyncio.sleep(interval_seconds)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Heartbeat monitor stopped.")
    return results


async def handle_acp(
    subcommand: str,
    repo_path: Optional[str] = None,
    model: str = "GPT-OSS 120B",
    instruction: Optional[str] = None,
    broker_instance: Optional[JarvisAgentBroker] = None,
) -> Dict[str, Any]:
    """Manage ACP external coding agent sessions."""
    broker = broker_instance or JarvisAgentBroker()

    if subcommand == "spawn":
        if not repo_path:
            raise ValueError("--repo is required to spawn an ACP session.")
        spec = AcpSessionSpec(
            repo_path=Path(repo_path),
            model=model,
        )
        created = broker.create_session(spec)
        return {
            "session_id": created.session_id,
            "status": "created",
            "repo_path": str(created.repo_path),
            "model": created.model,
        }

    elif subcommand == "run":
        if not repo_path or not instruction:
            raise ValueError("Both --repo and --instruction are required for ACP run.")
        spec = AcpSessionSpec(
            repo_path=Path(repo_path),
            model=model,
        )
        created = broker.create_session(spec)
        res = await broker.execute_turn(created.session_id, instruction=instruction)
        return {
            "session_id": created.session_id,
            "status": res.status,
            "summary": res.summary,
            "changed_files": res.changed_files,
            "tests_passed": res.tests_passed,
        }

    else:
        raise ValueError(f"Unknown ACP subcommand '{subcommand}' (supported: spawn, run)")


async def handle_serve(
    host: Optional[str] = None,
    port: Optional[int] = None,
    heartbeat_interval: float = 60.0,
) -> None:
    """Run the unified system server daemon: Gateway + Heartbeat Monitor."""
    ensure_nodes_registered()
    server = GatewayServer(host=host, port=port)
    await server.start()
    logger.info("JARVIS Unified Server started (Gateway + Heartbeat). Press Ctrl+C to terminate.")

    async def heartbeat_loop() -> None:
        while True:
            try:
                await heartbeat_monitor.pulse()
            except Exception as err:
                logger.error(f"Heartbeat pulse error: {err}")
            await asyncio.sleep(heartbeat_interval)

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    try:
        while True:
            await asyncio.sleep(1.0)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutdown signal received. Terminating unified server...")
    finally:
        heartbeat_task.cancel()
        await server.stop()
        logger.info("JARVIS Unified Server stopped cleanly.")
