"""JARVIS Policy Decisions and Autonomy Level Contracts.

Defines the decision taxonomy, decision models, and autonomy levels (0-5)
governing all capability execution across native, MCP, and external tools.
"""

from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AutonomyLevel(IntEnum):
    """Autonomy Level scale (0-5) governing agent independent authority.

    - Level 0: Observe only (zero tool executions permitted).
    - Level 1: Recommend action (human must manually initiate execution).
    - Level 2: Auto-execute safe, read-only operations.
    - Level 3: Auto-execute bounded mutations strictly within approved workspaces.
    - Level 4: Autonomous execution within pre-allocated token and operation budget.
    - Level 5: Mandatory human-in-the-loop approval required for all mutations.
    """

    OBSERVE_ONLY = 0
    RECOMMEND_ONLY = 1
    AUTO_READ_ONLY = 2
    AUTO_BOUNDED_MUTATION = 3
    AUTONOMOUS_BUDGETED = 4
    MANDATORY_APPROVAL_MUTATIONS = 5


class PolicyDecisionType(StrEnum):
    """Resultant policy verdict for an action proposal."""

    ALLOW = "ALLOW"
    """Action is safe and within current autonomy bounds; may proceed directly."""

    DENY = "DENY"
    """Action is strictly blocked by security policy, invariants, or boundaries."""

    REQUIRE_HITL = "REQUIRE_HITL"
    """Action requires explicit human-in-the-loop approval before execution."""

    REQUIRE_REVALIDATION = "REQUIRE_REVALIDATION"
    """Action approved earlier requires immediate re-check before commit."""

    REQUIRE_SANDBOX = "REQUIRE_SANDBOX"
    """Action is permitted only if executed within an isolated process sandbox."""

    REQUIRE_EXTERNAL_VERIFICATION = "REQUIRE_EXTERNAL_VERIFICATION"
    """Action must produce an external cryptographic/ETag verification receipt."""

    RATE_LIMITED = "RATE_LIMITED"
    """Action rejected due to velocity or frequency threshold violations."""

    SPEND_LIMITED = "SPEND_LIMITED"
    """Action rejected due to budget, token, or financial spend limit caps."""


class EffectAuthorization(BaseModel):
    """Hardened commit-time authorization record (ARCHITECTURE.md Layer 13).

    Binds human approval or automated authorization to exact canonical arguments,
    target resource, witness state, and short-lived TTL to eliminate TOCTOU risks.
    """

    proposal_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    user_id: str
    session_id: UUID
    agent_id: str
    tool_id: str
    tool_version: str = "1.0.0"
    canonical_arguments_hash: str
    """SHA-256 digest of canonically ordered arguments JSON."""

    target_resource: str
    """Specific file path, URL, process, or entity being modified."""

    target_witness_hash: str | None = None
    """ETag, git SHA, or byte digest observed immediately prior to commit."""

    data_scope: str = "workspace"
    policy_version: str = "1.0.0"
    workspace_version: str = "1.0.0"
    approval_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    """Strict TTL expiration (default <= 5 minutes)."""

    nonce: str
    """Single-use cryptographic nonce preventing replay attacks."""

    used: bool = False
    """Ensures single-use commit-time authorization."""

    def is_valid(self, at_time: datetime | None = None) -> bool:
        """Return True if authorization has not expired and has not been used."""
        now = at_time or datetime.now(UTC)
        if self.used:
            return False
        return now <= self.expires_at

    def mark_used(self) -> None:
        """Mark authorization as consumed to prevent replay."""
        if self.used:
            raise ValueError("EffectAuthorization has already been consumed.")
        self.used = True


class PolicyDecision(BaseModel):
    """Comprehensive policy decision verdict with risk score and obligations."""

    decision: PolicyDecisionType
    reason: str
    risk_score: float = Field(ge=0.0, le=1.0)
    matched_rule_id: str | None = None
    obligations: list[str] = Field(default_factory=list)
    authorization: EffectAuthorization | None = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    context_snapshot: dict[str, Any] = Field(default_factory=dict)

    def is_allowed(self) -> bool:
        """Return True if the decision allows immediate or sandboxed execution."""
        return self.decision in (PolicyDecisionType.ALLOW, PolicyDecisionType.REQUIRE_SANDBOX)

    def requires_approval(self) -> bool:
        """Return True if execution is paused pending human approval."""
        return self.decision == PolicyDecisionType.REQUIRE_HITL
