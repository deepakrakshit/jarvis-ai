"""Tests for sensitive filesystem boundary enforcement."""

from pathlib import Path

import pytest

from jarvis.actions.broker import ActionBroker
from jarvis.config import JarvisSettings
from jarvis.contracts.action import ActionRequest, ActionStatus, ExecutionTarget, RiskTier
from jarvis.execution.windows.filesystem import (
    delete_file,
    deliver_file,
    list_directory,
    read_file,
    search_files,
    write_file,
)
from jarvis.execution.windows.host import windows_node
from jarvis.policy.engine import PolicyEngine
from jarvis.storage.database import DatabaseEngine


def test_direct_filesystem_operations_reject_sensitive_paths(tmp_path: Path) -> None:
    """Direct handlers reject credentials, private directories, and databases."""
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN=not-for-export", encoding="utf-8")
    private_dir = tmp_path / ".ssh"
    private_dir.mkdir()
    private_key = private_dir / "id_ed25519"
    private_key.write_text("private", encoding="utf-8")
    database_file = tmp_path / "state.db"
    database_file.write_bytes(b"sqlite")

    for operation in (
        lambda: read_file(str(env_file)),
        lambda: write_file(str(env_file), "replacement"),
        lambda: delete_file(str(env_file)),
        lambda: deliver_file(str(database_file)),
        lambda: read_file(str(private_key)),
    ):
        with pytest.raises(PermissionError):
            operation()


def test_directory_listing_and_search_hide_sensitive_entries(tmp_path: Path) -> None:
    """Indirect directory operations do not disclose protected filenames."""
    (tmp_path / ".env").write_text("TOKEN=hidden", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")
    (tmp_path / "state.db").write_bytes(b"sqlite")

    entries = list_directory(str(tmp_path))
    assert [entry["name"] for entry in entries] == ["visible.txt"]

    results = search_files(str(tmp_path), query="")
    assert [match["name"] for match in results["matches"]] == ["visible.txt"]


@pytest.mark.asyncio
async def test_action_broker_rejects_sensitive_read_and_write(tmp_path: Path) -> None:
    """The canonical broker cannot turn protected paths into successful actions."""
    windows_node.register_capabilities(force=True)
    database = DatabaseEngine(db_path=tmp_path / "broker.db")
    settings = JarvisSettings(
        WORKSPACE_DIR=tmp_path,
        DATA_DIR=tmp_path / "data",
        DATABASE_PATH=tmp_path / "data" / "state.db",
        LOG_DIR=tmp_path / "logs",
        ARTIFACTS_DIR=tmp_path / "artifacts",
    )
    protected = tmp_path / ".env"
    protected.write_text("TOKEN=not-for-export", encoding="utf-8")
    broker = ActionBroker(
        policy=PolicyEngine(workspace_dir=settings.WORKSPACE_DIR, database=database),
        database=database,
    )

    for capability, args in (
        ("filesystem.read", {"path": str(protected)}),
        ("filesystem.write", {"path": str(protected), "content": "overwrite"}),
    ):
        result = await broker.execute(
            ActionRequest(
                task_id=f"task-{capability}",
                session_id="session-security",
                capability=capability,
                arguments=args,
                target=ExecutionTarget.WINDOWS_NODE,
                risk_tier=RiskTier.READ_ONLY
                if capability == "filesystem.read"
                else RiskTier.MEDIUM,
            )
        )
        assert result.status == ActionStatus.DENIED
        assert result.verified is False
        assert "sensitive" in (result.error or "").lower()
