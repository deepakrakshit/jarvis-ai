"""Authoritative Control Plane & LangGraph Execution Loop for JARVIS.

Enforces Section 11 and Section 12 of ARCHITECTURE.md:
Drives tasks through the authoritative state machine:
CREATED -> CLASSIFIED -> PLANNED -> POLICY_CHECK -> AUTHORIZED -> RUNNING ->
OBSERVING -> VERIFYING -> COMPLETED (or NEEDS_APPROVAL / FAILED).

Core Invariant P5: Observe after acting.
Core Invariant: The model is NOT the trust boundary.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict, cast
from uuid import uuid4

from langgraph.graph import END, StateGraph

from jarvis.actions.broker import ActionBroker, action_broker
from jarvis.cognition.model_router import ModelRouter, TaskClass, model_router
from jarvis.config import settings
from jarvis.contracts.action import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    ExecutionTarget,
    RiskTier,
)
from jarvis.contracts.policy import PolicyVerdict
from jarvis.contracts.task import Task, TaskPriority, TaskState, TaskType
from jarvis.policy.engine import PolicyEngine, policy_engine
from jarvis.policy.firewall import (
    CAPABILITY_BROWSER_NAVIGATE,
    CAPABILITY_BROWSER_SNAPSHOT,
    CAPABILITY_COMPUTER_SCREENSHOT,
    CAPABILITY_FILESYSTEM_LIST,
    CAPABILITY_FILESYSTEM_READ,
    CAPABILITY_SHELL_EXECUTE,
    CAPABILITY_SYSTEM_INFO,
    CAPABILITY_SYSTEM_VOLUME,
    CAPABILITY_WHATSAPP_CALL,
    CAPABILITY_WHATSAPP_LOGIN,
    CAPABILITY_WHATSAPP_LOGOUT,
)
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger


class ControlPlaneState(TypedDict, total=False):
    """Execution state schema passed across LangGraph nodes."""

    task: Task
    plan: List[ActionRequest]
    current_action_idx: int
    last_action_result: Optional[ActionResult]
    observations: List[Dict[str, Any]]
    error: Optional[str]
    iterations: int
    max_iterations: int


class ControlPlane:
    """Canonical control plane orchestrating task execution via LangGraph."""

    def __init__(
        self,
        broker: Optional[ActionBroker] = None,
        policy: Optional[PolicyEngine] = None,
        database: Optional[DatabaseEngine] = None,
        router: Optional[ModelRouter] = None,
    ) -> None:
        self.db = database or db
        self.policy = policy or (PolicyEngine(database=self.db) if database else policy_engine)
        self.broker = broker or (
            ActionBroker(policy=self.policy, database=self.db) if database else action_broker
        )
        self.router = router or model_router
        self._ensure_nodes_registered()
        self._graph = self._build_graph()

    def _ensure_nodes_registered(self) -> None:
        """Register host, browser, and WhatsApp nodes with the capability registry."""
        try:
            from jarvis.execution.browser.host import browser_node
            from jarvis.execution.whatsapp import whatsapp_node
            from jarvis.execution.windows.host import windows_node

            windows_node.register_capabilities()
            browser_node.register_capabilities()
            whatsapp_node.register_capabilities()
        except Exception as reg_err:
            logger.debug(
                f"Notice: Node registration warning during ControlPlane initialization: {reg_err}"
            )

    def _build_graph(self) -> Any:
        """Construct the compiled LangGraph workflow."""
        builder = StateGraph(ControlPlaneState)

        builder.add_node("classify", self._node_classify)
        builder.add_node("plan", self._node_plan)
        builder.add_node("policy_gate", self._node_policy_gate)
        builder.add_node("execute", self._node_execute)
        builder.add_node("observe_verify", self._node_observe_verify)

        # Edges
        builder.set_entry_point("classify")
        builder.add_edge("classify", "plan")

        def check_plan_route(state: ControlPlaneState) -> str:
            task = state["task"]
            if task.state == TaskState.COMPLETED or not state.get("plan"):
                return "completed"
            return "policy_gate"

        builder.add_conditional_edges(
            "plan",
            check_plan_route,
            {
                "completed": END,
                "policy_gate": "policy_gate",
            },
        )

        def check_policy_route(state: ControlPlaneState) -> str:
            task = state["task"]
            if task.state == TaskState.NEEDS_APPROVAL:
                return "needs_approval"
            if task.state == TaskState.FAILED:
                return "failed"
            return "execute"

        builder.add_conditional_edges(
            "policy_gate",
            check_policy_route,
            {
                "needs_approval": END,
                "failed": END,
                "execute": "execute",
            },
        )

        builder.add_edge("execute", "observe_verify")

        def check_verification_route(state: ControlPlaneState) -> str:
            task = state["task"]
            if task.state == TaskState.COMPLETED or task.state == TaskState.FAILED:
                return "terminal"

            # Check if more planned actions remain
            idx = state.get("current_action_idx", 0)
            plan = state.get("plan", [])
            if idx < len(plan):
                return "policy_gate"
            return "terminal"

        builder.add_conditional_edges(
            "observe_verify",
            check_verification_route,
            {
                "policy_gate": "policy_gate",
                "terminal": END,
            },
        )

        return builder.compile()

    async def _node_classify(self, state: ControlPlaneState) -> Dict[str, Any]:
        """Classify user intent and determine task domain."""
        task = state["task"]
        intent = task.raw_intent.lower()

        logger.info(f"Classifying task [{task.task_id}]: {task.raw_intent[:60]}")

        # Intent classification heuristics
        if any(
            w in intent for w in ["browse", "http://", "https://", "website", "url", "web page"]
        ):
            task.task_type = TaskType.BROWSER_AUTOMATION
        elif any(
            w in intent
            for w in [
                "volume",
                "brightness",
                "screenshot",
                "screen",
                "process",
                "powershell",
                "dir",
                "file",
                "system",
                "hardware",
                "folder",
                "download",
                "downloads",
                "document",
                "documents",
                "desktop",
                "directory",
                "call",
                "dial",
                "phone",
                "whatsapp",
                "app",
                "launch",
                "open",
            ]
        ):
            task.task_type = TaskType.WINDOWS_CONTROL
        elif any(w in intent for w in ["code", "refactor", "bug", "python", "script", "function"]):
            task.task_type = TaskType.CODING
        elif any(w in intent for w in ["research", "summarize", "analyze", "explain"]):
            task.task_type = TaskType.RESEARCH
        else:
            task.task_type = TaskType.CONVERSATION

        task.transition_to(TaskState.CLASSIFIED, f"Domain classified as {task.task_type.value}")
        self.db.save_task(task)
        return {"task": task}

    async def _node_plan(self, state: ControlPlaneState) -> Dict[str, Any]:
        """Formulate execution plan or invoke cognitive reasoning."""
        task = state["task"]
        intent = task.raw_intent.strip()
        intent_lower = intent.lower()
        plan: List[ActionRequest] = []

        logger.info(f"Formulating plan for task [{task.task_id}] ({task.task_type.value})")

        # Windows control intent mapping
        if "screenshot" in intent_lower:
            plan.append(
                ActionRequest(
                    task_id=task.task_id,
                    session_id=task.session_id,
                    capability=CAPABILITY_COMPUTER_SCREENSHOT,
                    target=ExecutionTarget.WINDOWS_NODE,
                    risk_tier=RiskTier.READ_ONLY,
                )
            )
        elif "system info" in intent_lower or "system status" in intent_lower:
            plan.append(
                ActionRequest(
                    task_id=task.task_id,
                    session_id=task.session_id,
                    capability=CAPABILITY_SYSTEM_INFO,
                    target=ExecutionTarget.WINDOWS_NODE,
                    risk_tier=RiskTier.READ_ONLY,
                )
            )
        elif "volume" in intent_lower and any(
            w in intent_lower for w in ["get", "what", "check", "level"]
        ):
            plan.append(
                ActionRequest(
                    task_id=task.task_id,
                    session_id=task.session_id,
                    capability=CAPABILITY_SYSTEM_VOLUME,
                    target=ExecutionTarget.WINDOWS_NODE,
                    risk_tier=RiskTier.LOW,
                )
            )
        elif "read file" in intent_lower or "read " in intent_lower:
            path = intent.split("read ", 1)[-1].strip().strip('"').strip("'")
            plan.append(
                ActionRequest(
                    task_id=task.task_id,
                    session_id=task.session_id,
                    capability=CAPABILITY_FILESYSTEM_READ,
                    arguments={"path": path},
                    target=ExecutionTarget.WINDOWS_NODE,
                    risk_tier=RiskTier.READ_ONLY,
                )
            )
        elif any(
            w in intent_lower
            for w in [
                "download folder",
                "downloads folder",
                "downloads",
                "my downloads",
                "desktop folder",
                "my desktop",
                "documents folder",
                "my documents",
                "list dir",
                "list files",
                "show files",
                "check folder",
                "access folder",
                "folder contents",
                "in my downloads",
                "in downloads",
            ]
        ):
            target_path = Path.home() / "Downloads"
            if "desktop" in intent_lower:
                target_path = Path.home() / "Desktop"
            elif "document" in intent_lower:
                target_path = Path.home() / "Documents"
            elif "workspace" in intent_lower:
                target_path = settings.WORKSPACE_DIR

            custom_path_match = re.search(r"(?:in|of|path|at)\s+([A-Za-z]:[\\/][^\s]+)", intent)
            if custom_path_match:
                target_path = Path(custom_path_match.group(1))

            plan.append(
                ActionRequest(
                    task_id=task.task_id,
                    session_id=task.session_id,
                    capability=CAPABILITY_FILESYSTEM_LIST,
                    arguments={"path": str(target_path), "max_entries": 50},
                    target=ExecutionTarget.WINDOWS_NODE,
                    risk_tier=RiskTier.READ_ONLY,
                )
            )
        elif "browse" in intent_lower or "navigate to" in intent_lower:
            parts = intent.split()
            url = next(
                (
                    p
                    for p in parts
                    if p.startswith("http://") or p.startswith("https://") or p.startswith("data:")
                ),
                "https://google.com",
            )
            plan.append(
                ActionRequest(
                    task_id=task.task_id,
                    session_id=task.session_id,
                    capability=CAPABILITY_BROWSER_NAVIGATE,
                    arguments={"url": url},
                    target=ExecutionTarget.BROWSER_NODE,
                    risk_tier=RiskTier.LOW,
                )
            )
            plan.append(
                ActionRequest(
                    task_id=task.task_id,
                    session_id=task.session_id,
                    capability=CAPABILITY_BROWSER_SNAPSHOT,
                    arguments={},
                    target=ExecutionTarget.BROWSER_NODE,
                    risk_tier=RiskTier.READ_ONLY,
                )
            )
        elif "run shell" in intent_lower or "exec " in intent_lower:
            cmd = (
                intent.split("run shell", 1)[-1]
                if "run shell" in intent_lower
                else intent.split("exec", 1)[-1]
            )
            plan.append(
                ActionRequest(
                    task_id=task.task_id,
                    session_id=task.session_id,
                    capability=CAPABILITY_SHELL_EXECUTE,
                    arguments={"command": cmd.strip()},
                    target=ExecutionTarget.WINDOWS_NODE,
                    risk_tier=RiskTier.HIGH,
                )
            )
        elif re.search(
            r"\b(?:call|dial|phone)\s+([+0-9\-\(\)\s]{5,15}|[A-Za-z0-9_\-]+)",
            intent,
            re.I,
        ) or (
            "call" in intent_lower and any(w in intent_lower for w in ["whatsapp", "phone", "dial"])
        ):
            target = "contact"
            objective = intent
            call_match = re.search(
                r"\b(?:call|dial|phone)\s+([+0-9\-\(\)\s]{5,15}|[A-Za-z0-9_\-]+)(?:\s+(?:and|to|about)\s+(.*))?",
                intent,
                re.I,
            )
            if call_match:
                target = call_match.group(1).strip()
                if call_match.group(2):
                    objective = call_match.group(2).strip()
            elif "call " in intent_lower:
                after_call = intent.split("call ", 1)[-1]
                parts = after_call.split(" on whatsapp", 1)[0].split(" to ", 1)
                target = parts[0].strip()
                if " to " in after_call:
                    objective = after_call.split(" to ", 1)[-1].strip()
                elif " about " in after_call:
                    objective = after_call.split(" about ", 1)[-1].strip()

            plan.append(
                ActionRequest(
                    task_id=task.task_id,
                    session_id=task.session_id,
                    capability=CAPABILITY_WHATSAPP_CALL,
                    arguments={"target": target, "objective": objective},
                    target=ExecutionTarget.WHATSAPP_NODE,
                    risk_tier=RiskTier.MEDIUM,
                )
            )
        elif "whatsapp" in intent_lower and any(
            w in intent_lower for w in ["logout", "disconnect", "sign out", "unlink"]
        ):
            plan.append(
                ActionRequest(
                    task_id=task.task_id,
                    session_id=task.session_id,
                    capability=CAPABILITY_WHATSAPP_LOGOUT,
                    arguments={},
                    target=ExecutionTarget.WHATSAPP_NODE,
                    risk_tier=RiskTier.MEDIUM,
                )
            )
        elif "whatsapp" in intent_lower and any(
            w in intent_lower for w in ["login", "link", "qr", "authenticate", "connect"]
        ):
            plan.append(
                ActionRequest(
                    task_id=task.task_id,
                    session_id=task.session_id,
                    capability=CAPABILITY_WHATSAPP_LOGIN,
                    arguments={"force_refresh": "force" in intent_lower},
                    target=ExecutionTarget.WHATSAPP_NODE,
                    risk_tier=RiskTier.LOW,
                )
            )

        if plan:
            task.transition_to(TaskState.PLANNED, f"Formulated {len(plan)} structured actions")
            self.db.save_task(task)
            return {"task": task, "plan": plan, "current_action_idx": 0}

        # Pure conversational / reasoning request -> delegate to Model Router
        task.transition_to(TaskState.RUNNING, "Executing cognitive completion")
        task_class = (
            TaskClass.DEEP_REASONING
            if task.task_type == TaskType.RESEARCH
            else TaskClass.SIMPLE_TOOL
        )
        callsign = getattr(settings, "USER_CALLSIGN", "Sir")
        system_instruction = (
            f"You are JARVIS (version {settings.APP_VERSION}), the personal AI operating system running locally on {callsign}'s Windows PC. "
            "You have direct host access to Windows filesystem (Downloads, Desktop, Documents), native apps, process control, system volume, browser automation, screenshots, and autonomous WhatsApp VoIP calling. "
            f"Address the user as {callsign}. Respond directly, warmly, confidently, and naturally as JARVIS."
        )
        response = await self.router.complete(
            prompt=task.raw_intent,
            task_class=task_class,
            system_instruction=system_instruction,
        )

        if response.error:
            task.error_message = response.error
            task.transition_to(TaskState.FAILED, f"Model execution error: {response.error}")
        else:
            task.result_summary = response.text_content
            task.verification_passed = True
            task.transition_to(TaskState.COMPLETED, "Cognitive response generated successfully")

        self.db.save_task(task)
        return {"task": task, "plan": []}

    async def _node_policy_gate(self, state: ControlPlaneState) -> Dict[str, Any]:
        """Authoritative security checkpoint evaluating the current planned action."""
        task = state["task"]
        idx = state.get("current_action_idx", 0)
        plan = state.get("plan", [])

        if idx >= len(plan):
            task.transition_to(TaskState.COMPLETED, "All planned actions finished")
            self.db.save_task(task)
            return {"task": task}

        action_req = plan[idx]
        task.transition_to(TaskState.POLICY_CHECK, f"Evaluating policy for {action_req.capability}")

        decision = self.policy.evaluate(action_req)

        if decision.verdict == PolicyVerdict.DENY:
            task.error_message = f"Policy Denial: {decision.reason}"
            task.transition_to(TaskState.FAILED, decision.reason)
            self.db.save_task(task)
            return {"task": task, "error": decision.reason}

        if decision.verdict == PolicyVerdict.ASK:
            appr_id = decision.approval_request.approval_id if decision.approval_request else "N/A"
            task.transition_to(TaskState.NEEDS_APPROVAL, f"Approval required [{appr_id}]")
            self.db.save_task(task)
            return {"task": task, "error": f"Operator approval required: ticket {appr_id}"}

        task.transition_to(TaskState.AUTHORIZED, f"Policy ALLOW: {decision.reason}")
        task.transition_to(TaskState.RUNNING, f"Dispatching {action_req.capability}")
        self.db.save_task(task)
        return {"task": task}

    async def _node_execute(self, state: ControlPlaneState) -> Dict[str, Any]:
        """Dispatch action to the ActionBroker."""
        task = state["task"]
        idx = state.get("current_action_idx", 0)
        action_req = state["plan"][idx]

        logger.info(f"Executing action [{idx + 1}/{len(state['plan'])}]: {action_req.capability}")
        result = await self.broker.execute(action_req)

        task.transition_to(
            TaskState.OBSERVING,
            f"Action {action_req.action_id} completed with {result.status.value}",
        )
        self.db.save_task(task)
        return {"task": task, "last_action_result": result}

    async def _node_observe_verify(self, state: ControlPlaneState) -> Dict[str, Any]:
        """Observe results and verify post-conditions (Core Principle P5)."""
        task = state["task"]
        result = state.get("last_action_result")
        idx = state.get("current_action_idx", 0)
        observations = state.get("observations", [])

        task.transition_to(TaskState.VERIFYING, "Verifying execution observation")

        if not result or result.status != ActionStatus.SUCCEEDED:
            error_msg = result.error if result else "Execution returned no result"
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                logger.warning(
                    f"Action failed, retrying ({task.retry_count}/{task.max_retries}): {error_msg}"
                )
                task.transition_to(TaskState.RETRYING, f"Retry attempt {task.retry_count}")
                self.db.save_task(task)
                return {"task": task, "current_action_idx": idx}

            task.error_message = error_msg
            task.transition_to(TaskState.FAILED, f"Execution failed: {error_msg}")
            self.db.save_task(task)
            return {"task": task, "error": error_msg}

        observations.append(
            {
                "action_id": result.action_id,
                "capability": state["plan"][idx].capability,
                "output": result.output,
                "verified": result.verified,
            }
        )

        next_idx = idx + 1
        if next_idx >= len(state["plan"]):
            callsign = getattr(settings, "USER_CALLSIGN", "Sir")
            if len(observations) == 1:
                obs = observations[0]
                cap = obs.get("capability")
                out = obs.get("output")
                if cap == CAPABILITY_FILESYSTEM_LIST and isinstance(out, list):
                    path_str = state["plan"][idx].arguments.get("path", "the folder")
                    if out:
                        names = [
                            entry["name"]
                            for entry in out
                            if isinstance(entry, dict) and "name" in entry
                        ]
                        sample = ", ".join(names[:8])
                        more = f" and {len(out) - 8} more items" if len(out) > 8 else ""
                        task.result_summary = (
                            f"Yes {callsign}, I have full access to {path_str}. "
                            f"Found {len(out)} items: {sample}{more}."
                        )
                    else:
                        task.result_summary = (
                            f"Yes {callsign}, I accessed {path_str}. The folder is currently empty."
                        )
                elif cap == CAPABILITY_FILESYSTEM_READ:
                    path_str = state["plan"][idx].arguments.get("path", "the file")
                    snippet = str(out)[:1500] if out else "File is empty."
                    task.result_summary = f"Content of {path_str}:\n\n{snippet}"
                elif cap == CAPABILITY_WHATSAPP_CALL and isinstance(out, dict):
                    task.result_summary = (
                        out.get("outcome_message")
                        or out.get("summary")
                        or f"Call to {state['plan'][idx].arguments.get('target')} finished."
                    )
                elif cap == CAPABILITY_COMPUTER_SCREENSHOT and isinstance(out, dict):
                    path_str = out.get("artifact_path") or out.get("path")
                    task.result_summary = f"Screenshot captured successfully: {path_str}"
                elif cap == CAPABILITY_SYSTEM_INFO and isinstance(out, dict):
                    task.result_summary = (
                        f"Host Status: OS {out.get('os', 'Windows')}, "
                        f"CPU {out.get('cpu_percent', 'N/A')}%, "
                        f"Memory {out.get('memory_used_gb', 'N/A')}/{out.get('memory_total_gb', 'N/A')} GB"
                    )
                elif cap == CAPABILITY_SYSTEM_VOLUME and isinstance(out, dict):
                    task.result_summary = f"Master volume level is {out.get('volume', 'N/A')}%"
                else:
                    task.result_summary = f"Action {cap} completed successfully, {callsign}."
            else:
                task.result_summary = f"All {len(state['plan'])} actions succeeded, {callsign}."

            task.verification_passed = True
            task.transition_to(TaskState.COMPLETED, "Goal accomplished and verified")

        self.db.save_task(task)
        return {
            "task": task,
            "current_action_idx": next_idx,
            "observations": observations,
        }

    async def execute_task(self, task: Task) -> Task:
        """Run a task through the compiled LangGraph state machine."""
        self.db.save_task(task)
        initial_state: ControlPlaneState = {
            "task": task,
            "plan": [],
            "current_action_idx": 0,
            "observations": [],
            "iterations": 0,
            "max_iterations": 10,
        }

        final_state = await self._graph.ainvoke(initial_state)
        return cast(Task, final_state["task"])

    async def submit_intent(
        self,
        raw_intent: str,
        session_id: str,
        priority: TaskPriority = TaskPriority.NORMAL,
    ) -> Task:
        """Convenience entrypoint to create and execute a task from raw user intent."""
        task = Task(
            task_id=f"TASK-{uuid4().hex[:8].upper()}",
            session_id=session_id,
            raw_intent=raw_intent,
            priority=priority,
            state=TaskState.CREATED,
        )
        return await self.execute_task(task)


# Global singleton instance
control_plane = ControlPlane()
