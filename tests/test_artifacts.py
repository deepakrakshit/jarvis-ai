"""Tests for JARVIS Artifact Storage, Emitters, and Sidecars.

Verifies Section 40 of ARCHITECTURE.md:
- First-class artifact generation, checksumming, and persistence
- Standardized emitters for Markdown, Diffs, Plans, Reports, and Screenshots
- Sidecar process registration, lifecycle, and health probes
- Gateway RPC artifacts.list, artifacts.get, and artifacts.download
"""

import sys
from pathlib import Path

import pytest

from jarvis.artifacts import (
    ArtifactEmitter,
    ArtifactStore,
    ArtifactType,
    SidecarManager,
)
from jarvis.gateway.protocol import ProtocolMethod, RequestFrame
from jarvis.gateway.server import GatewayServer
from jarvis.storage.database import DatabaseEngine


def test_artifact_store_lifecycle_and_checksum(test_db: DatabaseEngine, tmp_path: Path) -> None:
    """Verify artifact persistence, SHA-256 checksum calculation, and retrieval."""
    store = ArtifactStore(storage_dir=tmp_path / "artifacts", database=test_db)

    # 1. Store text artifact
    text_content = "# Project Brief\nAutonomous stateful operating system."
    art_text = store.save_artifact(
        title="Project Brief",
        artifact_type=ArtifactType.MARKDOWN,
        content=text_content,
        session_id="SESS_ART_1",
        task_id="TASK_ART_1",
    )

    assert art_text.title == "Project Brief"
    assert art_text.artifact_type == ArtifactType.MARKDOWN
    assert art_text.size_bytes == len(text_content.encode("utf-8"))
    assert len(art_text.checksum) == 64  # Valid SHA-256 length
    assert art_text.storage_path.exists()

    # Read text back
    read_text_back = store.read_text(art_text.artifact_id)
    assert read_text_back == text_content

    # 2. Store binary artifact
    bin_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    art_bin = store.save_artifact(
        title="Screen Capture",
        artifact_type=ArtifactType.SCREENSHOT,
        content=bin_data,
        session_id="SESS_ART_1",
    )
    assert art_bin.size_bytes == len(bin_data)
    read_bin_back = store.read_bytes(art_bin.artifact_id)
    assert read_bin_back == bin_data

    # 3. List artifacts with filter
    list_sess = store.list_artifacts(session_id="SESS_ART_1")
    assert len(list_sess) == 2

    list_task = store.list_artifacts(task_id="TASK_ART_1")
    assert len(list_task) == 1
    assert list_task[0].id == art_text.artifact_id

    # 4. Delete artifact
    deleted = store.delete_artifact(art_text.artifact_id)
    assert deleted is True
    assert not art_text.storage_path.exists()
    assert store.get_artifact(art_text.artifact_id) is None


def test_artifact_emitters(test_db: DatabaseEngine, tmp_path: Path) -> None:
    """Verify standardized emitter helpers for common outputs."""
    store = ArtifactStore(storage_dir=tmp_path / "emitters", database=test_db)
    emitter = ArtifactEmitter(store=store)

    # 1. Diff
    diff_text = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new"
    art_diff = emitter.emit_diff("Fix bug", diff_text, session_id="SESS_1")
    assert art_diff.artifact_type == ArtifactType.DIFF
    assert store.read_text(art_diff.artifact_id) == diff_text

    # 2. Plan
    plan_data = {"goal": "Upgrade database", "tasks": ["migration", "verify"]}
    art_plan = emitter.emit_plan("Migration Plan", plan_data, session_id="SESS_1")
    assert art_plan.artifact_type == ArtifactType.PLAN
    assert "Upgrade database" in store.read_text(art_plan.artifact_id)

    # 3. Report
    report = "# Evaluation Report\n100% tests passed."
    art_report = emitter.emit_report("Quality Report", report, session_id="SESS_1")
    assert art_report.artifact_type == ArtifactType.REPORT

    # 4. Log
    log_text = "[INFO] Starting daemon...\n[INFO] Daemon ready."
    art_log = emitter.emit_log("Server Log", log_text, session_id="SESS_1")
    assert art_log.artifact_type == ArtifactType.LOG


def test_sidecar_process_lifecycle(tmp_path: Path) -> None:
    """Verify auxiliary sidecar registration, launch, and termination."""
    mgr = SidecarManager()

    # Register dummy sleep process using current python interpreter
    sidecar = mgr.register(
        name="dummy_worker",
        executable=sys.executable,
        args=["-c", "import time; time.sleep(10)"],
        cwd=tmp_path,
    )
    assert not sidecar.is_running()

    # Start sidecar
    started = mgr.start("dummy_worker")
    assert started is True
    assert sidecar.is_running()
    status = sidecar.get_status()
    assert status["running"] is True
    assert status["pid"] is not None

    # Stop sidecar
    stopped = mgr.stop("dummy_worker")
    assert stopped is True
    assert not sidecar.is_running()


@pytest.mark.asyncio
async def test_gateway_artifact_rpc_handlers(test_db: DatabaseEngine, tmp_path: Path) -> None:
    """Verify artifacts.list, artifacts.get, and artifacts.download over Gateway protocol."""
    store = ArtifactStore(storage_dir=tmp_path / "gw_artifacts", database=test_db)
    daemon = GatewayServer(database=test_db, artifacts=store)

    # Pre-populate an artifact
    art = store.save_artifact(
        title="System Architecture",
        artifact_type=ArtifactType.MARKDOWN,
        content="# JARVIS OS Architecture",
        session_id="SESS_GW_1",
    )

    # Create dummy client connection
    class MockWebSocket:
        def __init__(self) -> None:
            self.sent_messages: list[str] = []

        async def send(self, message: str) -> None:
            self.sent_messages.append(message)

    ws = MockWebSocket()
    client = daemon.connections.register(ws)

    # 1. artifacts.list
    list_req = RequestFrame(
        method=ProtocolMethod.ARTIFACT_LIST.value,
        params={"session_id": "SESS_GW_1"},
    )
    await daemon._dispatch_method(client.conn_id, list_req)
    assert len(ws.sent_messages) == 1
    import json

    res1 = json.loads(ws.sent_messages[-1])
    assert res1["ok"] is True
    assert len(res1["payload"]["artifacts"]) == 1
    assert res1["payload"]["artifacts"][0]["id"] == art.artifact_id

    # 2. artifacts.get
    get_req = RequestFrame(
        method=ProtocolMethod.ARTIFACT_GET.value,
        params={"artifact_id": art.artifact_id},
    )
    await daemon._dispatch_method(client.conn_id, get_req)
    res2 = json.loads(ws.sent_messages[-1])
    assert res2["ok"] is True
    assert res2["payload"]["artifact"]["id"] == art.artifact_id
    assert res2["payload"]["artifact"]["title"] == "System Architecture"

    # 3. artifacts.download
    dl_req = RequestFrame(
        method=ProtocolMethod.ARTIFACT_DOWNLOAD.value,
        params={"artifact_id": art.artifact_id},
    )
    await daemon._dispatch_method(client.conn_id, dl_req)
    res3 = json.loads(ws.sent_messages[-1])
    assert res3["ok"] is True
    assert res3["payload"]["encoding"] == "base64"
    import base64

    decoded = base64.b64decode(res3["payload"]["data"]).decode("utf-8")
    assert decoded == "# JARVIS OS Architecture"
