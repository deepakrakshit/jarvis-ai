"""JARVIS Agent Broker for ACP and External Coding Agent Workers.

Implements Section 30 of ARCHITECTURE.md:
"The JARVIS Agent Broker controls:
- repository path
- branch
- tools
- environment
- model
- timeout
- sandbox
- network access
- artifact output
- approval policy"
"""

from typing import Any, Callable, Dict, List, Optional

from jarvis.cognition.model_router import ModelRouter, model_router
from jarvis.contracts.model import ModelFamily, ModelInvocationRequest
from jarvis.policy.engine import PolicyEngine
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger

from .models import (
    AcpEvent,
    AcpPermissionRequest,
    AcpPermissionResponse,
    AcpSessionSpec,
    AcpSessionState,
    AcpValidationResult,
)
from .permission_relay import AcpPermissionRelay
from .sandbox import AcpSandboxError, AcpSandboxManager
from .validator import AcpOutputValidator


class JarvisAgentBroker:
    """Authoritative broker and control plane for external coding agent harnesses."""

    def __init__(
        self,
        database: Optional[DatabaseEngine] = None,
        policy_engine: Optional[PolicyEngine] = None,
        router: Optional[ModelRouter] = None,
    ) -> None:
        self.db = database or db
        self.policy_engine = policy_engine or PolicyEngine(database=self.db)
        self.router = router or model_router
        self.validator = AcpOutputValidator()
        self._active_sessions: Dict[str, AcpSessionSpec] = {}
        self._active_sandboxes: Dict[str, AcpSandboxManager] = {}
        self._active_relays: Dict[str, AcpPermissionRelay] = {}

    def _validate_model_allowlist(self, model_name: str) -> None:
        """Verify the specified model strictly belongs to the 6 approved model families."""
        approved = {m.value for m in ModelFamily}
        if model_name not in approved:
            raise ValueError(
                f"Model '{model_name}' is not in the approved JARVIS runtime allowlist: "
                f"{sorted(approved)}"
            )

    def create_session(
        self,
        spec: AcpSessionSpec,
        approval_callback: Optional[Callable[[AcpPermissionRequest], AcpPermissionResponse]] = None,
    ) -> AcpSessionSpec:
        """Initialize, sandbox, and persist a new ACP coding agent session."""
        # 1. Enforce strict 6-model runtime allowlist
        self._validate_model_allowlist(spec.model)

        # 2. Initialize sandbox boundary
        sandbox = AcpSandboxManager(repo_path=spec.repo_path, read_only=spec.read_only)

        # 3. Switch to isolated branch if requested
        if spec.branch:
            sandbox.ensure_isolated_branch(spec.branch)

        # 4. Initialize permission relay
        relay = AcpPermissionRelay(
            policy_engine=self.policy_engine,
            session_spec=spec,
            approval_callback=approval_callback,
        )

        # 5. Persist session to database
        self.db.save_acp_session(
            session_id=spec.session_id,
            harness_type=spec.harness_type,
            model=spec.model,
            repo_path=str(spec.repo_path),
            branch=spec.branch,
            state=AcpSessionState.PENDING.value,
            allowed_tools=spec.allowed_tools,
            network_allowed=spec.network_allowed,
            read_only=spec.read_only,
            parent_task_id=spec.parent_task_id,
            metadata=spec.metadata,
        )

        # 6. Record session initialization event in ledger
        init_event = AcpEvent(
            session_id=spec.session_id,
            event_type="session.created",
            payload={
                "harness_type": spec.harness_type,
                "model": spec.model,
                "repo_path": str(spec.repo_path),
                "branch": spec.branch,
                "allowed_tools": spec.allowed_tools,
            },
        )
        self.db.save_acp_event(
            event_id=init_event.event_id,
            session_id=init_event.session_id,
            event_type=init_event.event_type,
            payload=init_event.payload,
        )

        self._active_sessions[spec.session_id] = spec
        self._active_sandboxes[spec.session_id] = sandbox
        self._active_relays[spec.session_id] = relay

        logger.info(f"Initialized ACP session {spec.session_id} for model '{spec.model}'")
        return spec

    def get_sandbox(self, session_id: str) -> Optional[AcpSandboxManager]:
        """Get the active sandbox manager for a session."""
        return self._active_sandboxes.get(session_id)

    def get_relay(self, session_id: str) -> Optional[AcpPermissionRelay]:
        """Get the active permission relay for a session."""
        return self._active_relays.get(session_id)

    def execute_tool(
        self,
        session_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute a tool within the session sandbox after passing policy gate."""
        relay = self._active_relays.get(session_id)
        sandbox = self._active_sandboxes.get(session_id)
        spec = self._active_sessions.get(session_id)

        if not relay or not sandbox or not spec:
            return {"status": "error", "message": f"Session '{session_id}' not found or active."}

        # Check permission relay
        perm = relay.evaluate_tool_call(tool_name, arguments)
        if perm.decision == "deny":
            logger.warning(f"Session {session_id}: tool '{tool_name}' execution denied by policy.")
            return {"status": "denied", "reason": perm.reason}

        # Safe execution within sandbox boundaries
        try:
            result_data: Dict[str, Any] = {}
            if tool_name == "read_file":
                safe_path = sandbox.resolve_safe_path(arguments["path"])
                content = safe_path.read_text(encoding="utf-8")
                result_data = {"status": "success", "content": content}

            elif tool_name == "write_file":
                if spec.read_only:
                    return {"status": "denied", "reason": "Session is read-only"}
                safe_path = sandbox.resolve_safe_path(arguments["path"])
                safe_path.parent.mkdir(parents=True, exist_ok=True)
                safe_path.write_text(arguments["content"], encoding="utf-8")
                result_data = {"status": "success", "path": str(safe_path)}

            elif tool_name == "list_dir":
                target_dir = arguments.get("path", ".")
                safe_path = sandbox.resolve_safe_path(target_dir)
                entries = [p.name for p in safe_path.iterdir()]
                result_data = {"status": "success", "entries": sorted(entries)}

            elif tool_name in ("git_status", "git_diff"):
                changes = sandbox.inspect_git_changes()
                result_data = {"status": "success", "changed_files": changes}

            elif tool_name == "run_tests":
                passed, test_out = sandbox.run_sandboxed_tests(
                    test_command=arguments.get("command"),
                    timeout_seconds=float(arguments.get("timeout", 60.0)),
                )
                result_data = {"status": "success", "passed": passed, "output": test_out}

            else:
                return {
                    "status": "denied",
                    "reason": f"Tool '{tool_name}' execution handler not mapped",
                }

            # Log to event ledger
            event = AcpEvent(
                session_id=session_id,
                event_type="tool.executed",
                payload={"tool": tool_name, "arguments": arguments, "result": result_data},
            )
            self.db.save_acp_event(
                event_id=event.event_id,
                session_id=event.session_id,
                event_type=event.event_type,
                payload=event.payload,
            )
            return result_data

        except AcpSandboxError as err:
            logger.error(f"Sandbox violation in session {session_id}: {err}")
            return {"status": "denied", "reason": str(err)}
        except Exception as err:
            logger.error(f"Tool execution error in session {session_id}: {err}")
            return {"status": "error", "message": str(err)}

    async def execute_turn(
        self,
        session_id: str,
        instruction: str,
        candidate_output: Optional[str] = None,
        test_command: Optional[str] = None,
        verify_ground_truth: bool = True,
    ) -> AcpValidationResult:
        """Execute a cognitive coding turn, capturing and validating structured output."""
        spec = self._active_sessions.get(session_id)
        sandbox = self._active_sandboxes.get(session_id)

        if not spec or not sandbox:
            return AcpValidationResult(
                status="rejected",
                summary=f"Session '{session_id}' is not active.",
                error=f"Unknown session {session_id}",
            )

        # Mark session as running
        self.db.update_acp_session_state(session_id, AcpSessionState.RUNNING.value)
        start_event = AcpEvent(
            session_id=session_id,
            event_type="turn.started",
            payload={"instruction": instruction},
        )
        self.db.save_acp_event(
            event_id=start_event.event_id,
            session_id=start_event.session_id,
            event_type=start_event.event_type,
            payload=start_event.payload,
        )

        # Determine raw output: supplied candidate, or route to cognitive model
        raw_output = candidate_output
        if raw_output is None:
            # Route to model router with TaskClass.CODING
            prompt = (
                f"You are an external coding agent operating in a sandboxed repository: {spec.repo_path}.\n"
                f"Instruction: {instruction}\n\n"
                "CRITICAL: You must conclude your work with a structured YAML or JSON block matching:\n"
                "```yaml\n"
                "result:\n"
                "  status: completed\n"
                "  summary: <summary of actions>\n"
                "  changed_files: [<list of relative file paths>]\n"
                "  tests_run: [<list of test commands or suites>]\n"
                "  tests_passed: true\n"
                "  artifacts: []\n"
                "  remaining_risks: []\n"
                "```\n"
            )
            try:
                # Find matching ModelFamily enum
                target_family = next(
                    (m for m in ModelFamily if m.value == spec.model),
                    ModelFamily.GPT_OSS_120B,
                )
                req = ModelInvocationRequest(
                    model_family=target_family,
                    prompt=prompt,
                    task_id=spec.parent_task_id or f"task_{session_id}",
                    session_id=session_id,
                )
                response = await self.router.invoke(req)
                raw_output = response.text_content or ""
            except Exception as err:
                logger.error(f"ACP model invocation failed: {err}")
                raw_output = f"Model execution failed: {err}"

        # Validate structured result schema and ground truth
        validation = self.validator.validate(
            raw_output=raw_output,
            sandbox=sandbox,
            verify_ground_truth_tests=verify_ground_truth,
            test_command=test_command,
        )

        final_state = (
            AcpSessionState.COMPLETED.value
            if validation.status == "completed"
            else AcpSessionState.FAILED.value
        )
        self.db.update_acp_session_state(session_id, final_state)

        # Log completion event
        complete_event = AcpEvent(
            session_id=session_id,
            event_type="turn.completed",
            payload={
                "status": validation.status,
                "summary": validation.summary,
                "tests_passed": validation.tests_passed,
                "remaining_risks": validation.remaining_risks,
            },
        )
        self.db.save_acp_event(
            event_id=complete_event.event_id,
            session_id=complete_event.session_id,
            event_type=complete_event.event_type,
            payload=complete_event.payload,
        )

        return validation

    def cancel_session(self, session_id: str, reason: str = "User cancelled") -> None:
        """Cancel an active ACP session."""
        self.db.update_acp_session_state(session_id, AcpSessionState.CANCELLED.value)
        event = AcpEvent(
            session_id=session_id,
            event_type="session.cancelled",
            payload={"reason": reason},
        )
        self.db.save_acp_event(
            event_id=event.event_id,
            session_id=event.session_id,
            event_type=event.event_type,
            payload=event.payload,
        )
        self._active_sessions.pop(session_id, None)
        self._active_sandboxes.pop(session_id, None)
        self._active_relays.pop(session_id, None)
        logger.info(f"Cancelled ACP session {session_id}: {reason}")

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve persisted ACP session details."""
        return self.db.get_acp_session(session_id)

    def get_ledger(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieve full event ledger for an ACP session."""
        return self.db.get_acp_events(session_id)
