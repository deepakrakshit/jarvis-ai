"""Canonical Action Contracts for the JARVIS Action Broker.

Defines ActionRequest, ActionResult, RiskTier, and ExecutionTarget.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


class RiskTier(str, Enum):
    """Action risk classification tier."""

    READ_ONLY = "READ_ONLY"  # Pure observation, zero state change (e.g. read file, get volume)
    LOW = "LOW"  # Reversible local modification (e.g. create temp file, change volume)
    MEDIUM = "MEDIUM"  # Nontrivial modification (e.g. write project file, start process)
    HIGH = "HIGH"  # Destructive or security-sensitive (e.g. terminate process, delete file)
    CRITICAL = "CRITICAL"  # Host-critical operations (e.g. system reboot, credentials, format)


class ExecutionTarget(str, Enum):
    """Target execution environment for the action."""

    HOST = "HOST"
    WINDOWS_NODE = "WINDOWS_NODE"
    BROWSER_NODE = "BROWSER_NODE"
    ANDROID_NODE = "ANDROID_NODE"
    WHATSAPP_NODE = "WHATSAPP_NODE"
    SANDBOX = "SANDBOX"
    REMOTE_NODE = "REMOTE_NODE"


class ActionStatus(str, Enum):
    """Lifecycle status of an action."""

    REQUESTED = "REQUESTED"
    AUTHORIZED = "AUTHORIZED"
    DENIED = "DENIED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class ActionRequest(BaseModel):
    """Authoritative action execution request dispatched to Action Broker."""

    action_id: str = Field(default_factory=lambda: f"ACT-{uuid4().hex[:12].upper()}")
    task_id: str
    session_id: str
    correlation_id: str = Field(default_factory=lambda: uuid4().hex)

    actor: str = Field(default="jarvis.model")
    capability: str  # Canonical identifier (e.g. "filesystem.read", "process.start")
    arguments: Dict[str, Any] = Field(default_factory=dict)
    target: ExecutionTarget = ExecutionTarget.HOST

    risk_tier: RiskTier = RiskTier.LOW
    provenance: str = Field(default="user_instruction")
    approval_required: bool = False
    approval_id: Optional[str] = None

    timestamp: datetime = Field(default_factory=utc_now)


class ActionResult(BaseModel):
    """Authoritative result returned from Action Broker execution."""

    action_id: str
    task_id: str
    status: ActionStatus
    execution_target: ExecutionTarget

    output: Any = None
    error: Optional[str] = None
    exit_code: Optional[int] = None

    # Real-world verification & observation
    observation: Dict[str, Any] = Field(default_factory=dict)
    verified: bool = False
    duration_ms: float = 0.0

    timestamp: datetime = Field(default_factory=utc_now)
    audit_logged: bool = False
