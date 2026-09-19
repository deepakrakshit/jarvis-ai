"""Canonical Node Contracts for Multi-Device Execution Fabric.

Defines Node, NodeCapability, NodeStatus, and NodeRole.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


class NodeStatus(str, Enum):
    """Liveness and authorization status of an enrolled node."""

    ENROLLING = "ENROLLING"
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    REVOKED = "REVOKED"


class NodePlatform(str, Enum):
    """Host operating system or runtime platform."""

    WINDOWS = "WINDOWS"
    ANDROID = "ANDROID"
    LINUX = "LINUX"
    MACOS = "MACOS"
    BROWSER = "BROWSER"
    REMOTE_VM = "REMOTE_VM"


class NodeCapability(BaseModel):
    """Specific capability declared and exposed by a node."""

    capability_name: str
    description: str
    risk_tier: str = "LOW"
    parameters_schema: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class Node(BaseModel):
    """Enrolled and authenticated node in the JARVIS execution fabric."""

    node_id: str = Field(default_factory=lambda: f"NODE-{uuid4().hex[:12].upper()}")
    node_name: str
    platform: NodePlatform
    status: NodeStatus = NodeStatus.ENROLLING

    # Cryptographic identity & authentication
    public_key: Optional[str] = None
    auth_token_hash: Optional[str] = None

    # Capabilities & Policy Scope
    capabilities: List[NodeCapability] = Field(default_factory=list)
    allowed_scopes: List[str] = Field(default_factory=list)

    # Health & Telemetry
    ip_address: Optional[str] = None
    last_heartbeat_at: datetime = Field(default_factory=utc_now)
    latency_ms: float = 0.0
    device_info: Dict[str, Any] = Field(default_factory=dict)

    registered_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
