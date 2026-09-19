"""Authoritative Action Broker for JARVIS.

The ONLY trusted bridge between cognitive intent and real-world execution.
Enforces validation, policy authorization, target dispatch, observation,
and immutable audit trails.
"""

import asyncio
import inspect
import time
from typing import Optional

from jarvis.actions.registry import capability_registry
from jarvis.contracts.action import (
    ActionRequest,
    ActionResult,
    ActionStatus,
)
from jarvis.contracts.policy import PolicyVerdict
from jarvis.policy.engine import PolicyEngine, policy_engine
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger


class ActionBroker:
    """Canonical executor that governs and dispatches real-world actions."""

    def __init__(
        self,
        policy: Optional[PolicyEngine] = None,
        database: Optional[DatabaseEngine] = None,
    ) -> None:
        self.policy = policy or policy_engine
        self.db = database or db

    async def execute(self, request: ActionRequest) -> ActionResult:
        """Process an ActionRequest through the full security and execution pipeline."""
        start_time = time.perf_counter()

        # Step 1: Persist initial ActionRequest
        self.db.save_action_request(request)

        # Step 2: Policy Authorization
        decision = self.policy.evaluate(request)

        # Handle Policy Verdicts
        if decision.verdict == PolicyVerdict.DENY:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            result = ActionResult(
                action_id=request.action_id,
                task_id=request.task_id,
                status=ActionStatus.DENIED,
                execution_target=request.target,
                error=f"Policy Denial: {decision.reason}",
                duration_ms=duration_ms,
                audit_logged=True,
            )
            self.db.save_action_result(result)
            return result

        if decision.verdict == PolicyVerdict.ASK:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            appr_id = decision.approval_request.approval_id if decision.approval_request else None
            result = ActionResult(
                action_id=request.action_id,
                task_id=request.task_id,
                status=ActionStatus.PENDING_APPROVAL,
                execution_target=request.target,
                error=f"Action paused awaiting operator approval [ticket: {appr_id}]",
                observation={"approval_ticket": appr_id},
                duration_ms=duration_ms,
                audit_logged=True,
            )
            self.db.save_action_result(result)
            return result

        # Step 3: Resolve Execution Handler
        handler = capability_registry.get_handler(request.capability)
        if not handler:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            result = ActionResult(
                action_id=request.action_id,
                task_id=request.task_id,
                status=ActionStatus.FAILED,
                execution_target=request.target,
                error=f"No execution handler registered for capability: '{request.capability}'",
                duration_ms=duration_ms,
                audit_logged=True,
            )
            self.db.save_action_result(result)
            return result

        # Step 4: Execute within Controlled Boundary
        try:
            logger.info(
                f"Executing action [{request.action_id}] capability '{request.capability}' on {request.target.value}"
            )
            if inspect.iscoroutinefunction(handler):
                output = await handler(request)
            else:
                output = await asyncio.to_thread(handler, request)

            duration_ms = (time.perf_counter() - start_time) * 1000.0
            result = ActionResult(
                action_id=request.action_id,
                task_id=request.task_id,
                status=ActionStatus.SUCCEEDED,
                execution_target=request.target,
                output=output,
                observation={
                    "status": "success",
                    "capability": request.capability,
                },
                verified=True,
                duration_ms=duration_ms,
                audit_logged=True,
            )
        except Exception as err:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(
                f"Execution failed for action [{request.action_id}]: {err}",
                exc_info=True,
            )
            result = ActionResult(
                action_id=request.action_id,
                task_id=request.task_id,
                status=ActionStatus.FAILED,
                execution_target=request.target,
                error=str(err),
                observation={
                    "status": "error",
                    "error_class": err.__class__.__name__,
                },
                verified=False,
                duration_ms=duration_ms,
                audit_logged=True,
            )

        # Step 5: Persist Result & Audit
        self.db.save_action_result(result)
        self.db.log_audit_event(
            event_type="ACTION_COMPLETED",
            action_id=request.action_id,
            task_id=request.task_id,
            session_id=request.session_id,
            capability=request.capability,
            verdict=result.status.value,
            details={"duration_ms": duration_ms, "error": result.error},
        )
        return result


# Global singleton instance
action_broker = ActionBroker()
