"""Data Models and Protocols for ACP and External Coding Agent Harnesses.

Implements Sections 30, 143, and 144 of ARCHITECTURE.md:
- Agent Control Protocol session contracts
- Workspace boundary definitions
- Permission relay request/response
- Section 144 Structured Output Validation schema
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class AcpRole(str, Enum):
    """Specialist roles for external coding agent harnesses."""

    CODING = "coding"
    REVIEW = "review"
    SPECIALIST = "specialist"


class AcpSessionState(str, Enum):
    """Lifecycle states for ACP sessions."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AcpSessionSpec(BaseModel):
    """Specification defining the constraints and boundary of an ACP session."""

    session_id: str = Field(default_factory=lambda: f"acp_{uuid.uuid4().hex[:12]}")
    harness_type: str = "coding_harness"
    model: str = "GPT-OSS 120B"
    repo_path: Path
    branch: Optional[str] = None
    allowed_tools: List[str] = Field(
        default_factory=lambda: [
            "read_file",
            "write_file",
            "list_dir",
            "run_tests",
            "git_status",
            "git_diff",
        ]
    )
    timeout_seconds: float = 300.0
    network_allowed: bool = False
    read_only: bool = False
    environment: Dict[str, str] = Field(default_factory=dict)
    parent_task_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AcpPermissionOption(BaseModel):
    """Selectable permission option presented to user or supervisor."""

    option_id: str
    name: str
    kind: Literal["allow_once", "allow_always", "reject_once"]


class AcpPermissionRequest(BaseModel):
    """Permission request payload when a command or action requires approval."""

    request_id: str = Field(default_factory=lambda: f"perm_{uuid.uuid4().hex[:10]}")
    session_id: str
    tool_call_id: str
    command: str
    title: str = "Action Approval Requested"
    options: List[AcpPermissionOption] = Field(
        default_factory=lambda: [
            AcpPermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
            AcpPermissionOption(option_id="allow-always", name="Allow always", kind="allow_always"),
            AcpPermissionOption(option_id="deny", name="Deny", kind="reject_once"),
        ]
    )


class AcpPermissionResponse(BaseModel):
    """Resolved response for an ACP permission request."""

    request_id: str
    decision: Literal["allow-once", "allow-always", "deny"]
    selected_option_id: Optional[str] = None
    reason: Optional[str] = None


class AcpValidationResult(BaseModel):
    """Section 144 authoritative structured result contract.

    Parent JARVIS agents verify this structured result rather than
    trusting unverified conversational prose.
    """

    status: Literal["completed", "failed", "rejected"]
    summary: str
    changed_files: List[str] = Field(default_factory=list)
    tests_run: List[str] = Field(default_factory=list)
    tests_passed: bool = False
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    remaining_risks: List[str] = Field(default_factory=list)
    raw_output: str = ""
    error: Optional[str] = None


class AcpEvent(BaseModel):
    """Event ledger item tracking execution in an ACP session."""

    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    session_id: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)
