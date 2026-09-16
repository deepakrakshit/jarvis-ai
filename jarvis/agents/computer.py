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
from jarvis.core.lifecycle.types import HealthProbeResult
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

COMPUTER_SYSTEM_PROMPT = """You are the JARVIS Computer Specialist.
Your domain covers system diagnostics, current clock time, date verification, OS environment status, CPU/RAM stats, and safety gates for system actions.

Available Tools:
- "native:clock:get_time": Retrieves point-in-time ISO timestamp, UTC time, local time, and day of week.
  Arguments: {}
- "native:system:get_stats": Retrieves host operating system, CPU usage %, memory usage, disk usage, and uptime.
  Arguments: {}

Analyze the user's message.
You MUST output ONLY a valid JSON object matching this schema:
{
  "action": "tool_call" | "clarify" | "direct_answer",
  "tool_id": "native:clock:get_time" | "native:system:get_stats" | null,
  "arguments": dict,
  "intent": string,
  "clarification_question": string | null,
  "direct_response": string | null,
  "target_resource": string | null
}

Rules:
1. If the user asks for the current time, date, timestamp, clock, or what day it is, set action='tool_call', tool_id='native:clock:get_time', arguments={}, target_resource='system_clock'.
2. If the user asks for system stats, performance, CPU, memory, RAM, disk, host hardware, or system health metrics, set action='tool_call', tool_id='native:system:get_stats', arguments={}, target_resource='host_system'.
3. If the user asks to reboot, shutdown, kill processes, or execute dangerous OS operations, set action='clarify' and ask for explicit user confirmation before proceeding.
4. If it is a query about system architecture or environment, set action='direct_answer' and explain the current status.
"""


class ComputerSpecialist(BaseSpecialist):
    """Specialist for OS control, system inspection, and runtime environment queries."""

    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
        super().__init__(
            manifest=SpecialistManifest(
                name="computer",
                role=SpecialistRole.COMPUTER,
                role_description="OS automation, clock queries, and system status observation.",
                allowed_tool_scopes=[
                    "native:clock:get_time",
                    "native:system:get_stats",
                    "native:shell:execute",
                    "os.window",
                ],
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
                    target = "system_clock" if "clock" in tool_id else "host_system"
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(data.get("intent", f"Execute {tool_id}")),
                        tool_id=tool_id,
                        arguments=args,
                        target_resource=target,
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

        # Check for system/OS performance stats
        if any(
            w in lower
            for w in (
                "system stats",
                "system status",
                "cpu",
                "memory",
                "ram",
                "disk",
                "hardware",
                "system metrics",
                "system info",
            )
        ):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Get system hardware and resource stats",
                tool_id="native:system:get_stats",
                arguments={},
                target_resource="host_system",
            )

        # Check for reboot / shutdown
        if any(w in lower for w in ("restart", "reboot", "shutdown", "power off")):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="System power state change",
                needs_clarification=True,
                clarification_question="Are you sure you want to reboot or shutdown? Please provide confirmation by replying with 'CONFIRM' to proceed.",
            )

        return SpecialistProposal(
            specialist_role=self.role,
            intent="General computer query",
            direct_response="I am the Computer Specialist. I can check current time, inspect CPU/RAM/disk metrics, and safely observe system status.",
        )

    async def synthesize(
        self,
        proposal: SpecialistProposal,
        tool_result: Any,
        user_message: str,
    ) -> str:
        """Synthesize clock or system observation into human-readable response."""
        if isinstance(tool_result, dict):
            if proposal.tool_id == "native:clock:get_time":
                self.scratchpad.add_note(f"Checked clock: {tool_result.get('local_iso')}")
            elif proposal.tool_id == "native:system:get_stats":
                self.scratchpad.add_note(
                    f"Host status: CPU {tool_result.get('cpu_percent')}% | RAM {tool_result.get('memory_percent')}%"
                )

        if self.gateway:
            try:
                prompt = (
                    f"User asked: '{user_message}'\n"
                    f"Tool result: {tool_result}\n\n"
                    "State the results clearly, naturally, and concisely."
                )
                req = GenerationRequest(
                    model_id="gemini-3.5-flash-lite",
                    system_instruction="You are the JARVIS Computer Specialist. Present system clock and hardware results crisply.",
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

        if proposal.tool_id == "native:system:get_stats":
            p = tool_result.get("platform", "Unknown")
            rel = tool_result.get("platform_release", "")
            cpu = tool_result.get("cpu_percent", 0.0)
            cpu_count = tool_result.get("cpu_count", 1)
            mem_pct = tool_result.get("memory_percent", 0.0)
            mem_used = tool_result.get("memory_used_gb", 0.0)
            mem_total = tool_result.get("memory_total_gb", 0.0)
            disk_pct = tool_result.get("disk_percent", 0.0)
            uptime = tool_result.get("uptime_seconds", 0)
            uptime_hrs = round(uptime / 3600.0, 1)

            return (
                f"**Host System Health Metrics**:\n\n"
                f"- **OS**: {p} {rel}\n"
                f"- **CPU**: {cpu}% ({cpu_count} logical cores)\n"
                f"- **Memory**: {mem_pct}% used ({mem_used} GB / {mem_total} GB)\n"
                f"- **Disk**: {disk_pct}% used\n"
                f"- **Uptime**: ~{uptime_hrs} hours ({uptime}s)"
            )

        return str(tool_result)

    async def health_probe(self) -> HealthProbeResult:
        """Run diagnostic health check on computer specialist capabilities."""
        return HealthProbeResult(
            component_id="specialist:computer",
            healthy=True,
            details={"capabilities": ["native:clock:get_time", "native:system:get_stats"]},
        )
