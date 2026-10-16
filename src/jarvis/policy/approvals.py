"""Operator Approval Management for Gated Privileged Actions.

Manages interactive approval tickets, expiration, and audit states.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from jarvis.config import settings
from jarvis.contracts.policy import ApprovalRequest, ApprovalStatus
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger


class ApprovalManager:
    """Tracks and resolves interactive approval tickets."""

    def __init__(self, database: Optional[DatabaseEngine] = None) -> None:
        self.db = database or db
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
        ttl_seconds: Optional[int] = None,
    ) -> ApprovalRequest:
        """Create and register a pending approval request."""
        configured_ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else getattr(settings, "APPROVAL_TTL_SECONDS", 300)
        )
        ttl = max(1, int(configured_ttl))
        request = ApprovalRequest(
            action_id=action_id,
            task_id=task_id,
            session_id=session_id,
            capability=capability,
            target_resource=target_resource,
            risk_summary=risk_summary,
            arguments_summary=arguments_summary or {},
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
        )
        self._pending_approvals[request.approval_id] = request
        self.db.save_approval_request(request)
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
        request = self.get_pending(approval_id)
        if not request:
            logger.error(f"Approval request {approval_id} not found.")
            return None

        if request.status != ApprovalStatus.PENDING:
            logger.warning(
                f"Approval request {approval_id} is already {request.status.value} and cannot be resolved again."
            )
            return None

        now = datetime.now(timezone.utc)
        if request.expires_at and now >= request.expires_at:
            self._expire(request)
            return None

        new_status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        if not self.db.resolve_approval(
            approval_id=request.approval_id,
            status=new_status,
            decided_by=operator_identity,
            rejection_reason=rejection_reason,
        ):
            return None

        request.status = new_status
        request.decided_at = now
        request.decided_by = operator_identity
        request.rejection_reason = rejection_reason

        # Log audit event
        self.db.log_audit_event(
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
        """Get an approval request and expire it before it can be used."""
        # Durable storage is authoritative so another manager/process cannot leave
        # this manager using a stale pending decision from its local cache.
        request = self.db.get_approval_request(approval_id)
        if request is None:
            request = self._pending_approvals.get(approval_id)
        elif request:
            self._pending_approvals[approval_id] = request

        if request and request.status == ApprovalStatus.PENDING:
            now = datetime.now(timezone.utc)
            if request.expires_at and now >= request.expires_at:
                self._expire(request)
                refreshed = self.db.get_approval_request(approval_id)
                if refreshed:
                    request = refreshed
                    self._pending_approvals[approval_id] = refreshed
        return request

    def cancel(
        self,
        approval_id: str,
        operator_identity: str = "system",
        reason: str = "Approval cancelled",
    ) -> Optional[ApprovalRequest]:
        """Revoke a pending approval so it cannot authorize execution."""
        request = self.get_pending(approval_id)
        if not request or request.status != ApprovalStatus.PENDING:
            return None
        if not self.db.resolve_approval(
            approval_id=approval_id,
            status=ApprovalStatus.REVOKED,
            decided_by=operator_identity,
            rejection_reason=reason,
        ):
            return None
        request.status = ApprovalStatus.REVOKED
        request.decided_at = datetime.now(timezone.utc)
        request.decided_by = operator_identity
        request.rejection_reason = reason
        self.db.log_audit_event(
            event_type="APPROVAL_REVOKED",
            action_id=request.action_id,
            task_id=request.task_id,
            session_id=request.session_id,
            capability=request.capability,
            verdict=ApprovalStatus.REVOKED.value,
            details={
                "approval_id": approval_id,
                "decided_by": operator_identity,
                "reason": reason,
            },
        )
        return request

    def _expire(self, request: ApprovalRequest) -> None:
        """Persist timeout state exactly once for an expired request."""
        if request.status != ApprovalStatus.PENDING:
            return
        resolved = self.db.resolve_approval(
            approval_id=request.approval_id,
            status=ApprovalStatus.TIMED_OUT,
            decided_by="system",
            rejection_reason="Approval expired",
        )
        if resolved:
            request.status = ApprovalStatus.TIMED_OUT
            request.decided_at = datetime.now(timezone.utc)
            request.decided_by = "system"
            request.rejection_reason = "Approval expired"
            self.db.log_audit_event(
                event_type="APPROVAL_TIMED_OUT",
                action_id=request.action_id,
                task_id=request.task_id,
                session_id=request.session_id,
                capability=request.capability,
                verdict=ApprovalStatus.TIMED_OUT.value,
                details={"approval_id": request.approval_id},
            )


# Global singleton instance
approval_manager = ApprovalManager()
