"""JARVIS Gateway Subsystem.

Implements Section 9 of ARCHITECTURE.md:
- Typed WebSocket RPC protocol server and client connection manager.
- Dispatching user intents directly into LangGraph Control Plane.
- Streaming asynchronous task progress, completed events, and approval requests.
- Discoverable service catalog and live health monitoring.
"""

from jarvis.gateway.connection_manager import ClientSession, ConnectionManager
from jarvis.gateway.protocol import (
    EventFrame,
    FrameType,
    ProtocolEvent,
    ProtocolMethod,
    RequestFrame,
    ResponseFrame,
    create_challenge_event,
    create_error_response,
    create_hello_ok_response,
    create_success_response,
    create_task_completed_event,
    create_task_progress_event,
)
from jarvis.gateway.server import GatewayServer, gateway_server

__all__ = [
    "GatewayServer",
    "gateway_server",
    "ConnectionManager",
    "ClientSession",
    "RequestFrame",
    "ResponseFrame",
    "EventFrame",
    "FrameType",
    "ProtocolMethod",
    "ProtocolEvent",
    "create_challenge_event",
    "create_hello_ok_response",
    "create_success_response",
    "create_error_response",
    "create_task_progress_event",
    "create_task_completed_event",
]
