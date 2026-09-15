"""JARVIS Computer Specialist.

Handles system monitoring, operating system state, clock verification,
and environment management (ARCHITECTURE.md Layer 8 & 10).
"""

from typing import Any

from jarvis.agents.base import (
    BaseSpecialist,
    SpecialistManifest,
    SpecialistProposal,
    SpecialistRole,
)


class ComputerSpecialist(BaseSpecialist):
    """Specialist for OS control, system inspection, and runtime environment queries."""

    def __init__(self) -> None:
        super().__init__(
            manifest=SpecialistManifest(
                name="computer",
                role=SpecialistRole.COMPUTER,
                role_description="OS automation, clock queries, and system status observation.",
                allowed_tool_scopes=["native:clock:get_time", "native:shell:execute", "os.window"],
                memory_mode="PER_SPECIALIST",
            )
        )

    async def propose(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> SpecialistProposal:
        """Evaluate computer/system request."""
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

        # Check for system/OS commands
        if any(w in lower for w in ("system status", "os version", "environment", "system info")):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="System environment query",
                direct_response="JARVIS v1.0.0 is operating on Windows zero-trust host. Subsystems active: LangGraph state machine, IFC label lattice, Centralized Policy Engine, Action Broker, and LocalProcessSandbox.",
            )

        if any(w in lower for w in ("restart", "reboot", "shutdown", "power off")):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Critical power action",
                needs_clarification=True,
                clarification_question="Are you sure you want to trigger a system shutdown/reboot? This requires explicit high-level confirmation.",
            )

        return SpecialistProposal(
            specialist_role=self.role,
            intent="General computer query",
            direct_response="I am the Computer Specialist. I manage operating system automation, timestamps, and process state. How can I help?",
        )

    async def synthesize(
        self,
        proposal: SpecialistProposal,
        tool_result: Any,
        user_message: str,
    ) -> str:
        """Synthesize system/clock observation."""
        if proposal.tool_id == "native:clock:get_time":
            utc = tool_result.get("utc_iso", "")
            local = tool_result.get("local_iso", "")
            dow = tool_result.get("day_of_week", "")
            return f"Current Time: **{local}** ({dow}) [UTC: `{utc}`]"

        return str(tool_result)
