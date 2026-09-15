"""JARVIS Research Specialist.

Handles web queries, URL fetching, fact extraction, and citation synthesis
with LLM intelligence (ARCHITECTURE.md Layer 8 & 10).
"""

import re
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

RESEARCH_SYSTEM_PROMPT = """You are the JARVIS Research Specialist.
Your domain covers online research, fetching documentation from URLs, and synthesizing evidence with citations.

Available Tools:
- "native:web:fetch": Fetches the text/HTML content of a specified web URL.
  Arguments: {"url": string}

Analyze the user's message.
You MUST output ONLY a valid JSON object matching this schema:
{
  "action": "tool_call" | "clarify" | "direct_answer",
  "tool_id": "native:web:fetch" | null,
  "arguments": dict,
  "intent": string,
  "clarification_question": string | null,
  "direct_response": string | null,
  "target_resource": string | null
}

Rules:
1. If the user provides a specific URL to fetch, inspect, browse, or read (e.g. "fetch https://example.com/docs"), set action='tool_call', tool_id='native:web:fetch', arguments={"url": "<url>"}, target_resource="<url>".
2. If the user asks to search or research something with an empty or vague query (e.g. "search for", "look up"), set action='clarify' and ask what topic or URL they would like researched.
3. If it is a conceptual question that can be answered directly using knowledge, set action='direct_answer'.
"""


class ResearchSpecialist(BaseSpecialist):
    """Specialist for online research, literature synthesis, and URL verification."""

    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
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
            ),
            model_gateway=model_gateway,
        )

    async def propose(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> SpecialistProposal:
        """Evaluate research request with LLM intelligence or fallback."""
        if self.gateway:
            try:
                messages = [ChatMessage(role="user", content=user_message)]
                req = GenerationRequest(
                    model_id="gemini-3.5-flash-lite",
                    system_instruction=RESEARCH_SYSTEM_PROMPT,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=250,
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
                        or "What specific topic or web URL would you like me to research?",
                    )

                if action == "tool_call" and data.get("tool_id"):
                    tool_id = str(data["tool_id"])
                    args = data.get("arguments") or {}
                    url = args.get("url", "")
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(data.get("intent", f"Fetch URL '{url}'")),
                        tool_id=tool_id,
                        arguments=args,
                        target_resource=url,
                    )

                if action == "direct_answer" and data.get("direct_response"):
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(data.get("intent", "Direct research response")),
                        direct_response=str(data["direct_response"]),
                    )

            except Exception as exc:
                logger.warning("research_specialist_llm_fallback", error=str(exc))

        return self._heuristic_propose(user_message)

    def _heuristic_propose(self, user_message: str) -> SpecialistProposal:
        """Rule-based fallback for offline test suites and network disconnection."""
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
            if not topic:
                return SpecialistProposal(
                    specialist_role=self.role,
                    intent="Search query clarification",
                    needs_clarification=True,
                    clarification_question="What specific topic would you like me to research?",
                )
            return SpecialistProposal(
                specialist_role=self.role,
                intent=f"Research topic '{topic}'",
                direct_response=f"I have initialized research on '{topic}'. Provide a specific documentation URL or query for deeper extraction.",
            )

        return SpecialistProposal(
            specialist_role=self.role,
            intent="General research query",
            direct_response="I am the Research Specialist. I can fetch web documentation, browse URLs, and verify factual references.",
        )

    async def synthesize(
        self,
        proposal: SpecialistProposal,
        tool_result: Any,
        user_message: str,
    ) -> str:
        """Synthesize web research results into human-readable response."""
        if self.gateway:
            try:
                prompt = (
                    f"User asked: '{user_message}'\n"
                    f"Web fetch result from '{proposal.arguments.get('url')}':\n"
                    f"{tool_result}\n\n"
                    "Synthesize an insightful, concise, and structured summary with key facts and citations."
                )
                req = GenerationRequest(
                    model_id="gemini-3.5-flash-lite",
                    system_instruction="You are the JARVIS Research Specialist. Provide structured, accurate, well-cited summaries.",
                    messages=[ChatMessage(role="user", content=prompt)],
                    temperature=0.2,
                    max_tokens=500,
                )
                res = await self.gateway.generate(req)
                if res.content.strip():
                    return res.content.strip()
            except Exception as exc:
                logger.warning("research_specialist_synth_fallback", error=str(exc))

        if proposal.tool_id == "native:web:fetch":
            url = tool_result.get("url", proposal.arguments.get("url", ""))
            content = tool_result.get("content", "")
            bytes_count = tool_result.get("bytes_count", 0)
            preview = content[:500] if content else "(No text content retrieved)"
            return f"Fetched **{url}** ({bytes_count} bytes):\n\n```\n{preview}\n```"

        return str(tool_result)
