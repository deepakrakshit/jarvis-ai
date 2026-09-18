"""JARVIS 5-Step Human-In-The-Loop Approval Pipeline and Commit-Time Authorization.

Implements Layer 13 of the canonical architecture:
1. Pre-Approval Guard (static policy + dynamic risk validation + draft creation)
2. Tamper-Resistant User Interrupt (renders control plane diff, defends against OWASP "Lies-in-the-Loop")
3. Post-Approval Revalidation (verifies argument hash, TTL, and workspace state)
4. Commit-Time Authorization (binds authorization token to target witness hash immediately before durable effect)
5. Action Broker Dispatch Handoff
"""

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from jarvis.core.exceptions import PolicyViolationError
from jarvis.core.logging import get_logger
from jarvis.core.policy.decision import EffectAuthorization

logger = get_logger(__name__)


class ApprovalStatus(StrEnum):
    """Lifecycle status of a human approval request."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


def compute_canonical_arguments_hash(arguments: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of canonical arguments JSON."""
    canonical_json = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


class ApprovalRequest(BaseModel):
    """Structured control-plane approval prompt for human review.

    Defends against OWASP "Lies-in-the-Loop" / Dialog Forging:
    Parameters, diffs, resource URIs, and risk scores are derived strictly
    from the deterministic Control Plane, preventing deceptive model prose.
    """

    request_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    tool_id: str
    target_resource: str
    arguments: dict[str, Any]
    canonical_arguments_hash: str
    risk_score: float
    risk_factors: list[str] = Field(default_factory=list)
    proposed_diff: str | None = None
    model_explanation: str | None = None
    """Explanatory model text, strictly flagged as unverified in the UI."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    resolved_by: str | None = None
    resolved_at: datetime | None = None

    def is_expired(self, at_time: datetime | None = None) -> bool:
        """Return True if approval window has lapsed."""
        now = at_time or datetime.now(UTC)
        return now > self.expires_at


class HITLPipeline:
    """Manages the 5-step approval pipeline and commit-time authorization validation."""

    def __init__(self, default_ttl_seconds: int = 300) -> None:
        self.default_ttl = timedelta(seconds=default_ttl_seconds)
        self._pending_requests: dict[UUID, ApprovalRequest] = {}
        self._authorizations: dict[UUID, EffectAuthorization] = {}

    def create_approval_request(
        self,
        task_id: UUID,
        tool_id: str,
        arguments: dict[str, Any],
        target_resource: str,
        risk_score: float,
        risk_factors: list[str] | None = None,
        proposed_diff: str | None = None,
        model_explanation: str | None = None,
    ) -> ApprovalRequest:
        """Step 1: Create a tamper-resistant approval request from the control plane."""
        args_hash = compute_canonical_arguments_hash(arguments)
        now = datetime.now(UTC)
        req = ApprovalRequest(
            task_id=task_id,
            tool_id=tool_id,
            target_resource=target_resource,
            arguments=arguments,
            canonical_arguments_hash=args_hash,
            risk_score=risk_score,
            risk_factors=risk_factors or [],
            proposed_diff=proposed_diff,
            model_explanation=model_explanation,
            created_at=now,
            expires_at=now + self.default_ttl,
        )
        self._pending_requests[req.request_id] = req
        logger.info(
            "approval_request_created",
            request_id=str(req.request_id),
            task_id=str(task_id),
            tool_id=tool_id,
            target=target_resource,
            risk_score=risk_score,
        )
        return req

    def resolve_approval(
        self,
        request_id: UUID,
        approved: bool,
        user_id: str = "human_operator",
        session_id: UUID | None = None,
        agent_id: str = "core_agent",
    ) -> EffectAuthorization | None:
        """Step 2: Process human resolution (Tamper-Resistant User Interrupt)."""
        req = self._pending_requests.get(request_id)
        if not req:
            raise PolicyViolationError(f"Approval request '{request_id}' not found.")

        now = datetime.now(UTC)
        if req.is_expired(now):
            req.status = ApprovalStatus.EXPIRED
            raise PolicyViolationError(
                f"Approval request '{request_id}' expired at {req.expires_at.isoformat()}."
            )

        if not approved:
            req.status = ApprovalStatus.REJECTED
            req.resolved_by = user_id
            req.resolved_at = now
            logger.warning("approval_rejected_by_user", request_id=str(request_id), user_id=user_id)
            return None

        req.status = ApprovalStatus.APPROVED
        req.resolved_by = user_id
        req.resolved_at = now

        # Generate cryptographic single-use EffectAuthorization token
        auth = EffectAuthorization(
            proposal_id=req.request_id,
            task_id=req.task_id,
            user_id=user_id,
            session_id=session_id or uuid4(),
            agent_id=agent_id,
            tool_id=req.tool_id,
            canonical_arguments_hash=req.canonical_arguments_hash,
            target_resource=req.target_resource,
            approval_timestamp=now,
            expires_at=now + self.default_ttl,
            nonce=secrets.token_hex(16),
        )
        self._authorizations[auth.proposal_id] = auth
        logger.info(
            "effect_authorized",
            proposal_id=str(auth.proposal_id),
            tool_id=auth.tool_id,
            nonce=auth.nonce[:8],
            expires_at=auth.expires_at.isoformat(),
        )
        return auth

    def find_active_authorization(
        self,
        tool_id: str,
        canonical_arguments_hash: str,
        target_resource: str | None = None,
        at_time: datetime | None = None,
    ) -> EffectAuthorization | None:
        """Find an unexpired, unconsumed EffectAuthorization matching tool and argument hash."""
        now = at_time or datetime.now(UTC)
        candidates: list[EffectAuthorization] = []
        for auth in self._authorizations.values():
            if auth.tool_id != tool_id:
                continue
            if auth.canonical_arguments_hash != canonical_arguments_hash:
                continue
            if target_resource is not None and auth.target_resource != target_resource:
                continue
            if auth.is_valid(now):
                candidates.append(auth)

        if not candidates:
            return None
        candidates.reverse()
        candidates.sort(key=lambda a: a.approval_timestamp, reverse=True)
        return candidates[0]

    def find_latest_pending_request(
        self,
        tool_id: str | None = None,
        canonical_arguments_hash: str | None = None,
        target_resource: str | None = None,
        at_time: datetime | None = None,
    ) -> ApprovalRequest | None:
        """Find the most recent valid pending ApprovalRequest."""
        now = at_time or datetime.now(UTC)
        candidates: list[ApprovalRequest] = []
        for req in self._pending_requests.values():
            if req.status != ApprovalStatus.PENDING:
                continue
            if req.is_expired(now):
                continue
            if tool_id is not None and req.tool_id != tool_id:
                continue
            if (
                canonical_arguments_hash is not None
                and req.canonical_arguments_hash != canonical_arguments_hash
            ):
                continue
            if target_resource is not None and req.target_resource != target_resource:
                continue
            candidates.append(req)

        if not candidates:
            return None
        candidates.reverse()
        candidates.sort(key=lambda r: r.created_at, reverse=True)
        return candidates[0]

    def resolve_latest_pending(
        self,
        approved: bool,
        user_id: str = "human_operator",
        session_id: UUID | None = None,
        tool_id: str | None = None,
        canonical_arguments_hash: str | None = None,
        agent_id: str = "core_agent",
    ) -> EffectAuthorization | None:
        """Resolve the most recent valid pending ApprovalRequest."""
        req = self.find_latest_pending_request(
            tool_id=tool_id,
            canonical_arguments_hash=canonical_arguments_hash,
        )
        if not req:
            return None
        return self.resolve_approval(
            request_id=req.request_id,
            approved=approved,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
        )

    def revalidate_and_commit(
        self,
        authorization: EffectAuthorization,
        actual_arguments: dict[str, Any],
        current_witness_hash: str | None = None,
    ) -> None:
        """Steps 3 & 4: Post-approval revalidation and commit-time authorization binding.

        Enforces that:
        1. Authorization has not expired (TTL check).
        2. Authorization has not been previously consumed (nonce single-use check).
        3. Canonical argument hash exactly matches actual argument payload (TOCTOU parameter tampering defense).
        4. Target witness hash matches current resource state if required (witness check).
        """
        now = datetime.now(UTC)

        # 1. Check TTL expiration
        if not authorization.is_valid(now):
            raise PolicyViolationError(
                f"Commit authorization '{authorization.proposal_id}' is invalid or expired. "
                f"Expires at {authorization.expires_at.isoformat()}, now is {now.isoformat()}."
            )

        # 2. Check canonical arguments hash
        actual_hash = compute_canonical_arguments_hash(actual_arguments)
        if actual_hash != authorization.canonical_arguments_hash:
            logger.error(
                "toctou_parameter_tampering_detected",
                expected_hash=authorization.canonical_arguments_hash,
                actual_hash=actual_hash,
            )
            raise PolicyViolationError(
                f"TOCTOU violation: Canonical argument hash '{actual_hash}' does not match "
                f"authorized argument hash '{authorization.canonical_arguments_hash}'."
            )

        # 3. Bind target witness hash if provided
        if (
            authorization.target_witness_hash is not None
            and current_witness_hash is not None
            and authorization.target_witness_hash != current_witness_hash
        ):
            raise PolicyViolationError(
                f"Target witness mismatch: state changed between approval and commit. "
                f"Expected witness '{authorization.target_witness_hash}', found '{current_witness_hash}'."
            )

        # 4. Mark single-use token as consumed
        authorization.mark_used()
        logger.info(
            "commit_authorization_verified_and_consumed",
            proposal_id=str(authorization.proposal_id),
            tool_id=authorization.tool_id,
        )
