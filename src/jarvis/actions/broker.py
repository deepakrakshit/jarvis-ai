"""Authoritative Action Broker for JARVIS.

The ONLY trusted bridge between cognitive intent and real-world execution.
Enforces validation, policy authorization, target dispatch, observation,
and immutable audit trails.
"""

import asyncio
import inspect
import time
from pathlib import Path
from typing import Any, Optional, Tuple

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

            if inspect.iscoroutine(output):
                output = await output

            duration_ms = (time.perf_counter() - start_time) * 1000.0
            verified, verification_failed, verification_reason = self._verify_execution(
                request, output
            )
            result_status = ActionStatus.FAILED if verification_failed else ActionStatus.SUCCEEDED
            result = ActionResult(
                action_id=request.action_id,
                task_id=request.task_id,
                status=result_status,
                execution_target=request.target,
                output=output,
                error=verification_reason if verification_failed else None,
                observation={
                    "status": "success",
                    "capability": request.capability,
                    "verification": "VERIFIED" if verified else "UNVERIFIED",
                    "verification_reason": verification_reason,
                },
                verified=verified,
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

    @staticmethod
    def _verify_execution(request: ActionRequest, output: Any) -> Tuple[bool, bool, str]:
        """Verify supported postconditions without treating handler return as proof."""
        if isinstance(output, dict) and isinstance(output.get("verified"), bool):
            return output["verified"], False, "Handler supplied explicit verification metadata."

        capability = request.capability
        arguments = request.arguments

        try:
            if capability == "filesystem.read":
                path = Path(str(arguments["path"])).resolve()
                return path.is_file() and isinstance(output, str), False, "Read path exists."

            if capability == "filesystem.write":
                path = Path(str(arguments["path"])).resolve()
                if not path.is_file():
                    return False, True, "Write completed without a regular-file postcondition."
                expected = arguments.get("content")
                if isinstance(expected, str) and path.read_text(encoding="utf-8") != expected:
                    return False, True, "Write postcondition did not match requested content."
                return True, False, "Written content matches the requested file."

            if capability == "filesystem.delete":
                path = Path(str(arguments["path"])).resolve()
                return not path.exists(), False, "Deleted path is absent."

            if capability == "filesystem.list":
                return (
                    isinstance(output, list),
                    False,
                    "Directory listing returned a structured list.",
                )

            if capability == "filesystem.search":
                return (
                    isinstance(output, dict) and isinstance(output.get("matches"), list),
                    False,
                    "File search returned structured matches.",
                )

            if capability == "artifact.deliver":
                path = output.get("artifact_path") if isinstance(output, dict) else None
                return (
                    isinstance(output, dict)
                    and output.get("authorized") is True
                    and isinstance(path, str)
                    and Path(path).is_file(),
                    False,
                    "Artifact authorization and file presence were checked.",
                )

            if capability == "shell.execute":
                if not isinstance(output, dict):
                    return False, False, "Shell output did not include an exit status."
                if output.get("timed_out"):
                    return False, True, "Shell command timed out."
                if output.get("exit_code") != 0:
                    return False, True, "Shell command returned a non-zero exit code."
                return True, False, "Shell command returned exit code zero."

            if capability == "system.info":
                required = {"os", "cpu_count", "memory_total_gb"}
                return (
                    isinstance(output, dict) and required.issubset(output),
                    False,
                    "System information contains required telemetry fields.",
                )

            if capability == "browser.navigate":
                status_code = output.get("status_code") if isinstance(output, dict) else None
                return (
                    isinstance(output, dict)
                    and isinstance(output.get("url"), str)
                    and isinstance(status_code, int)
                    and status_code < 400,
                    False,
                    "Browser navigation returned a successful response and URL.",
                )

            if capability == "browser.snapshot":
                return (
                    isinstance(output, dict) and isinstance(output.get("text"), str),
                    False,
                    "Browser snapshot returned structured text.",
                )
        except (KeyError, OSError, UnicodeError, TypeError, ValueError) as err:
            return False, True, f"Verification failed: {err}"

        return False, False, "No capability-specific postcondition is available."


# Global singleton instance
action_broker = ActionBroker()
