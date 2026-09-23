"""End-to-End System Integration Test for JARVIS.

Verifies the unified interaction lifecycle across:
- Gateway WebSocket daemon handshake and typed RPC messaging
- Task dispatch into LangGraph Control Plane
- Capability Firewall Policy verification
- Windows Node execution and observation
- Realtime event streaming back to WebSocket client
- First-class artifact generation and indexing
- Heartbeat awareness monitor
"""

import asyncio
import json
import socket
from pathlib import Path

import pytest
import websockets

from jarvis.actions.broker import ActionBroker
from jarvis.artifacts import ArtifactEmitter, ArtifactStore
from jarvis.core.control_plane import ControlPlane
from jarvis.cron import HeartbeatMonitor
from jarvis.execution.windows.host import windows_node
from jarvis.gateway.protocol import ProtocolMethod
from jarvis.gateway.server import GatewayServer
from jarvis.memory.manager import MemoryManager
from jarvis.policy.engine import PolicyEngine
from jarvis.storage.database import DatabaseEngine


def find_free_port() -> int:
    """Dynamically allocate an open port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.mark.asyncio
async def test_full_system_e2e_lifecycle(test_db: DatabaseEngine, tmp_path: Path) -> None:
    """Execute end-to-end integration test validating all primary subsystems together."""
    windows_node.register_capabilities()

    # 1. Initialize isolated database and subsystems
    policy = PolicyEngine(database=test_db)
    broker = ActionBroker(database=test_db)
    memory = MemoryManager(database=test_db)
    art_store = ArtifactStore(storage_dir=tmp_path / "e2e_artifacts", database=test_db)
    emitter = ArtifactEmitter(store=art_store)
    cp = ControlPlane(database=test_db, policy=policy, broker=broker)
    monitor = HeartbeatMonitor(database=test_db, control=cp)

    port = find_free_port()
    server = GatewayServer(
        host="127.0.0.1",
        port=port,
        control=cp,
        database=test_db,
        memory=memory,
        broker=broker,
        policy=policy,
        artifacts=art_store,
    )
    await server.start()

    uri = f"ws://127.0.0.1:{port}"
    try:
        async with websockets.connect(uri) as ws:
            # 2. Receive challenge frame
            challenge_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            challenge = json.loads(challenge_raw)
            assert challenge["type"] == "event"
            assert challenge["event"] == "connect.challenge"
            nonce = challenge["payload"]["nonce"]
            assert nonce is not None

            # 3. Handshake: connect
            connect_req = {
                "type": "req",
                "id": "req-connect-1",
                "method": ProtocolMethod.CONNECT.value,
                "params": {
                    "client_id": "e2e-tester",
                    "nonce": nonce,
                    "session_id": "SESS-E2E-1",
                },
            }
            await ws.send(json.dumps(connect_req))
            hello_ok_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            hello_ok = json.loads(hello_ok_raw)
            assert hello_ok["type"] == "res"
            assert hello_ok["ok"] is True
            assert "features" in hello_ok["payload"]
            assert "methods" in hello_ok["payload"]["features"]

            # 4. Submit Task via Gateway RPC: task.create
            task_req = {
                "type": "req",
                "id": "req-task-1",
                "method": ProtocolMethod.TASK_CREATE.value,
                "params": {
                    "intent": "Check system volume and memory",
                    "session_id": "SESS-E2E-1",
                    "priority": "NORMAL",
                    "task_type": "WINDOWS_CONTROL",
                },
            }
            await ws.send(json.dumps(task_req))

            # Receive RPC acceptance response
            task_res_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            task_res = json.loads(task_res_raw)
            assert task_res["type"] == "res"
            assert task_res["ok"] is True
            task_id = task_res["payload"]["task_id"]
            assert task_id is not None

            # 5. Collect streamed progress / completion events
            events = []
            while True:
                msg_raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                msg = json.loads(msg_raw)
                if msg["type"] == "event":
                    events.append(msg)
                    if msg["event"] == "task.completed":
                        break

            assert len(events) >= 1
            completed_evt = events[-1]
            assert completed_evt["event"] == "task.completed"
            assert completed_evt["payload"]["task_id"] == task_id
            assert completed_evt["payload"]["state"] == "COMPLETED"

            # 6. Verify first-class artifact emission and retrieval over Gateway
            art = emitter.emit_report(
                title="E2E Execution Report",
                report_markdown=f"# Run Report for {task_id}\nAll systems verified operational.",
                session_id="SESS-E2E-1",
                task_id=task_id,
            )
            assert art.artifact_id is not None

            # Query artifacts via Gateway RPC
            art_list_req = {
                "type": "req",
                "id": "req-art-list",
                "method": ProtocolMethod.ARTIFACT_LIST.value,
                "params": {"session_id": "SESS-E2E-1"},
            }
            await ws.send(json.dumps(art_list_req))
            art_list_res_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            art_list_res = json.loads(art_list_res_raw)
            assert art_list_res["ok"] is True
            assert len(art_list_res["payload"]["artifacts"]) == 1
            assert art_list_res["payload"]["artifacts"][0]["id"] == art.artifact_id

            # 7. Proactive Heartbeat check
            report = await monitor.pulse()
            assert report.status == "COMPLETED"

    finally:
        await server.stop()
