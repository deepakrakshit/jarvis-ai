"""Authoritative Policy Engine for JARVIS.

Core Invariant: The model is NOT the trust boundary.
Evaluates every proposed action request deterministically against capability,
resource targets, risk tiers, and operator approvals.
"""

from pathlib import Path
from typing import Optional

from jarvis.config import settings
from jarvis.contracts.action import ActionRequest, RiskTier
from jarvis.contracts.policy import ApprovalStatus, PolicyDecision, PolicyVerdict
from jarvis.policy.approvals import ApprovalManager, approval_manager
from jarvis.policy.firewall import (
    CAPABILITY_FILESYSTEM_DELETE,
    CAPABILITY_FILESYSTEM_WRITE,
    CAPABILITY_PROCESS_TERMINATE,
    CAPABILITY_RISK_MAP,
    CAPABILITY_SHELL_EXECUTE,
    is_dangerous_command,
)
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger


class PolicyEngine:
    """Authoritative evaluator for action permissions and security boundaries."""

    def __init__(
        self,
        workspace_dir: Optional[Path] = None,
        approvals: Optional[ApprovalManager] = None,
        database: Optional[DatabaseEngine] = None,
    ) -> None:
        self.workspace_dir = workspace_dir or settings.WORKSPACE_DIR
        self.approvals = approvals or approval_manager
        self.db = database or db

    def evaluate(self, request: ActionRequest) -> PolicyDecision:
        """Evaluate an ActionRequest and return an authoritative PolicyDecision."""
        capability = request.capability

        # 1. Capability Existence Check
        if capability not in CAPABILITY_RISK_MAP:
            decision = PolicyDecision(
                action_id=request.action_id,
                verdict=PolicyVerdict.DENY,
                reason=f"Unknown or unauthorized capability: '{capability}'",
                capability=capability,
                risk_tier=RiskTier.CRITICAL.value,
            )
            self._log_decision(request, decision)
            return decision

        risk_tier = CAPABILITY_RISK_MAP[capability]

        # 2. Dangerous Shell Commands Check (Fail-Closed)
        if capability == CAPABILITY_SHELL_EXECUTE:
            command = str(request.arguments.get("command", ""))
            if is_dangerous_command(command):
                decision = PolicyDecision(
                    action_id=request.action_id,
                    verdict=PolicyVerdict.DENY,
                    reason=f"Command matches prohibited destructive pattern: '{command}'",
                    capability=capability,
                    risk_tier=RiskTier.CRITICAL.value,
                )
                self._log_decision(request, decision)
                return decision

        # 3. Existing Approval Verification
        if request.approval_id:
            approval = self.approvals.get_pending(request.approval_id)
            if approval and approval.status == ApprovalStatus.APPROVED:
                decision = PolicyDecision(
                    action_id=request.action_id,
                    verdict=PolicyVerdict.ALLOW,
                    reason=f"Explicitly approved by operator [{approval.decided_by}]",
                    capability=capability,
                    risk_tier=risk_tier.value,
                    approval_request=approval,
                )
                self._log_decision(request, decision)
                return decision
            elif approval and approval.status == ApprovalStatus.REJECTED:
                decision = PolicyDecision(
                    action_id=request.action_id,
                    verdict=PolicyVerdict.DENY,
                    reason=f"Explicitly rejected by operator: {approval.rejection_reason or 'No reason provided'}",
                    capability=capability,
                    risk_tier=risk_tier.value,
                    approval_request=approval,
                )
                self._log_decision(request, decision)
                return decision

        # 4. Destructive / High Risk Actions Require Interactive Approval
        if risk_tier == RiskTier.HIGH or capability in (
            CAPABILITY_FILESYSTEM_DELETE,
            CAPABILITY_PROCESS_TERMINATE,
        ):
            target_desc = str(
                request.arguments.get("path")
                or request.arguments.get("pid")
                or request.arguments.get("command")
                or "system"
            )
            approval_req = self.approvals.create_request(
                action_id=request.action_id,
                task_id=request.task_id,
                session_id=request.session_id,
                capability=capability,
                target_resource=target_desc,
                risk_summary=f"High-risk action: {capability} on {target_desc}",
                arguments_summary=request.arguments,
            )
            decision = PolicyDecision(
                action_id=request.action_id,
                verdict=PolicyVerdict.ASK,
                reason="High-risk operation requires explicit operator approval",
                capability=capability,
                risk_tier=risk_tier.value,
                approval_request=approval_req,
            )
            self._log_decision(request, decision)
            return decision

        # 5. Filesystem Write Path Inspection
        if capability == CAPABILITY_FILESYSTEM_WRITE:
            target_path_str = str(request.arguments.get("path", ""))
            target_path = Path(target_path_str).resolve()
            workspace = self.workspace_dir.resolve()
            # Allow workspace writes automatically; ask for external system paths
            try:
                target_path.relative_to(workspace)
                verdict = PolicyVerdict.ALLOW
                reason = "Workspace file write permitted."
            except ValueError:
                # Outside workspace
                approval_req = self.approvals.create_request(
                    action_id=request.action_id,
                    task_id=request.task_id,
                    session_id=request.session_id,
                    capability=capability,
                    target_resource=str(target_path),
                    risk_summary="Filesystem modification outside workspace",
                    arguments_summary=request.arguments,
                )
                decision = PolicyDecision(
                    action_id=request.action_id,
                    verdict=PolicyVerdict.ASK,
                    reason="Writing outside workspace boundaries requires approval",
                    capability=capability,
                    risk_tier=RiskTier.HIGH.value,
                    approval_request=approval_req,
                )
                self._log_decision(request, decision)
                return decision

            decision = PolicyDecision(
                action_id=request.action_id,
                verdict=verdict,
                reason=reason,
                capability=capability,
                risk_tier=risk_tier.value,
            )
            self._log_decision(request, decision)
            return decision

        # 6. Default Allow for Read-Only and Low Risk Operations
        decision = PolicyDecision(
            action_id=request.action_id,
            verdict=PolicyVerdict.ALLOW,
            reason=f"Capability '{capability}' is within allowed policy tier [{risk_tier.value}]",
            capability=capability,
            risk_tier=risk_tier.value,
        )
        self._log_decision(request, decision)
        return decision

    def _log_decision(self, request: ActionRequest, decision: PolicyDecision) -> None:
        """Record policy evaluation in audit trail."""
        logger.info(
            f"Policy Decision [{decision.verdict.value}]: {request.capability} - {decision.reason}"
        )
        self.db.log_audit_event(
            event_type="POLICY_EVALUATION",
            action_id=request.action_id,
            task_id=request.task_id,
            session_id=request.session_id,
            capability=request.capability,
            verdict=decision.verdict.value,
            details={
                "reason": decision.reason,
                "risk_tier": decision.risk_tier,
                "approval_id": (
                    decision.approval_request.approval_id if decision.approval_request else None
                ),
            },
        )


# Global singleton instance
policy_engine = PolicyEngine()
