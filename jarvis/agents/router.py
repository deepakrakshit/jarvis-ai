"""JARVIS Capability Specialist Router.

Directs incoming user intents to the optimal capability specialist or handles
conversational queries as the central JARVIS OS persona (ARCHITECTURE.md Layer 7 & Layer 8,
MODEL_ROSTER.md Pool B: gemini-3.1-flash-lite / gemini-3.5-flash-lite).
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from jarvis.agents.analysis import AnalysisSpecialist
from jarvis.agents.base import BaseSpecialist, SpecialistRole, parse_llm_json
from jarvis.agents.coding import CodingSpecialist
from jarvis.agents.computer import ComputerSpecialist
from jarvis.agents.personal import PersonalSpecialist
from jarvis.agents.research import ResearchSpecialist
from jarvis.core.gateway.interfaces import ChatMessage, GenerationRequest
from jarvis.core.gateway.router import ModelGateway
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class RoutingCategory(StrEnum):
    """Broad categorization of user prompts at Layer 07."""

    CONVERSATIONAL = "conversational"
    """Greetings, general chat, identity/capability questions, chitchat."""

    SPECIALIST = "specialist"
    """Actionable domain tasks requiring one of the 5 canonical specialists."""


class RoutingDecision(BaseModel):
    """Structured decision produced by the Layer 07 Intent Classifier."""

    category: RoutingCategory
    specialist_role: SpecialistRole | None = None
    intent: str = "General user request"
    direct_response: str | None = None
    reasoning: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


ROUTER_SYSTEM_PROMPT = """You are the JARVIS Layer 07 Capability Router and Intent Classifier.
You are the intelligent front-door for the JARVIS Personal AI Operating System.

Your job is to analyze the user message (taking into account recent conversation history) and classify it:

Categories:
1. "conversational":
   - The user is offering a greeting ("hey", "hello", "hi", "good morning", etc.).
   - The user is asking how you are doing ("how are you", "what's up", etc.).
   - The user is inquiring about your identity, origins, or capabilities ("who are you", "what can you do for me", "help", "what are your skills", etc.).
   - The user is engaging in general pleasantries, gratitude ("thank you", "thanks"), or open-ended conversation.
   - For "conversational", you MUST formulate a direct, warm, articulate, and helpful response in "direct_response" in the voice of JARVIS (the Personal AI Operating System).

2. "specialist":
   - The request requires an actionable task or query handled by one of our 5 canonical domain specialists:
     * "coding": Filesystem operations (reading, writing, inspecting, listing files), code analysis, debugging, terminal commands, test running.
     * "analysis": Mathematical calculations, formula evaluations (e.g. sqrt, arithmetic), numeric statistics, quantitative data analysis.
     * "computer": Point-in-time system clock, current time, date, operating system status, desktop application lifecycle (open/launch/close/list apps), screen capture, and GUI input automation.
     * "personal": Saving user preferences, remembering statements ("remember that..."), setting reminders ("remind me to..."), saving or recalling personal notes/facts. Any request to remember, record, or recall user preferences or personal facts MUST ALWAYS be classified as "specialist" with specialist_role="personal", NEVER as "conversational".
     * "research": Web searches, fetching URLs, external documentation, citations.
   - CRITICAL: If the user asks to perform a domain action (e.g. "can you inspect a file?", "read a file for me", "calculate something", "search something") but omits parameters (such as the target file path, expression, or search topic), you MUST STILL classify it as "specialist" with the appropriate specialist_role so the specialist can proactively ask the user for the missing parameters!

You MUST respond with ONLY a valid JSON object matching this schema:
{
  "category": "conversational" | "specialist",
  "specialist_role": "coding" | "analysis" | "computer" | "personal" | "research" | null,
  "intent": "<concise summary of intent>",
  "direct_response": "<full conversational response as JARVIS if category is conversational, else null>",
  "reasoning": "<brief classification reason>"
}
"""


class SpecialistRouter:
    """Routes user requests to one of the 5 canonical capability specialists or handles conversational chat."""

    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
        self.gateway = model_gateway or ModelGateway()
        self.specialists: dict[SpecialistRole, BaseSpecialist] = {
            SpecialistRole.CODING: CodingSpecialist(model_gateway=self.gateway),
            SpecialistRole.RESEARCH: ResearchSpecialist(model_gateway=self.gateway),
            SpecialistRole.COMPUTER: ComputerSpecialist(model_gateway=self.gateway),
            SpecialistRole.PERSONAL: PersonalSpecialist(model_gateway=self.gateway),
            SpecialistRole.ANALYSIS: AnalysisSpecialist(model_gateway=self.gateway),
        }

    async def route_intent(
        self,
        user_message: str,
        history: list[dict[str, Any]] | None = None,
    ) -> RoutingDecision:
        """Analyze user message with LLM intelligence (gemini-3.1-flash-lite / fallback)."""
        messages: list[ChatMessage] = []

        if history:
            recent = history[-4:]
            for turn in recent:
                u_text = str(turn.get("user_message", "")).strip()
                a_text = str(turn.get("assistant_response", "")).strip()
                if u_text:
                    messages.append(ChatMessage(role="user", content=u_text))
                if a_text:
                    messages.append(ChatMessage(role="assistant", content=a_text))

        messages.append(
            ChatMessage(role="user", content=f"Classify this user message:\n{user_message}")
        )

        if self.gateway:
            try:
                req = GenerationRequest(
                    model_id="gemini-3.5-flash-lite",
                    system_instruction=ROUTER_SYSTEM_PROMPT,
                    messages=messages,
                    temperature=0.0,
                    max_tokens=300,
                )
                res = await self.gateway.generate(req)
                parsed = parse_llm_json(res.content)
                cat_str = str(parsed.get("category", "conversational")).lower()
                category = (
                    RoutingCategory.SPECIALIST
                    if "spec" in cat_str
                    else RoutingCategory.CONVERSATIONAL
                )

                role_val = parsed.get("specialist_role")
                role_enum: SpecialistRole | None = None
                if role_val:
                    try:
                        role_enum = SpecialistRole(str(role_val).lower())
                    except ValueError:
                        role_enum = None

                return RoutingDecision(
                    category=category,
                    specialist_role=role_enum,
                    intent=str(parsed.get("intent", "Routed intent")),
                    direct_response=parsed.get("direct_response"),
                    reasoning=parsed.get("reasoning"),
                )
            except Exception as exc:
                logger.warning("router_llm_classification_failed", error=str(exc))

        return self._heuristic_route(user_message)

    def _heuristic_route(self, user_message: str) -> RoutingDecision:
        """Rule-based fallback routing for deterministic test suites and offline modes."""
        msg = user_message.strip().lower()

        # Conversational greetings
        if any(w in msg for w in ("hello", "hi ", "hey", "good morning", "good evening", "howdy")):
            return RoutingDecision(
                category=RoutingCategory.CONVERSATIONAL,
                intent="Greeting",
                direct_response="Greetings. I am JARVIS, your Personal AI Operating System. How may I assist you today?",
            )

        if any(w in msg for w in ("how are you", "how's it going", "how are u")):
            return RoutingDecision(
                category=RoutingCategory.CONVERSATIONAL,
                intent="Pleasantry",
                direct_response="I am functioning within optimal operational parameters. All systems are online. How can I help you?",
            )

        if any(w in msg for w in ("thank you", "thanks", "appreciate it")):
            return RoutingDecision(
                category=RoutingCategory.CONVERSATIONAL,
                intent="Gratitude",
                direct_response="You are most welcome. Standing by for your next command.",
            )

        if any(
            w in msg for w in ("what can you do", "what can u do", "who are you", "what are you")
        ):
            return RoutingDecision(
                category=RoutingCategory.CONVERSATIONAL,
                intent="Capabilities inquiry",
                direct_response=(
                    "I am JARVIS, your Personal AI Operating System. I can assist you across multiple domains:\n\n"
                    "• **Coding & Files**: Inspect, create, refactor code, list directories, and execute tests.\n"
                    "• **Analysis & Math**: Compute mathematical expressions and evaluate data.\n"
                    "• **Computer & OS**: Manage desktop applications (launch/close/list), inspect screen, and monitor environment.\n"
                    "• **Personal Notes**: Securely organize notes and reminders.\n"
                    "• **Research**: Fetch web resources and summarize information.\n\n"
                    "What would you like to work on?"
                ),
            )

        # Specialist classification
        if any(
            w in msg
            for w in ("calculate", "compute", "evaluate", "how much is", "math", "sqrt(", "pi")
        ):
            return RoutingDecision(
                category=RoutingCategory.SPECIALIST,
                specialist_role=SpecialistRole.ANALYSIS,
                intent="Mathematical calculation",
            )

        if any(
            w in msg
            for w in (
                "what time",
                "current time",
                "clock",
                "what date",
                "today's date",
                "screenshot",
                "capture screen",
                "screen capture",
                "see my screen",
                "active window",
                "running apps",
                "list apps",
                "system stats",
                "system status",
                "cpu",
                "ram",
            )
        ) or (
            any(
                msg.startswith(p)
                for p in ("open ", "launch ", "close ", "quit ", "start ", "kill ")
            )
            and not any(
                f in msg for f in ("file", "code", "directory", "dir", "script", "repo", "test")
            )
        ):
            return RoutingDecision(
                category=RoutingCategory.SPECIALIST,
                specialist_role=SpecialistRole.COMPUTER,
                intent="Computer & OS action",
            )

        if any(
            w in msg for w in ("remind me", "take a note", "remember that", "save note", "my notes")
        ):
            return RoutingDecision(
                category=RoutingCategory.SPECIALIST,
                specialist_role=SpecialistRole.PERSONAL,
                intent="Personal note/reminder",
            )

        if any(
            w in msg
            for w in ("http://", "https://", "search for", "find out", "research", "browse")
        ):
            return RoutingDecision(
                category=RoutingCategory.SPECIALIST,
                specialist_role=SpecialistRole.RESEARCH,
                intent="Web research",
            )

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
            )
        ):
            return RoutingDecision(
                category=RoutingCategory.SPECIALIST,
                specialist_role=SpecialistRole.CODING,
                intent="Coding task",
            )

        # General conversational fallback
        return RoutingDecision(
            category=RoutingCategory.CONVERSATIONAL,
            intent="General conversational inquiry",
            direct_response="I am JARVIS, your Personal AI Operating System. How may I assist you with your code, calculations, or tasks?",
        )

    def route(self, user_message: str, context: dict[str, Any] | None = None) -> BaseSpecialist:
        """Synchronous routing interface for backwards compatibility."""
        decision = self._heuristic_route(user_message)
        if decision.specialist_role:
            return self.specialists[decision.specialist_role]
        return self.specialists[SpecialistRole.CODING]

    def get_specialist(self, role: SpecialistRole) -> BaseSpecialist:
        """Get specialist by its enum role."""
        return self.specialists[role]
