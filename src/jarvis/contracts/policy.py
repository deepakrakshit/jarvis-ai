"""Canonical Policy & Approval Contracts for JARVIS Policy Engine.

Defines PolicyVerdict, PolicyDecision, ApprovalRequest, and ApprovalStatus.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


class PolicyVerdict(str, Enum):
    """Authoritative verdict from the Policy Engine."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    ASK = "ASK"  # Requires interactive operator approval
    CONDITIONAL = "CONDITIONAL"  # Allowed under specific sandbox constraints
    DEFER = "DEFER"  # Blocked pending prerequisite event/action


class ApprovalStatus(str, Enum):
    """Operator approval workflow status."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    TIMED_OUT = "TIMED_OUT"
    REVOKED = "REVOKED"


class ApprovalRequest(BaseModel):
    """Interactive approval request presented to the operator."""

    approval_id: str = Field(default_factory=lambda: f"APPR-{uuid4().hex[:12].upper()}")
    action_id: str
    task_id: str
    session_id: str

    capability: str
    target_resource: str
    risk_summary: str
    arguments_summary: Dict[str, Any] = Field(default_factory=dict)

    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = Field(default_factory=utc_now)
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    rejection_reason: Optional[str] = None


class PolicyDecision(BaseModel):
    """Complete evaluation returned by the Policy Engine."""

    decision_id: str = Field(default_factory=lambda: f"POL-{uuid4().hex[:12].upper()}")
    action_id: str
    verdict: PolicyVerdict
    reason: str

    capability: str
    risk_tier: str
    constraints: Dict[str, Any] = Field(default_factory=dict)
    approval_request: Optional[ApprovalRequest] = None

    evaluated_at: datetime = Field(default_factory=utc_now)
