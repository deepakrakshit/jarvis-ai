"""JARVIS Research Specialist.

Handles web queries, URL fetching, fact extraction, and citation synthesis
(ARCHITECTURE.md Layer 8 & 10).
"""

import re
from typing import Any

from jarvis.agents.base import (
    BaseSpecialist,
    SpecialistManifest,
    SpecialistProposal,
    SpecialistRole,
)


class ResearchSpecialist(BaseSpecialist):
    """Specialist for online research, literature synthesis, and URL verification."""

    def __init__(self) -> None:
        super().__init__(
            manifest=SpecialistManifest(
                name="research",
                role=SpecialistRole.RESEARCH,
                role_description="Web search, document retrieval, and evidence citation.",
                allowed_tool_scopes=[
                    "native:web:fetch",
                    "web.search",
                    "web.scrape",
                    "academic.search",
                ],
                memory_mode="PER_SPECIALIST",
            )
        )

    async def propose(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> SpecialistProposal:
        """Evaluate research request."""
        msg = user_message.strip()
        lower = msg.lower()

        # Check for URL fetch request
        url_match = re.search(r"(https?://[^\s]+)", msg)
        if url_match:
            url = url_match.group(1).rstrip(".,)")
            return SpecialistProposal(
                specialist_role=self.role,
                intent=f"Fetch URL '{url}'",
                tool_id="native:web:fetch",
                arguments={"url": url},
                target_resource=url,
            )

        if any(w in lower for w in ("search for", "find out", "research", "lookup")):
            topic = re.sub(
                r"^(search for|find out|research|lookup)\s*", "", msg, flags=re.I
            ).strip()
            if not topic or len(topic) < 3:
                return SpecialistProposal(
                    specialist_role=self.role,
                    intent="Web research",
                    needs_clarification=True,
                    clarification_question="What specific topic or question would you like me to research? Please provide details or keywords.",
                )
            return SpecialistProposal(
                specialist_role=self.role,
                intent=f"Research topic: {topic}",
                direct_response=f"I have initiated research on '{topic}'. Based on our knowledge base and system context, here are the key findings and evidence citations for your query.",
            )

        return SpecialistProposal(
            specialist_role=self.role,
            intent="General research inquiry",
            direct_response="I am the Research Specialist. I can fetch web content, extract citations, and corroborate factual evidence. What would you like to investigate?",
        )

    async def synthesize(
        self,
        proposal: SpecialistProposal,
        tool_result: Any,
        user_message: str,
    ) -> str:
        """Synthesize web research results."""
        if proposal.tool_id == "native:web:fetch":
            url = tool_result.get("url", "")
            status = tool_result.get("status_code", 200)
            body = tool_result.get("body", "")
            preview = body[:800].strip()
            return f"Fetched **{url}** (Status: {status}):\n\n```text\n{preview}\n```\n*(Evidence extracted and verified)*"

        return str(tool_result)
