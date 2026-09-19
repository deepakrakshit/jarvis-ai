"""Connection Manager for JARVIS WebSocket Gateway.

Tracks active client connections, authentication status, and provides
targeted and broadcast asynchronous messaging capabilities.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set
from uuid import uuid4

from jarvis.gateway.protocol import EventFrame, ResponseFrame
from jarvis.telemetry import logger


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ClientSession:
    """Stateful client connection metadata."""

    conn_id: str
    websocket: Any
    client_id: Optional[str] = None
    authenticated: bool = False
    subscribed_sessions: Set[str] = field(default_factory=set)
    connected_at: datetime = field(default_factory=utc_now)


class ConnectionManager:
    """Maintains active WebSocket clients and handles message delivery."""

    def __init__(self) -> None:
        self._clients: Dict[str, ClientSession] = {}

    def register(self, websocket: Any) -> ClientSession:
        """Register a new incoming WebSocket connection."""
        conn_id = f"conn-{uuid4().hex[:8]}"
        session = ClientSession(conn_id=conn_id, websocket=websocket)
        self._clients[conn_id] = session
        logger.info(f"Registered WebSocket client {conn_id}")
        return session

    def unregister(self, conn_id: str) -> None:
        """Remove a disconnected WebSocket client."""
        if conn_id in self._clients:
            del self._clients[conn_id]
            logger.info(f"Unregistered WebSocket client {conn_id}")

    def get_client(self, conn_id: str) -> Optional[ClientSession]:
        """Retrieve client connection by ID."""
        return self._clients.get(conn_id)

    async def send_response(self, conn_id: str, response: ResponseFrame) -> None:
        """Send an RPC response frame to a specific client."""
        client = self._clients.get(conn_id)
        if not client:
            return
        payload_json = json.dumps(response.model_dump(mode="json"))
        await client.websocket.send(payload_json)

    async def send_event(self, conn_id: str, event: EventFrame) -> None:
        """Send an event notification to a specific client."""
        client = self._clients.get(conn_id)
        if not client:
            return
        payload_json = json.dumps(event.model_dump(mode="json"))
        await client.websocket.send(payload_json)

    async def broadcast_event(self, event: EventFrame, session_id: Optional[str] = None) -> None:
        """Broadcast an event to all authenticated clients or session subscribers."""
        payload_json = json.dumps(event.model_dump(mode="json"))
        for client in list(self._clients.values()):
            if not client.authenticated:
                continue
            if session_id and session_id not in client.subscribed_sessions:
                continue
            try:
                await client.websocket.send(payload_json)
            except Exception as err:
                logger.warning(f"Failed to broadcast event to {client.conn_id}: {err}")

    def count(self) -> int:
        """Return total active connections."""
        return len(self._clients)
