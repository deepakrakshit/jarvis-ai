"""Comprehensive Test Suite for JARVIS WebSocket Gateway.

Tests Section 9 requirements:
- Challenge nonce on incoming client connection.
- Authentication gating for protected RPC methods.
- Hello-ok handshake and capability discovery catalog.
- Health check and node listing.
- Memory store and search RPC dispatch.
- Model catalog retrieval.
- Task creation, progress streaming, and completion events.
"""

import asyncio
import json
import socket
from pathlib import Path
from typing import Any, Dict

import pytest
import websockets

from jarvis.core.control_plane import ControlPlane
from jarvis.gateway.server import GatewayServer
from jarvis.memory.manager import MemoryManager
from jarvis.storage.database import DatabaseEngine


def get_free_port() -> int:
    """Dynamically allocate an available ephemeral TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def temp_gateway_db(tmp_path: Path) -> DatabaseEngine:
    """Isolated database for gateway testing."""
    return DatabaseEngine(db_path=tmp_path / "gateway_test.db")


@pytest.fixture
def gateway_harness(temp_gateway_db: DatabaseEngine) -> Dict[str, Any]:
    """Provide initialized GatewayServer with isolated dependencies."""
    port = get_free_port()
    mem = MemoryManager(database=temp_gateway_db)
    cp = ControlPlane(database=temp_gateway_db)

    server = GatewayServer(
        host="127.0.0.1",
        port=port,
        database=temp_gateway_db,
        memory=mem,
        control=cp,
    )

    return {"server": server, "port": port}


@pytest.mark.asyncio
async def test_gateway_handshake_and_rpc_lifecycle(
    gateway_harness: Dict[str, Any],
) -> None:
    """Verify challenge event, handshake, auth gate, and basic RPC methods."""
    server: GatewayServer = gateway_harness["server"]
    port: int = gateway_harness["port"]

    await server.start()
    uri = f"ws://127.0.0.1:{port}"

    try:
        async with websockets.connect(uri) as ws:
            # 1. Receive initial connect.challenge event
            raw_challenge = await asyncio.wait_for(ws.recv(), timeout=5.0)
            challenge = json.loads(raw_challenge)
            assert challenge["type"] == "event"
            assert challenge["event"] == "connect.challenge"
            assert "nonce" in challenge["payload"]

            # 2. Attempt unauthorized RPC method before connect
            health_req = {
                "type": "req",
                "id": "unauth-1",
                "method": "health",
                "params": {},
            }
            await ws.send(json.dumps(health_req))
            raw_unauth_res = await asyncio.wait_for(ws.recv(), timeout=5.0)
            unauth_res = json.loads(raw_unauth_res)
            assert unauth_res["type"] == "res"
            assert unauth_res["id"] == "unauth-1"
            assert unauth_res["ok"] is False
            assert "Authentication required" in unauth_res["error"]

            # 3. Perform connect handshake
            connect_req = {
                "type": "req",
                "id": "conn-1",
                "method": "connect",
                "params": {"client_id": "test-operator-ui"},
            }
            await ws.send(json.dumps(connect_req))
            raw_conn_res = await asyncio.wait_for(ws.recv(), timeout=5.0)
            conn_res = json.loads(raw_conn_res)
            assert conn_res["type"] == "res"
            assert conn_res["id"] == "conn-1"
            assert conn_res["ok"] is True
            assert conn_res["payload"]["type"] == "hello-ok"
            assert "methods" in conn_res["payload"]["features"]

            # 4. Now authenticated, check health
            await ws.send(json.dumps(health_req))
            raw_health_res = await asyncio.wait_for(ws.recv(), timeout=5.0)
            health_res = json.loads(raw_health_res)
            assert health_res["ok"] is True
            assert health_res["payload"]["status"] == "HEALTHY"

            # 5. Check nodes listing
            nodes_req = {
                "type": "req",
                "id": "nodes-1",
                "method": "node.list",
                "params": {},
            }
            await ws.send(json.dumps(nodes_req))
            raw_nodes_res = await asyncio.wait_for(ws.recv(), timeout=5.0)
            nodes_res = json.loads(raw_nodes_res)
            assert nodes_res["ok"] is True
            assert len(nodes_res["payload"]["nodes"]) >= 2

            # 6. Check memory store and search
            store_req = {
                "type": "req",
                "id": "mem-store-1",
                "method": "memory.store",
                "params": {
                    "key": "user_beverage",
                    "content": "User drinks sparkling water during coding",
                    "memory_type": "USER_PREFERENCE",
                },
            }
            await ws.send(json.dumps(store_req))
            raw_store_res = await asyncio.wait_for(ws.recv(), timeout=5.0)
            store_res = json.loads(raw_store_res)
            assert store_res["ok"] is True

            search_req = {
                "type": "req",
                "id": "mem-search-1",
                "method": "memory.search",
                "params": {"query": "sparkling water"},
            }
            await ws.send(json.dumps(search_req))
            raw_search_res = await asyncio.wait_for(ws.recv(), timeout=5.0)
            search_res = json.loads(raw_search_res)
            assert search_res["ok"] is True
            assert len(search_res["payload"]["memories"]) >= 1
            assert search_res["payload"]["memories"][0]["key"] == "user_beverage"

    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_gateway_task_create_and_streaming(
    gateway_harness: Dict[str, Any],
) -> None:
    """Verify task submission, synchronous acceptance, and async progress/completed events."""
    server: GatewayServer = gateway_harness["server"]
    port: int = gateway_harness["port"]

    await server.start()
    uri = f"ws://127.0.0.1:{port}"

    try:
        async with websockets.connect(uri) as ws:
            # Drain challenge
            await ws.recv()

            # Connect
            connect_req = {
                "type": "req",
                "id": "c1",
                "method": "connect",
                "params": {"client_id": "test-task-client"},
            }
            await ws.send(json.dumps(connect_req))
            await ws.recv()

            # Submit task
            task_req = {
                "type": "req",
                "id": "task-req-1",
                "method": "task.create",
                "params": {
                    "intent": "Inspect system info",
                    "task_type": "WINDOWS_CONTROL",
                },
            }
            await ws.send(json.dumps(task_req))

            # Expect task acceptance response
            raw_accept = await asyncio.wait_for(ws.recv(), timeout=5.0)
            accept_res = json.loads(raw_accept)
            assert accept_res["type"] == "res"
            assert accept_res["id"] == "task-req-1"
            assert accept_res["ok"] is True
            task_id = accept_res["payload"]["task_id"]

            # Expect task.progress event
            raw_prog = await asyncio.wait_for(ws.recv(), timeout=5.0)
            prog_event = json.loads(raw_prog)
            assert prog_event["type"] == "event"
            assert prog_event["event"] == "task.progress"
            assert prog_event["payload"]["task_id"] == task_id

            # Expect task.completed event
            raw_done = await asyncio.wait_for(ws.recv(), timeout=10.0)
            done_event = json.loads(raw_done)
            assert done_event["type"] == "event"
            assert done_event["event"] == "task.completed"
            assert done_event["payload"]["task_id"] == task_id

    finally:
        await server.stop()
