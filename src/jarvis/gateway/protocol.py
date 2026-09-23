"""JARVIS Typed Gateway Wire Protocol.

Implements Section 9 and adapts the RPC wire contracts from the core substrate:
- Canonical JSON envelopes: 'req', 'res', 'event'.
- Handshake protocol with challenge nonce and hello-ok capability catalog.
- Strongly-typed schemas for task lifecycle, action approval, and event streaming.
"""

import time
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class FrameType(str, Enum):
    """Supported envelope frame types."""

    REQ = "req"
    RES = "res"
    EVENT = "event"


class RequestFrame(BaseModel):
    """Client-to-server RPC request envelope."""

    type: str = "req"
    id: str = Field(default_factory=lambda: f"req-{uuid4().hex[:8]}")
    method: str
    params: Dict[str, Any] = Field(default_factory=dict)


class ResponseFrame(BaseModel):
    """Server-to-client RPC response envelope."""

    type: str = "res"
    id: str
    ok: bool = True
    payload: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class EventFrame(BaseModel):
    """Server-to-client asynchronous event notification envelope."""

    type: str = "event"
    event: str
    payload: Optional[Dict[str, Any]] = None
    seq: Optional[int] = None


# Canonical Method and Event Names
class ProtocolMethod(str, Enum):
    """Supported RPC methods exposed by JARVIS Gateway."""

    CONNECT = "connect"
    HEALTH = "health"
    TASK_CREATE = "task.create"
    TASK_GET = "task.get"
    TASK_CANCEL = "task.cancel"
    ACTION_APPROVE = "action.approve"
    SESSION_CREATE = "session.create"
    SESSION_GET = "session.get"
    MEMORY_SEARCH = "memory.search"
    MEMORY_STORE = "memory.store"
    MODEL_LIST = "model.list"
    NODE_LIST = "node.list"
    ARTIFACT_LIST = "artifacts.list"
    ARTIFACT_GET = "artifacts.get"
    ARTIFACT_DOWNLOAD = "artifacts.download"


class ProtocolEvent(str, Enum):
    """Supported asynchronous event notification types."""

    CONNECT_CHALLENGE = "connect.challenge"
    TASK_PROGRESS = "task.progress"
    TASK_COMPLETED = "task.completed"
    APPROVAL_REQUIRED = "approval.required"
    HEARTBEAT = "heartbeat"


# Factory Helpers
def create_challenge_event(nonce: Optional[str] = None) -> EventFrame:
    """Create initial handshake challenge event frame."""
    return EventFrame(
        event=ProtocolEvent.CONNECT_CHALLENGE.value,
        payload={
            "nonce": nonce or uuid4().hex,
            "ts": int(time.time() * 1000),
        },
    )


def create_hello_ok_response(
    req_id: str,
    conn_id: str,
    server_version: str = "3.0.0",
    supported_methods: Optional[List[str]] = None,
    supported_events: Optional[List[str]] = None,
) -> ResponseFrame:
    """Create hello-ok handshake response with discovery catalog."""
    methods = supported_methods or [m.value for m in ProtocolMethod]
    events = supported_events or [e.value for e in ProtocolEvent]

    return ResponseFrame(
        id=req_id,
        ok=True,
        payload={
            "type": "hello-ok",
            "protocol": 1,
            "server": {
                "version": server_version,
                "connId": conn_id,
            },
            "features": {
                "methods": methods,
                "events": events,
            },
        },
    )


def create_success_response(req_id: str, payload: Dict[str, Any]) -> ResponseFrame:
    """Create standard successful RPC response."""
    return ResponseFrame(id=req_id, ok=True, payload=payload)


def create_error_response(req_id: str, error_message: str) -> ResponseFrame:
    """Create standard error RPC response."""
    return ResponseFrame(id=req_id, ok=False, error=error_message)


def create_task_progress_event(
    task_id: str,
    state: str,
    message: str,
    seq: int = 1,
) -> EventFrame:
    """Create task lifecycle progress notification event."""
    return EventFrame(
        event=ProtocolEvent.TASK_PROGRESS.value,
        seq=seq,
        payload={
            "task_id": task_id,
            "state": state,
            "message": message,
            "timestamp": int(time.time() * 1000),
        },
    )


def create_task_completed_event(
    task_id: str,
    state: str,
    result_summary: Optional[str] = None,
    error: Optional[str] = None,
    seq: int = 2,
) -> EventFrame:
    """Create terminal task completion notification event."""
    return EventFrame(
        event=ProtocolEvent.TASK_COMPLETED.value,
        seq=seq,
        payload={
            "task_id": task_id,
            "state": state,
            "result_summary": result_summary,
            "error": error,
            "timestamp": int(time.time() * 1000),
        },
    )
