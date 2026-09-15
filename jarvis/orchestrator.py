"""JARVIS Master Runtime Orchestrator.

Integrates the 5 Capability Specialists, Capability Firewall, Centralized Policy Engine,
Action Broker, Lifecycle Manager, and Session Logging into a unified execution loop
(ARCHITECTURE.md Layer 5, 8, 12, 13, 14).
"""

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from jarvis.agents.base import SpecialistRole
from jarvis.agents.router import RoutingCategory, SpecialistRouter
from jarvis.core.broker.broker import ActionBroker
from jarvis.core.capabilities.builtin import BUILTIN_CAPABILITIES, register_builtin_capabilities
from jarvis.core.capabilities.firewall import CapabilityFirewall
from jarvis.core.capabilities.manifest import RiskClass
from jarvis.core.capabilities.registry import CapabilityRegistry
from jarvis.core.gateway.router import ModelGateway
from jarvis.core.lifecycle.manager import LifecycleManager
from jarvis.core.lifecycle.types import ComponentType
from jarvis.core.logging import get_logger
from jarvis.core.policy.decision import AutonomyLevel, EffectAuthorization, PolicyDecisionType
from jarvis.core.policy.engine import PolicyEngine
from jarvis.core.session.models import ConversationTurn, SystemLogEntry, ToolExecutionRecord
from jarvis.core.session.session_manager import SessionManager
from jarvis.tools.native import dispatch_native_tool

logger = get_logger(__name__)


class JarvisOrchestrator:
    """The central personal AI operating system orchestrator."""

    def __init__(
        self,
        workspace_root: Path | str | None = None,
        autonomy_level: AutonomyLevel = AutonomyLevel.AUTO_BOUNDED_MUTATION,
        session_manager: SessionManager | None = None,
        sessions_path: Path | str | None = None,
        conversations_path: Path | str | None = None,
        model_gateway: ModelGateway | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.autonomy_level = autonomy_level

        # Subsystems
        self.gateway = model_gateway or ModelGateway()
        self.lifecycle = LifecycleManager()
        self.registry = CapabilityRegistry()
        register_builtin_capabilities(self.registry)

        self.firewall = CapabilityFirewall()
        self.policy_engine = PolicyEngine(
            workspace_root=self.workspace_root,
            default_autonomy=self.autonomy_level,
        )
        self.broker = ActionBroker()
        self.router = SpecialistRouter(model_gateway=self.gateway)
        self.session_manager = session_manager or SessionManager(
            sessions_path=sessions_path,
            conversations_path=conversations_path,
        )

        # Register components in Lifecycle Manager
        for manifest in BUILTIN_CAPABILITIES:
            rec = self.lifecycle.register_component(manifest.capability_id, ComponentType.TOOL)
            self.lifecycle.validate_component(rec.component_id)
            self.lifecycle.enable_component(rec.component_id)

    async def interact(
        self,
        session_id: str,
        user_message: str,
        on_progress: Any = None,
    ) -> str:
        """Execute a full conversational or task turn with complete logging."""
        system_logs: list[SystemLogEntry] = []
        tool_executions: list[ToolExecutionRecord] = []
        turn_id = uuid4()
        task_id = uuid4()

        def log_event(
            comp: str, msg: str, details: dict[str, Any] | None = None, level: str = "INFO"
        ) -> None:
            entry = SystemLogEntry(
                component=comp,
                message=msg,
                details=details or {},
                level=level,
            )
            system_logs.append(entry)
            if on_progress:
                on_progress(comp, msg)

        log_event("gateway", f"Received user prompt: '{user_message}'")

        # 0. Retrieve conversation history for context
        history_records: list[dict[str, Any]] = []
        conv_record = self.session_manager.get_conversation(session_id)
        if conv_record:
            for past_turn in conv_record.turns:
                history_records.append(
                    {
                        "user_message": past_turn.user_message,
                        "assistant_response": past_turn.assistant_response,
                    }
                )

        # 1. Router: Intelligent Layer 07 Classification (gemini-3.1-flash-lite / fallback)
        decision = await self.router.route_intent(user_message, history=history_records)
        log_event(
            "router",
            f"Classified intent: '{decision.intent}' (Category: {decision.category.value})",
            details={
                "category": decision.category.value,
                "specialist": decision.specialist_role.value if decision.specialist_role else None,
                "reasoning": decision.reasoning,
            },
        )

        # 2. Handle Conversational Turn (greetings, identity, capabilities, chitchat)
        if decision.category == RoutingCategory.CONVERSATIONAL:
            assistant_response = (
                decision.direct_response
                or "Hello! I am JARVIS, your Personal AI Operating System. How can I assist you today?"
            )
            log_event(
                "jarvis", f"Delivered conversational response: '{assistant_response[:60]}...'"
            )

            turn = ConversationTurn(
                turn_id=turn_id,
                user_message=user_message,
                assistant_response=assistant_response,
                specialist="jarvis",
                intent=decision.intent,
                needs_clarification=False,
                clarification_question=None,
                tool_executions=[],
                system_logs=system_logs,
            )
            self.session_manager.add_turn(session_id, turn)
            return assistant_response

        # 3. Specialist Route: Dispatch to the selected specialist
        role = decision.specialist_role or SpecialistRole.CODING
        specialist = self.router.get_specialist(role)
        log_event("specialist", f"Dispatched task to specialist: {specialist.role.value}")

        # 4. Specialist Proposal (LLM-driven)
        proposal = await specialist.propose(
            user_message,
            context={"history": history_records, "workspace_root": str(self.workspace_root)},
        )
        log_event(
            "specialist",
            f"Specialist '{specialist.role.value}' formulated proposal: {proposal.intent}",
            details={
                "needs_clarification": proposal.needs_clarification,
                "tool_id": proposal.tool_id,
            },
        )

        # 5. Check Clarification Need (JARVIS proactively asking questions)
        if proposal.needs_clarification and proposal.clarification_question:
            assistant_response = proposal.clarification_question
            log_event("specialist", f"Prompted user for clarification: {assistant_response}")

            turn = ConversationTurn(
                turn_id=turn_id,
                user_message=user_message,
                assistant_response=assistant_response,
                specialist=specialist.role.value,
                intent=proposal.intent,
                needs_clarification=True,
                clarification_question=proposal.clarification_question,
                tool_executions=[],
                system_logs=system_logs,
            )
            self.session_manager.add_turn(session_id, turn)
            return assistant_response

        # 6. Check Direct Response (No tool execution needed)
        if not proposal.tool_id and proposal.direct_response:
            assistant_response = proposal.direct_response
            log_event("specialist", "Provided direct response without tool execution")

            turn = ConversationTurn(
                turn_id=turn_id,
                user_message=user_message,
                assistant_response=assistant_response,
                specialist=specialist.role.value,
                intent=proposal.intent,
                needs_clarification=False,
                tool_executions=[],
                system_logs=system_logs,
            )
            self.session_manager.add_turn(session_id, turn)
            return assistant_response

        if not proposal.tool_id:
            assistant_response = (
                "I analyzed your request, but no actionable execution path was identified."
            )
            turn = ConversationTurn(
                turn_id=turn_id,
                user_message=user_message,
                assistant_response=assistant_response,
                specialist=specialist.role.value,
                intent=proposal.intent,
                tool_executions=[],
                system_logs=system_logs,
            )
            self.session_manager.add_turn(session_id, turn)
            return assistant_response

        # 7. Capability Verification & Least-Privilege Projection
        manifest = self.registry.get(proposal.tool_id)
        if not manifest:
            assistant_response = f"Capability '{proposal.tool_id}' is not registered in the system."
            log_event("registry", assistant_response, level="ERROR")
            turn = ConversationTurn(
                turn_id=turn_id,
                user_message=user_message,
                assistant_response=assistant_response,
                specialist=specialist.role.value,
                intent=proposal.intent,
                tool_executions=[],
                system_logs=system_logs,
            )
            self.session_manager.add_turn(session_id, turn)
            return assistant_response

        # 8. Centralized Policy Evaluation
        decision_policy = self.policy_engine.evaluate_invocation(
            task_id=task_id,
            manifest=manifest,
            arguments=proposal.arguments,
            autonomy_level=self.autonomy_level,
            target_resource=proposal.target_resource,
            agent_id=specialist.role.value,
            user_id="jarvis_user",
        )
        log_event(
            "policy_engine",
            f"Policy Decision: {decision_policy.decision.value} (Risk: {decision_policy.risk_score:.2f}) - {decision_policy.reason}",
            details={
                "decision": decision_policy.decision.value,
                "risk_score": decision_policy.risk_score,
            },
        )

        if decision_policy.decision == PolicyDecisionType.DENY:
            assistant_response = f"Security Policy Blocked Action: {decision_policy.reason}"
            self.lifecycle.record_invocation_failure(manifest.capability_id, decision_policy.reason)
            turn = ConversationTurn(
                turn_id=turn_id,
                user_message=user_message,
                assistant_response=assistant_response,
                specialist=specialist.role.value,
                intent=proposal.intent,
                tool_executions=[],
                system_logs=system_logs,
            )
            self.session_manager.add_turn(session_id, turn)
            return assistant_response

        # 9. Action Broker Execution Fabric
        start_time = time.perf_counter()
        authorization: EffectAuthorization | None = None

        if manifest.risk_class != RiskClass.READ_ONLY or manifest.approval_requirement:
            args_hash = ActionBroker.compute_canonical_hash(proposal.arguments)
            authorization = EffectAuthorization(
                task_id=task_id,
                user_id="jarvis_user",
                session_id=uuid4(),
                agent_id=specialist.role.value,
                tool_id=manifest.capability_id,
                canonical_arguments_hash=args_hash,
                target_resource=proposal.target_resource or "default_target",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                nonce=uuid4().hex,
            )
            log_event(
                "action_broker",
                f"Issued commit-time authorization token for '{manifest.capability_id}'",
            )

        try:
            cap_id = manifest.capability_id

            async def tool_runner(args: dict[str, Any]) -> Any:
                return await dispatch_native_tool(cap_id, args)

            tool_output = await self.broker.execute_action(
                task_id=task_id,
                tool_id=cap_id,
                arguments=proposal.arguments,
                executor_fn=tool_runner,
                manifest=manifest,
                authorization=authorization,
                target_resource=proposal.target_resource,
            )
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            self.lifecycle.record_invocation_success(manifest.capability_id)
            log_event(
                "action_broker",
                f"Action '{manifest.capability_id}' verified and completed in {duration_ms:.1f}ms",
            )

            tool_executions.append(
                ToolExecutionRecord(
                    tool_id=manifest.capability_id,
                    arguments=proposal.arguments,
                    policy_decision=decision_policy.decision.value,
                    risk_score=decision_policy.risk_score,
                    result=tool_output,
                    verified=True,
                    duration_ms=duration_ms,
                )
            )

            # 10. Synthesize Result (LLM-driven)
            assistant_response = await specialist.synthesize(proposal, tool_output, user_message)
            log_event("specialist", "Synthesized final observation response")

        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            self.lifecycle.record_invocation_failure(manifest.capability_id, str(exc))
            log_event(
                "action_broker",
                f"Execution error on '{manifest.capability_id}': {exc}",
                level="ERROR",
            )
            assistant_response = f"Action execution failed: {exc}"

            tool_executions.append(
                ToolExecutionRecord(
                    tool_id=manifest.capability_id,
                    arguments=proposal.arguments,
                    policy_decision=decision_policy.decision.value,
                    risk_score=decision_policy.risk_score,
                    result={"error": str(exc)},
                    verified=False,
                    duration_ms=duration_ms,
                )
            )

        # 11. Save Complete Turn with System Logs
        turn = ConversationTurn(
            turn_id=turn_id,
            user_message=user_message,
            assistant_response=assistant_response,
            specialist=specialist.role.value,
            intent=proposal.intent,
            needs_clarification=False,
            tool_executions=tool_executions,
            system_logs=system_logs,
        )
        self.session_manager.add_turn(session_id, turn)
        return assistant_response
