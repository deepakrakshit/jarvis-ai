"""Permission Relay Bridging ACP Tool Calls to JARVIS Policy Engine.

Implements Sections 30 and 143 of ARCHITECTURE.md:
- Adapts external coding agent tool/command requests to ActionRequests
- Evaluates actions deterministically against the capability firewall
- Manages approval escalation and session-level 'allow-always' grants
"""

import uuid
from typing import Any, Callable, Dict, Literal, Optional, Set

from jarvis.contracts.action import ActionRequest, ExecutionTarget, RiskTier
from jarvis.contracts.policy import PolicyVerdict
from jarvis.policy.engine import PolicyEngine
from jarvis.policy.firewall import (
    CAPABILITY_FILESYSTEM_READ,
    CAPABILITY_FILESYSTEM_WRITE,
    CAPABILITY_SHELL_EXECUTE,
)
from jarvis.telemetry import logger

from .models import (
    AcpPermissionOption,
    AcpPermissionRequest,
    AcpPermissionResponse,
    AcpSessionSpec,
)

# Tool name to capability mapping
TOOL_CAPABILITY_MAP: Dict[str, str] = {
    "read_file": CAPABILITY_FILESYSTEM_READ,
    "list_dir": CAPABILITY_FILESYSTEM_READ,
    "git_status": CAPABILITY_FILESYSTEM_READ,
    "git_diff": CAPABILITY_FILESYSTEM_READ,
    "write_file": CAPABILITY_FILESYSTEM_WRITE,
    "delete_file": "filesystem.delete",
    "run_tests": CAPABILITY_SHELL_EXECUTE,
    "execute_command": CAPABILITY_SHELL_EXECUTE,
    "bash": CAPABILITY_SHELL_EXECUTE,
    "terminal": CAPABILITY_SHELL_EXECUTE,
}


class AcpPermissionRelay:
    """Relays tool requests from external agent harnesses to the Policy Engine."""

    def __init__(
        self,
        policy_engine: PolicyEngine,
        session_spec: AcpSessionSpec,
        approval_callback: Optional[Callable[[AcpPermissionRequest], AcpPermissionResponse]] = None,
    ) -> None:
        self.policy_engine = policy_engine
        self.session_spec = session_spec
        self.approval_callback = approval_callback
        self._always_allowed_cache: Set[str] = set()
        self._pending_requests: Dict[str, AcpPermissionRequest] = {}

    def _cache_key(self, tool_name: str, identifier: str) -> str:
        return f"{tool_name}:{identifier}"

    def evaluate_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> AcpPermissionResponse:
        """Evaluate an external agent tool call against policy rules."""
        call_id = tool_call_id or f"call_{uuid.uuid4().hex[:8]}"

        # 1. Check session tool allowlist
        if tool_name not in self.session_spec.allowed_tools:
            logger.warning(
                f"ACP session {self.session_spec.session_id}: "
                f"tool '{tool_name}' not in allowed tools: {self.session_spec.allowed_tools}"
            )
            return AcpPermissionResponse(
                request_id=f"perm_{uuid.uuid4().hex[:10]}",
                decision="deny",
                reason=f"Tool '{tool_name}' is not authorized for this session.",
            )

        # 2. Check read-only session enforcement
        if self.session_spec.read_only and tool_name in (
            "write_file",
            "delete_file",
            "execute_command",
            "bash",
        ):
            return AcpPermissionResponse(
                request_id=f"perm_{uuid.uuid4().hex[:10]}",
                decision="deny",
                reason=f"Session is read-only; tool '{tool_name}' is forbidden.",
            )

        # 3. Check session-level allow-always cache
        cmd_text = str(arguments.get("command", arguments.get("path", "")))
        cache_key = self._cache_key(tool_name, cmd_text)
        if cache_key in self._always_allowed_cache:
            return AcpPermissionResponse(
                request_id=f"perm_{uuid.uuid4().hex[:10]}",
                decision="allow-once",
                selected_option_id="allow-always",
            )

        # 4. Map to Policy Engine ActionRequest
        capability = TOOL_CAPABILITY_MAP.get(tool_name, CAPABILITY_SHELL_EXECUTE)

        action_req = ActionRequest(
            task_id=self.session_spec.parent_task_id or f"task_{self.session_spec.session_id}",
            session_id=self.session_spec.session_id,
            capability=capability,
            target=ExecutionTarget.SANDBOX,
            arguments=arguments,
            risk_tier=(
                RiskTier.READ_ONLY if capability == CAPABILITY_FILESYSTEM_READ else RiskTier.MEDIUM
            ),
        )

        decision = self.policy_engine.evaluate(action_req)

        if decision.verdict == PolicyVerdict.ALLOW:
            return AcpPermissionResponse(
                request_id=f"perm_{uuid.uuid4().hex[:10]}",
                decision="allow-once",
                selected_option_id="allow-once",
            )

        if decision.verdict == PolicyVerdict.DENY:
            logger.warning(f"ACP tool '{tool_name}' denied by policy: {decision.reason}")
            return AcpPermissionResponse(
                request_id=f"perm_{uuid.uuid4().hex[:10]}",
                decision="deny",
                reason=decision.reason,
            )

        # 5. PolicyVerdict.REQUIRE_APPROVAL: Build permission escalation request
        req = AcpPermissionRequest(
            session_id=self.session_spec.session_id,
            tool_call_id=call_id,
            command=cmd_text or tool_name,
            title=f"Approval needed for {tool_name}: {cmd_text}",
            options=[
                AcpPermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
                AcpPermissionOption(
                    option_id="allow-always", name="Allow always", kind="allow_always"
                ),
                AcpPermissionOption(option_id="deny", name="Deny", kind="reject_once"),
            ],
        )
        self._pending_requests[req.request_id] = req

        # If an interactive or automated supervisor callback is provided, invoke it
        if self.approval_callback:
            resp = self.approval_callback(req)
            if resp.decision == "allow-always":
                self._always_allowed_cache.add(cache_key)
            return resp

        # Default fallback if no callback registered: hold closed (deny)
        return AcpPermissionResponse(
            request_id=req.request_id,
            decision="deny",
            reason="Approval required but no interactive supervisor callback is attached.",
        )

    def resolve_permission(
        self,
        request_id: str,
        decision: Literal["allow-once", "allow-always", "deny"],
        tool_name: str = "",
        identifier: str = "",
    ) -> AcpPermissionResponse:
        """Explicitly resolve a pending permission request."""
        if decision not in ("allow-once", "allow-always", "deny"):
            raise ValueError(f"Invalid permission decision: {decision}")

        if decision == "allow-always" and tool_name and identifier:
            self._always_allowed_cache.add(self._cache_key(tool_name, identifier))

        self._pending_requests.pop(request_id, None)
        return AcpPermissionResponse(
            request_id=request_id,
            decision=decision,
            selected_option_id=decision,
        )
