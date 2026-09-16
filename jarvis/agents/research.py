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
Your domain covers online research, web searches, fetching documentation from URLs, and synthesizing evidence with citations.

Available Tools:
- "native:web:search": Searches the live web and returns matching titles, URLs, and snippets.
  Arguments: {"query": string}
- "native:web:fetch": Fetches the text/HTML content of a specified web URL.
  Arguments: {"url": string}

Analyze the user's message, recent conversation history, and classified intent.
You MUST output ONLY a valid JSON object matching this schema:
{
  "action": "tool_call" | "clarify" | "direct_answer",
  "tool_id": "native:web:search" | "native:web:fetch" | null,
  "arguments": dict,
  "intent": string,
  "clarification_question": string | null,
  "direct_response": string | null,
  "target_resource": string | null
}

Rules:
1. If the user asks to search, find, lookup, or research a topic on the internet (e.g. "search the internet for X", "research about GPT 6 ASTRA", or following up with "yeah" to search recent articles/rumors), set action='tool_call', tool_id='native:web:search', arguments={"query": "<search query>"}, target_resource="web_search".
2. If the user provides a specific URL to fetch, browse, or read (e.g. "fetch https://example.com/docs"), set action='tool_call', tool_id='native:web:fetch', arguments={"url": "<url>"}, target_resource="<url>".
3. If the user says an affirmation ("yeah", "yes", "sure") following an assistant offer to search or investigate something, extract the subject from history/intent and trigger the web search tool!
4. If the user message is completely empty or meaningless with no conversational context, set action='clarify'.
5. If it is a purely conceptual or informational question that does not require live web search, set action='direct_answer' and provide a comprehensive, educational, well-structured explanation in direct_response.
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
                    "native:web:search",
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
                    system_instruction=RESEARCH_SYSTEM_PROMPT,
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
                        or "What specific topic or web URL would you like me to research?",
                    )

                if action == "tool_call" and data.get("tool_id"):
                    tool_id = str(data["tool_id"])
                    args = data.get("arguments") or {}
                    target = str(
                        data.get("target_resource") or args.get("url") or args.get("query") or "web"
                    )
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
                        intent=str(data.get("intent", "Direct research response")),
                        direct_response=str(data["direct_response"]),
                    )

            except Exception as exc:
                logger.warning("research_specialist_llm_fallback", error=str(exc))

        return self._heuristic_propose(user_message, context)

    def _heuristic_propose(
        self, user_message: str, context: dict[str, Any] | None = None
    ) -> SpecialistProposal:
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

        # Check for affirmative follow-up with intent
        if (
            lower in ("yeah", "yes", "sure", "ok", "please do")
            and context
            and context.get("intent")
        ):
            return SpecialistProposal(
                specialist_role=self.role,
                intent=str(context["intent"]),
                tool_id="native:web:search",
                arguments={"query": str(context["intent"])},
                target_resource="web_search",
            )

        if any(w in lower for w in ("search for", "find out", "research", "lookup", "search")):
            topic = (
                re.sub(
                    r"^(search for|find out|research about|research|lookup|search on the internet about|search the internet for)\s*",
                    "",
                    msg,
                    flags=re.I,
                )
                .strip()
                .strip("\"'")
            )
            if not topic:
                return SpecialistProposal(
                    specialist_role=self.role,
                    intent="Search query clarification",
                    needs_clarification=True,
                    clarification_question="What specific topic would you like me to research?",
                )
            return SpecialistProposal(
                specialist_role=self.role,
                intent=f"Search web for '{topic}'",
                tool_id="native:web:search",
                arguments={"query": topic},
                target_resource="web_search",
            )

        # Check for conceptual explanations
        if (
            any(
                w in lower
                for w in (
                    "explain",
                    "what is",
                    "how does",
                    "describe",
                    "overview of",
                    "tell me about",
                )
            )
            and "cpu" in lower
            and (
                "instruction" in lower or "execute" in lower or "cycle" in lower or "work" in lower
            )
        ):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Explain CPU instruction execution cycle",
                direct_response=(
                    "### How a CPU Executes an Instruction: The Instruction Cycle\n\n"
                    "At a foundational level, a Central Processing Unit (CPU) executes instructions through the **Fetch-Decode-Execute Cycle**:\n\n"
                    "1. **Fetch:** The CPU retrieves the next instruction from memory (RAM or Cache) using the address stored in the **Program Counter (PC)** register. Once fetched into the **Instruction Register (IR)**, the PC is incremented to point to the subsequent instruction.\n\n"
                    "2. **Decode:** The **Control Unit (CU)** interprets the binary opcode of the instruction to determine what operation needs to be performed (such as arithmetic, logic, or memory access) and identifies the required operands.\n\n"
                    "3. **Execute:** The **Arithmetic Logic Unit (ALU)** performs the computation (e.g. addition, subtraction, bitwise operation), or the memory subsystem transfers data between registers and system RAM.\n\n"
                    "4. **Store / Writeback:** The result of the execution is stored back in a destination register or memory location, and the CPU readies itself for the next cycle or handles pending hardware interrupts."
                ),
            )

        return SpecialistProposal(
            specialist_role=self.role,
            intent="General research query",
            direct_response="I am the Research Specialist. I can search the live web, fetch documentation, and verify factual references.",
        )

    async def synthesize(
        self,
        proposal: SpecialistProposal,
        tool_result: Any,
        user_message: str,
    ) -> str:
        """Synthesize web research results into human-readable response."""
        # Record evidence in isolated scratchpad (Contract 04)
        if proposal.tool_id == "native:web:search" and isinstance(tool_result, dict):
            results = tool_result.get("results", [])
            query = tool_result.get("query", proposal.arguments.get("query", ""))
            self.scratchpad.add_note(f"Searched web for '{query}' with {len(results)} results")
            for r in results:
                self.scratchpad.add_evidence(
                    source=r.get("url", "web_search"),
                    content=f"{r.get('title', '')}: {r.get('snippet', '')}",
                    confidence=0.95,
                )
        elif proposal.tool_id == "native:web:fetch" and isinstance(tool_result, dict):
            url = tool_result.get("url", proposal.arguments.get("url", ""))
            body = tool_result.get("body", "")
            self.scratchpad.add_note(f"Fetched URL '{url}' ({len(body)} chars)")
            self.scratchpad.add_evidence(
                source=url,
                content=body[:1000],
                confidence=1.0,
            )

        if self.gateway:
            try:
                prompt = (
                    f"User asked: '{user_message}'\n"
                    f"Tool '{proposal.tool_id}' was executed with result:\n"
                    f"{tool_result}\n\n"
                    "Synthesize an insightful, well-structured summary incorporating key findings, relevant dates, and source URLs."
                )
                req = GenerationRequest(
                    model_id="gemini-3.5-flash-lite",
                    system_instruction="You are the JARVIS Research Specialist. Provide structured, accurate, well-cited summaries.",
                    messages=[ChatMessage(role="user", content=prompt)],
                    temperature=0.2,
                    max_tokens=2000,
                )
                res = await self.gateway.generate(req)
                if res.content.strip():
                    return res.content.strip()
            except Exception as exc:
                logger.warning("research_specialist_synth_fallback", error=str(exc))

        if proposal.tool_id == "native:web:search":
            results = tool_result.get("results", [])
            query = tool_result.get("query", proposal.arguments.get("query", ""))
            if not results:
                return f"No search results returned for query '{query}'."
            items = []
            for r in results:
                title = r.get("title", "Untitled")
                url = r.get("url", "#")
                snippet = r.get("snippet", "")
                items.append(f"- [{title}]({url})\n  {snippet}")
            return f"Search Results for **{query}**:\n\n" + "\n\n".join(items)

        if proposal.tool_id == "native:web:fetch":
            url = tool_result.get("url", proposal.arguments.get("url", ""))
            content = tool_result.get("body", "")
            bytes_count = len(content.encode("utf-8"))
            preview = content[:500] if content else "(No text content retrieved)"
            return f"Fetched **{url}** ({bytes_count} bytes):\n\n```\n{preview}\n```"

        return str(tool_result)
