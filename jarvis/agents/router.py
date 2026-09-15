"""JARVIS Capability Specialist Router.

Directs incoming user intents to the optimal capability specialist
(ARCHITECTURE.md Layer 7 & Layer 8).
"""

from typing import Any

from jarvis.agents.analysis import AnalysisSpecialist
from jarvis.agents.base import BaseSpecialist, SpecialistRole
from jarvis.agents.coding import CodingSpecialist
from jarvis.agents.computer import ComputerSpecialist
from jarvis.agents.personal import PersonalSpecialist
from jarvis.agents.research import ResearchSpecialist
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class SpecialistRouter:
    """Routes user requests to one of the 5 canonical capability specialists."""

    def __init__(self) -> None:
        self.specialists: dict[SpecialistRole, BaseSpecialist] = {
            SpecialistRole.CODING: CodingSpecialist(),
            SpecialistRole.RESEARCH: ResearchSpecialist(),
            SpecialistRole.COMPUTER: ComputerSpecialist(),
            SpecialistRole.PERSONAL: PersonalSpecialist(),
            SpecialistRole.ANALYSIS: AnalysisSpecialist(),
        }

    def route(self, user_message: str, context: dict[str, Any] | None = None) -> BaseSpecialist:
        """Analyze user message and return the most suitable specialist."""
        msg = user_message.lower().strip()

        # 1. Math / Calculation / Analytics -> Analysis
        if any(
            w in msg
            for w in ("calculate", "compute", "evaluate", "how much is", "math", "sqrt(", "pi")
        ):
            logger.info("router_selected_specialist", role=SpecialistRole.ANALYSIS.value)
            return self.specialists[SpecialistRole.ANALYSIS]

        # 2. Time / Clock / OS status -> Computer
        if any(
            w in msg
            for w in (
                "what time",
                "current time",
                "clock",
                "what date",
                "today's date",
                "timestamp",
                "system status",
                "os version",
            )
        ):
            logger.info("router_selected_specialist", role=SpecialistRole.COMPUTER.value)
            return self.specialists[SpecialistRole.COMPUTER]

        # 3. Personal / Notes / Reminders -> Personal
        if any(
            w in msg
            for w in (
                "remind me",
                "take a note",
                "remember that",
                "save note",
                "my notes",
                "my reminders",
            )
        ):
            logger.info("router_selected_specialist", role=SpecialistRole.PERSONAL.value)
            return self.specialists[SpecialistRole.PERSONAL]

        # 4. Web Fetch / Search / Research -> Research
        if any(
            w in msg
            for w in (
                "http://",
                "https://",
                "search for",
                "find out",
                "research",
                "browse",
                "lookup",
            )
        ):
            logger.info("router_selected_specialist", role=SpecialistRole.RESEARCH.value)
            return self.specialists[SpecialistRole.RESEARCH]

        # 5. Files / Code / Terminal / Git -> Coding
        if any(
            w in msg
            for w in (
                "file",
                "read",
                "write",
                "code",
                "directory",
                "dir",
                "list",
                "cat",
                "script",
                "test",
                "python",
                "bug",
            )
        ):
            logger.info("router_selected_specialist", role=SpecialistRole.CODING.value)
            return self.specialists[SpecialistRole.CODING]

        # Default fallback: General intelligence / coding
        logger.info("router_fallback_specialist", role=SpecialistRole.CODING.value)
        return self.specialists[SpecialistRole.CODING]

    def get_specialist(self, role: SpecialistRole) -> BaseSpecialist:
        """Get specialist by its enum role."""
        return self.specialists[role]
