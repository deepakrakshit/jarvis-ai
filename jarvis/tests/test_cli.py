"""Tests for the JARVIS Unified CLI Entrypoint and Command Handlers."""

from pathlib import Path

import pytest

from jarvis.cli import build_parser, main
from jarvis.cli.commands import handle_acp, handle_cron, handle_run, handle_status
from jarvis.contracts.task import TaskType
from jarvis.core.control_plane import ControlPlane
from jarvis.storage.database import DatabaseEngine


def test_cli_parser_structure() -> None:
    """Verify that all top-level subcommands and flags parse correctly."""
    parser = build_parser()

    # Status
    args_status = parser.parse_args(["status", "--json"])
    assert args_status.command == "status"
    assert args_status.json is True

    # Run
    args_run = parser.parse_args(["run", "Inspect system health", "--type", "WINDOWS_CONTROL"])
    assert args_run.command == "run"
    assert args_run.intent == "Inspect system health"
    assert args_run.type == "WINDOWS_CONTROL"

    # Gateway
    args_gw = parser.parse_args(["gateway", "--port", "9999"])
    assert args_gw.command == "gateway"
    assert args_gw.port == 9999

    # Cron
    args_cron = parser.parse_args(["cron", "--continuous", "--interval", "30"])
    assert args_cron.command == "cron"
    assert args_cron.continuous is True
    assert args_cron.interval == 30.0

    # ACP
    args_acp = parser.parse_args(["acp", "spawn", "--repo", "C:/test_repo"])
    assert args_acp.command == "acp"
    assert args_acp.acp_subcommand == "spawn"
    assert args_acp.repo == "C:/test_repo"


def test_cli_handle_status(test_db: DatabaseEngine) -> None:
    """Verify diagnostics dictionary gathered by handle_status."""
    report = handle_status(database=test_db)
    assert report["status"] == "OPERATIONAL"
    assert "version" in report
    assert "database_stats" in report
    assert "sessions_count" in report["database_stats"]
    assert "tasks_count" in report["database_stats"]
    assert "system_info" in report


@pytest.mark.asyncio
async def test_cli_handle_run_and_control_plane(test_db: DatabaseEngine) -> None:
    """Verify task submission and execution via handle_run."""
    cp = ControlPlane(database=test_db)
    outcome = await handle_run(
        intent="Check system volume",
        task_type=TaskType.WINDOWS_CONTROL,
        cp=cp,
    )
    assert "task_id" in outcome
    assert outcome["state"] in ("COMPLETED", "FAILED")


@pytest.mark.asyncio
async def test_cli_handle_cron(test_db: DatabaseEngine) -> None:
    """Verify single heartbeat pulse via handle_cron."""
    from jarvis.cron import HeartbeatMonitor

    monitor = HeartbeatMonitor(database=test_db)
    results = await handle_cron(once=True, monitor=monitor)
    assert len(results) == 1
    assert results[0]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_cli_handle_acp(tmp_path: Path, test_db: DatabaseEngine) -> None:
    """Verify ACP spawn command handler."""
    from jarvis.acp import JarvisAgentBroker

    repo = tmp_path / "acp_cli_repo"
    repo.mkdir()
    broker = JarvisAgentBroker(database=test_db)

    result = await handle_acp(
        subcommand="spawn",
        repo_path=str(repo),
        model="GPT-OSS 120B",
        broker_instance=broker,
    )
    assert result["status"] == "created"
    assert "session_id" in result
    assert result["model"] == "GPT-OSS 120B"


def test_cli_main_invocations(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify executing main() with status --json and --version."""
    exit_code_status = main(["status", "--json"])
    assert exit_code_status == 0
    captured_status = capsys.readouterr()
    assert '"status": "OPERATIONAL"' in captured_status.out

    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    captured_version = capsys.readouterr()
    assert "JARVIS OS v" in captured_version.out
