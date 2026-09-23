"""Operator Approval Management for Gated Privileged Actions.

Manages interactive approval tickets, expiration, and audit states.
"""

from datetime import datetime, timezone
from typing import Dict, Optional

from jarvis.contracts.policy import ApprovalRequest, ApprovalStatus
from jarvis.storage.database import db
from jarvis.telemetry import logger


class ApprovalManager:
    """Tracks and resolves interactive approval tickets."""

    def __init__(self) -> None:
        self._pending_approvals: Dict[str, ApprovalRequest] = {}

    def create_request(
        self,
        action_id: str,
        task_id: str,
        session_id: str,
        capability: str,
        target_resource: str,
        risk_summary: str,
        arguments_summary: Optional[Dict[str, object]] = None,
    ) -> ApprovalRequest:
        """Create and register a pending approval request."""
        request = ApprovalRequest(
            action_id=action_id,
            task_id=task_id,
            session_id=session_id,
            capability=capability,
            target_resource=target_resource,
            risk_summary=risk_summary,
            arguments_summary=arguments_summary or {},
        )
        self._pending_approvals[request.approval_id] = request
        logger.warning(
            f"Approval required [{request.approval_id}] for {capability} targeting {target_resource} - {risk_summary}"
        )
        return request

    def resolve(
        self,
        approval_id: str,
        approved: bool,
        operator_identity: str = "human_operator",
        rejection_reason: Optional[str] = None,
    ) -> Optional[ApprovalRequest]:
        """Resolve a pending approval request."""
        request = self._pending_approvals.get(approval_id)
        if not request:
            logger.error(f"Approval request {approval_id} not found.")
            return None

        now = datetime.now(timezone.utc)
        request.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        request.decided_at = now
        request.decided_by = operator_identity
        request.rejection_reason = rejection_reason

        # Log audit event
        db.log_audit_event(
            event_type="APPROVAL_RESOLVED",
            action_id=request.action_id,
            task_id=request.task_id,
            session_id=request.session_id,
            capability=request.capability,
            verdict="APPROVED" if approved else "REJECTED",
            details={
                "approval_id": approval_id,
                "decided_by": operator_identity,
                "reason": rejection_reason,
            },
        )
        return request

    def get_pending(self, approval_id: str) -> Optional[ApprovalRequest]:
        """Get an active approval request."""
        return self._pending_approvals.get(approval_id)


# Global singleton instance
approval_manager = ApprovalManager()
