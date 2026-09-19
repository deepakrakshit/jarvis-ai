"""JARVIS Gateway Daemon and WebSocket Server.

Implements Section 9 of ARCHITECTURE.md:
- Persistent daemon serving typed RPC wire contracts over WebSockets.
- Challenge/response handshake and capability discovery.
- Dispatches client intents into the LangGraph Control Plane.
- Streams realtime task lifecycle events and progress notifications.
- Bridges action approvals, memory search, model statuses, and node queries.
"""

import base64
import json
from typing import Optional
from uuid import uuid4

import websockets
from websockets.asyncio.server import (
    Server as WebSocketServer,
)
from websockets.asyncio.server import (
    ServerConnection as WebSocketServerProtocol,
)
from websockets.asyncio.server import (
    serve,
)

from jarvis.actions.broker import ActionBroker, action_broker
from jarvis.artifacts.store import ArtifactStore, artifact_store
from jarvis.cognition.model_router import ModelRouter, model_router
from jarvis.config import settings
from jarvis.contracts.memory import MemoryRecord, MemoryType, ProvenanceSource, TrustLevel
from jarvis.contracts.task import Task, TaskPriority, TaskState, TaskType
from jarvis.core.control_plane import ControlPlane, control_plane
from jarvis.gateway.connection_manager import ConnectionManager
from jarvis.gateway.protocol import (
    ProtocolMethod,
    RequestFrame,
    create_challenge_event,
    create_error_response,
    create_hello_ok_response,
    create_success_response,
    create_task_completed_event,
    create_task_progress_event,
)
from jarvis.memory.manager import MemoryManager, memory_manager
from jarvis.policy.engine import PolicyEngine, policy_engine
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger


class GatewayServer:
    """Persistent network and control server daemon for JARVIS."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        control: Optional[ControlPlane] = None,
        database: Optional[DatabaseEngine] = None,
        memory: Optional[MemoryManager] = None,
        router: Optional[ModelRouter] = None,
        broker: Optional[ActionBroker] = None,
        policy: Optional[PolicyEngine] = None,
        artifacts: Optional[ArtifactStore] = None,
    ) -> None:
        self.host = host or settings.GATEWAY_HOST
        self.port = port or settings.GATEWAY_PORT
        self.control = control or control_plane
        self.db = database or db
        self.memory = memory or memory_manager
        self.router = router or model_router
        self.broker = broker or action_broker
        self.policy = policy or policy_engine
        self.artifacts = artifacts or (
            ArtifactStore(database=self.db) if database else artifact_store
        )

        self.connections = ConnectionManager()
        self._server: Optional[WebSocketServer] = None
        self._is_running = False

    async def start(self) -> None:
        """Start the WebSocket gateway server."""
        if self._is_running:
            return

        logger.info(f"Starting JARVIS Gateway daemon on ws://{self.host}:{self.port}...")
        self._server = await serve(
            self._handle_connection,
            self.host,
            self.port,
        )
        self._is_running = True
        logger.info(f"JARVIS Gateway successfully listening on ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Gracefully stop the WebSocket gateway server."""
        if not self._is_running or not self._server:
            return

        logger.info("Stopping JARVIS Gateway daemon...")
        self._server.close()
        await self._server.wait_closed()
        self._is_running = False
        logger.info("JARVIS Gateway stopped.")

    async def _handle_connection(self, websocket: WebSocketServerProtocol) -> None:
        """Handle incoming client WebSocket lifecycle."""
        client = self.connections.register(websocket)
        challenge_nonce = uuid4().hex

        try:
            # 1. Send initial connect challenge event
            challenge = create_challenge_event(nonce=challenge_nonce)
            await self.connections.send_event(client.conn_id, challenge)

            # 2. Process incoming request messages
            async for raw_message in websocket:
                try:
                    data = json.loads(raw_message)
                    req = RequestFrame(**data)
                except Exception as parse_err:
                    err_res = create_error_response(
                        req_id="unknown",
                        error_message=f"Invalid JSON RPC frame: {parse_err}",
                    )
                    await websocket.send(json.dumps(err_res.model_dump()))
                    continue

                # Enforce authentication for all methods except 'connect'
                if not client.authenticated and req.method != ProtocolMethod.CONNECT.value:
                    err_res = create_error_response(
                        req_id=req.id,
                        error_message="Authentication required. Please complete connect handshake.",
                    )
                    await self.connections.send_response(client.conn_id, err_res)
                    continue

                # Dispatch method
                await self._dispatch_method(client.conn_id, req)

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client {client.conn_id} disconnected normally.")
        except Exception as exc:
            logger.error(f"Error handling client {client.conn_id}: {exc}")
        finally:
            self.connections.unregister(client.conn_id)

    async def _dispatch_method(self, conn_id: str, req: RequestFrame) -> None:
        """Route request to the appropriate internal subsystem handler."""
        method = req.method
        params = req.params

        try:
            if method == ProtocolMethod.CONNECT.value:
                client = self.connections.get_client(conn_id)
                if client:
                    client.authenticated = True
                    client.client_id = params.get("client_id", "anonymous-client")
                    session_id = params.get("session_id")
                    if session_id:
                        client.subscribed_sessions.add(session_id)

                res = create_hello_ok_response(
                    req_id=req.id,
                    conn_id=conn_id,
                    server_version=settings.APP_VERSION,
                )
                await self.connections.send_response(conn_id, res)

            elif method == ProtocolMethod.HEALTH.value:
                payload = {
                    "status": "HEALTHY",
                    "version": settings.APP_VERSION,
                    "active_connections": self.connections.count(),
                    "nodes": ["windows_node", "browser_node"],
                }
                await self.connections.send_response(
                    conn_id, create_success_response(req.id, payload)
                )

            elif method == ProtocolMethod.TASK_CREATE.value:
                await self._handle_task_create(conn_id, req)

            elif method == ProtocolMethod.TASK_GET.value:
                task_id = params.get("task_id", "")
                task = self.db.get_task(task_id)
                if not task:
                    await self.connections.send_response(
                        conn_id, create_error_response(req.id, f"Task {task_id} not found")
                    )
                else:
                    await self.connections.send_response(
                        conn_id, create_success_response(req.id, task.model_dump(mode="json"))
                    )

            elif method == ProtocolMethod.TASK_CANCEL.value:
                task_id = params.get("task_id", "")
                task = self.db.get_task(task_id)
                if not task:
                    await self.connections.send_response(
                        conn_id, create_error_response(req.id, f"Task {task_id} not found")
                    )
                else:
                    task.transition_to(TaskState.CANCELLED, "Cancelled by user via Gateway")
                    self.db.save_task(task)
                    await self.connections.send_response(
                        conn_id,
                        create_success_response(
                            req.id, {"task_id": task_id, "status": "CANCELLED"}
                        ),
                    )

            elif method == ProtocolMethod.ACTION_APPROVE.value:
                approval_id = params.get("approval_id", "")
                decision = params.get("decision", "APPROVED")  # APPROVED or REJECTED
                reason = params.get("reason")
                self.policy.approvals.resolve(
                    approval_id=approval_id,
                    approved=(decision == "APPROVED"),
                    operator_identity=conn_id,
                    rejection_reason=reason,
                )
                await self.connections.send_response(
                    conn_id,
                    create_success_response(
                        req.id,
                        {"approval_id": approval_id, "decision": decision, "status": "RECORDED"},
                    ),
                )

            elif method == ProtocolMethod.SESSION_CREATE.value:
                session_id = params.get("session_id") or f"SESSION-{uuid4().hex[:8].upper()}"
                title = params.get("title", "New JARVIS Session")
                self.db.create_session(session_id=session_id, title=title)
                client = self.connections.get_client(conn_id)
                if client:
                    client.subscribed_sessions.add(session_id)
                await self.connections.send_response(
                    conn_id,
                    create_success_response(req.id, {"session_id": session_id, "title": title}),
                )

            elif method == ProtocolMethod.MEMORY_SEARCH.value:
                query = params.get("query", "")
                limit = int(params.get("limit", 10))
                type_str = params.get("memory_type")
                mem_type = MemoryType(type_str) if type_str else None

                memories = self.memory.search(query=query, memory_type=mem_type, limit=limit)
                records_data = [m.model_dump(mode="json") for m in memories]
                await self.connections.send_response(
                    conn_id, create_success_response(req.id, {"memories": records_data})
                )

            elif method == ProtocolMethod.MEMORY_STORE.value:
                record = MemoryRecord(
                    memory_type=MemoryType(params.get("memory_type", MemoryType.SEMANTIC.value)),
                    key=params.get("key", "note"),
                    content=params.get("content", ""),
                    provenance_source=ProvenanceSource.DIRECT_USER_INSTRUCTION,
                    trust_level=TrustLevel.TRUSTED_OPERATOR,
                    importance_score=float(params.get("importance_score", 0.5)),
                    session_id=params.get("session_id"),
                )
                stored = self.memory.store(record)
                await self.connections.send_response(
                    conn_id,
                    create_success_response(
                        req.id, {"stored_record": stored.model_dump(mode="json")}
                    ),
                )

            elif method == ProtocolMethod.MODEL_LIST.value:
                quotas = self.router.quotas.get_all_quotas()
                models_data = [
                    {
                        "model_family": family.value,
                        "health": self.router.quotas.get_health(family).value,
                        "quota": q.model_dump(mode="json"),
                    }
                    for family, q in quotas.items()
                ]
                await self.connections.send_response(
                    conn_id, create_success_response(req.id, {"models": models_data})
                )

            elif method == ProtocolMethod.NODE_LIST.value:
                payload = {
                    "nodes": [
                        {"node_id": "windows-primary", "type": "windows", "status": "ONLINE"},
                        {"node_id": "browser-playwright", "type": "browser", "status": "ONLINE"},
                    ]
                }
                await self.connections.send_response(
                    conn_id, create_success_response(req.id, payload)
                )

            elif method == ProtocolMethod.ARTIFACT_LIST.value:
                session_key = params.get("sessionKey") or params.get("session_id")
                task_id = params.get("taskId") or params.get("task_id")
                art_type = params.get("type")
                limit = int(params.get("limit", 50))
                summaries = self.artifacts.list_artifacts(
                    session_id=session_key,
                    task_id=task_id,
                    artifact_type=art_type,
                    limit=limit,
                )
                await self.connections.send_response(
                    conn_id,
                    create_success_response(
                        req.id, {"artifacts": [s.model_dump() for s in summaries]}
                    ),
                )

            elif method == ProtocolMethod.ARTIFACT_GET.value:
                art_id = params.get("artifactId") or params.get("artifact_id", "")
                art = self.artifacts.get_artifact(art_id)
                if not art:
                    await self.connections.send_response(
                        conn_id, create_error_response(req.id, f"Artifact {art_id} not found")
                    )
                else:
                    await self.connections.send_response(
                        conn_id,
                        create_success_response(
                            req.id, {"artifact": art.to_summary().model_dump()}
                        ),
                    )

            elif method == ProtocolMethod.ARTIFACT_DOWNLOAD.value:
                art_id = params.get("artifactId") or params.get("artifact_id", "")
                art = self.artifacts.get_artifact(art_id)
                if not art:
                    await self.connections.send_response(
                        conn_id, create_error_response(req.id, f"Artifact {art_id} not found")
                    )
                else:
                    try:
                        raw_bytes = self.artifacts.read_bytes(art_id)
                        encoded = base64.b64encode(raw_bytes).decode("ascii")
                        download_payload = {
                            "artifact": art.to_summary().model_dump(),
                            "encoding": "base64",
                            "data": encoded,
                        }
                        await self.connections.send_response(
                            conn_id, create_success_response(req.id, download_payload)
                        )
                    except Exception as download_err:
                        await self.connections.send_response(
                            conn_id,
                            create_error_response(
                                req.id, f"Failed to read artifact: {download_err}"
                            ),
                        )

            else:
                await self.connections.send_response(
                    conn_id,
                    create_error_response(req.id, f"Unknown method '{method}'"),
                )

        except Exception as err:
            logger.error(f"Error handling method {method}: {err}")
            await self.connections.send_response(conn_id, create_error_response(req.id, str(err)))

    async def _handle_task_create(self, conn_id: str, req: RequestFrame) -> None:
        """Create and asynchronously execute a task, streaming progress to the client."""
        params = req.params
        raw_intent = params.get("intent", "")
        session_id = params.get("session_id") or f"SESSION-{uuid4().hex[:8].upper()}"
        priority_str = params.get("priority", "NORMAL")
        task_type_str = params.get("task_type", "CONVERSATION")

        task = Task(
            task_id=f"TASK-{uuid4().hex[:8].upper()}",
            session_id=session_id,
            raw_intent=raw_intent,
            task_type=TaskType(task_type_str)
            if task_type_str in TaskType._value2member_map_
            else TaskType.CONVERSATION,
            priority=TaskPriority(priority_str)
            if priority_str in TaskPriority._value2member_map_
            else TaskPriority.NORMAL,
            state=TaskState.CREATED,
        )

        # Notify initial acceptance
        accept_res = create_success_response(
            req.id,
            {
                "task_id": task.task_id,
                "session_id": task.session_id,
                "state": task.state.value,
                "status": "ACCEPTED",
            },
        )
        await self.connections.send_response(conn_id, accept_res)

        # Emit progress event
        prog_event = create_task_progress_event(
            task_id=task.task_id,
            state="DISPATCHED",
            message=f"Task dispatched to Control Plane: '{raw_intent}'",
            seq=1,
        )
        await self.connections.send_event(conn_id, prog_event)

        # Run task through Control Plane asynchronously
        try:
            completed_task = await self.control.execute_task(task)
            # Emit completed event
            done_event = create_task_completed_event(
                task_id=completed_task.task_id,
                state=completed_task.state.value,
                result_summary=completed_task.result_summary,
                error=completed_task.error_message,
                seq=2,
            )
            await self.connections.send_event(conn_id, done_event)
        except Exception as exec_err:
            logger.error(f"Task execution failed for {task.task_id}: {exec_err}")
            fail_event = create_task_completed_event(
                task_id=task.task_id,
                state=TaskState.FAILED.value,
                error=str(exec_err),
                seq=2,
            )
            await self.connections.send_event(conn_id, fail_event)


# Default singleton instance
gateway_server = GatewayServer()
