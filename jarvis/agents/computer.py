"""JARVIS Computer Specialist.

Handles system monitoring, operating system state, clock verification,
and environment management with LLM intelligence (ARCHITECTURE.md Layer 8 & 10).
"""

from typing import Any

from jarvis.agents.base import (
    BaseSpecialist,
    SpecialistManifest,
    SpecialistProposal,
    SpecialistRole,
    parse_llm_json,
)
from jarvis.core.gateway.interfaces import ChatMessage, GenerationRequest
from jarvis.core.gateway.router import ModelGateway
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

COMPUTER_SYSTEM_PROMPT = """You are the JARVIS Computer Specialist.
Your domain covers system diagnostics, current clock time, date verification, OS environment status, and safety gates for system actions.

Available Tools:
- "native:clock:get_time": Retrieves point-in-time ISO timestamp, UTC time, local time, and day of week.
  Arguments: {}

Analyze the user's message.
You MUST output ONLY a valid JSON object matching this schema:
{
  "action": "tool_call" | "clarify" | "direct_answer",
  "tool_id": "native:clock:get_time" | null,
  "arguments": dict,
  "intent": string,
  "clarification_question": string | null,
  "direct_response": string | null,
  "target_resource": string | null
}

Rules:
1. If the user asks for the current time, date, timestamp, clock, or what day it is, set action='tool_call', tool_id='native:clock:get_time', arguments={}, target_resource='system_clock'.
2. If the user asks to reboot, shutdown, kill processes, or execute dangerous OS operations, set action='clarify' and ask for explicit user confirmation before proceeding.
3. If it is a query about system status, architecture, or environment, set action='direct_answer' and explain the current status.
"""


class ComputerSpecialist(BaseSpecialist):
    """Specialist for OS control, system inspection, and runtime environment queries."""

    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
        super().__init__(
            manifest=SpecialistManifest(
                name="computer",
                role=SpecialistRole.COMPUTER,
                role_description="OS automation, clock queries, and system status observation.",
                allowed_tool_scopes=["native:clock:get_time", "native:shell:execute", "os.window"],
                memory_mode="PER_SPECIALIST",
            ),
            model_gateway=model_gateway,
        )

    async def propose(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> SpecialistProposal:
        """Evaluate computer/system request with LLM intelligence or fallback."""
        if self.gateway:
            try:
                history_str = ""
                if context and "history" in context and context["history"]:
                    recent = context["history"][-3:]
                    lines = []
                    for turn in recent:
                        lines.append(f"User: {turn.get('user_message')}")
                        lines.append(f"JARVIS: {str(turn.get('assistant_response', ''))[:250]}")
                    history_str = "Recent Conversation History:\n" + "\n".join(lines) + "\n\n"

                intent_str = (
                    f"Classified Intent: {context.get('intent')}\n"
                    if context and context.get("intent")
                    else ""
                )
                full_content = f"{history_str}{intent_str}Current User Request: {user_message}"

                messages = [ChatMessage(role="user", content=full_content)]
                req = GenerationRequest(
                    model_id="gemini-3.5-flash-lite",
                    system_instruction=COMPUTER_SYSTEM_PROMPT,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=500,
                )
                resp = await self.gateway.generate(req)
                data = parse_llm_json(resp.content)
                action = data.get("action", "direct_answer")

                if action == "clarify":
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(data.get("intent", "Clarification needed")),
                        needs_clarification=True,
                        clarification_question=data.get("clarification_question")
                        or "Please provide confirmation before proceeding with this system action.",
                    )

                if action == "tool_call" and data.get("tool_id"):
                    tool_id = str(data["tool_id"])
                    args = data.get("arguments") or {}
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(data.get("intent", "Get system clock time")),
                        tool_id=tool_id,
                        arguments=args,
                        target_resource="system_clock",
                    )

                if action == "direct_answer" and data.get("direct_response"):
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(data.get("intent", "System environment query")),
                        direct_response=str(data["direct_response"]),
                    )

            except Exception as exc:
                logger.warning("computer_specialist_llm_fallback", error=str(exc))

        return self._heuristic_propose(user_message)

    def _heuristic_propose(self, user_message: str) -> SpecialistProposal:
        """Rule-based fallback for offline test suites and network disconnection."""
        msg = user_message.strip()
        lower = msg.lower()

        # Check for time or date queries
        if any(
            w in lower for w in ("time", "date", "clock", "what day", "current time", "timestamp")
        ):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Get system clock time",
                tool_id="native:clock:get_time",
                arguments={},
                target_resource="system_clock",
            )

        # Check for reboot / shutdown
        if any(w in lower for w in ("restart", "reboot", "shutdown", "power off")):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="System power state change",
                needs_clarification=True,
                clarification_question="Are you sure you want to reboot or shutdown? Please provide confirmation by replying with 'CONFIRM' to proceed.",
            )

        # Check for system/OS commands
        if any(w in lower for w in ("system status", "os version", "environment", "system info")):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="System environment query",
                direct_response="JARVIS v1.0.0 is operating on Windows zero-trust host. Subsystems active: LangGraph state machine, IFC label lattice, Centralized Policy Engine, Action Broker, and LocalProcessSandbox.",
            )

        return SpecialistProposal(
            specialist_role=self.role,
            intent="General computer query",
            direct_response="I am the Computer Specialist. I can check current time, inspect operating environment status, and safely manage system processes.",
        )

    async def synthesize(
        self,
        proposal: SpecialistProposal,
        tool_result: Any,
        user_message: str,
    ) -> str:
        """Synthesize clock observation into human-readable response."""
        if self.gateway:
            try:
                prompt = (
                    f"User asked: '{user_message}'\n"
                    f"Clock result: {tool_result}\n\n"
                    "State the current time, day of the week, and date clearly and naturally."
                )
                req = GenerationRequest(
                    model_id="gemini-3.5-flash-lite",
                    system_instruction="You are the JARVIS Computer Specialist. Present system clock and time results crisply.",
                    messages=[ChatMessage(role="user", content=prompt)],
                    temperature=0.1,
                    max_tokens=1000,
                )
                res = await self.gateway.generate(req)
                if res.content.strip():
                    return res.content.strip()
            except Exception as exc:
                logger.warning("computer_specialist_synth_fallback", error=str(exc))

        if proposal.tool_id == "native:clock:get_time":
            local = tool_result.get("local_iso", "")
            utc = tool_result.get("utc_iso", "")
            day = tool_result.get("day_of_week", "")
            return f"Current Time: **{local}** ({day}) [UTC: `{utc}`]"

        return str(tool_result)
