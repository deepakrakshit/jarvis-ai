"""JARVIS Personal Specialist.

Handles user preferences, personal scheduling, memory notes, and reminders
with strict confidentiality and privacy boundaries (ARCHITECTURE.md Layer 8 & 10).
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

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


class NoteCategory(StrEnum):
    """Semantic category of personal user note."""

    REMINDER = "REMINDER"
    PREFERENCE = "PREFERENCE"
    FACT = "FACT"


class PersonalNote(BaseModel):
    """Structured personal memory item."""

    note_id: UUID = Field(default_factory=uuid4)
    content: str
    category: NoteCategory = NoteCategory.FACT
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


PERSONAL_SYSTEM_PROMPT = """You are the JARVIS Personal Specialist.
Your domain covers user preferences, personal reminders, daily agenda, and private notes.
You enforce strict confidentiality and privacy boundaries: notes never leave the local boundary.

Existing Notes in Memory:
{notes}

Analyze the user's message.
You MUST output ONLY a valid JSON object matching this schema:
{
  "action": "save_note" | "retrieve_notes" | "clarify" | "direct_answer",
  "note_text": string | null,
  "category": "REMINDER" | "PREFERENCE" | "FACT",
  "intent": string,
  "clarification_question": string | null,
  "direct_response": string | null
}

Rules:
1. If the user asks to save a note or reminder (e.g. "remind me to review the quarterly report tomorrow morning", "remember that my preferred IDE is VSCode"), set action='save_note', categorize it properly, extract the clean note text into note_text, and provide a polite confirmation in direct_response.
2. If the user says "remind me" or "take a note" without providing any details or content, set action='clarify' and ask what they would like to be reminded of.
3. If the user asks to see, view, or list their notes or reminders, set action='retrieve_notes'.
4. For general personal questions, set action='direct_answer'.
"""


class PersonalSpecialist(BaseSpecialist):
    """Specialist for user preferences, notes, reminders, and daily agenda."""

    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
        super().__init__(
            manifest=SpecialistManifest(
                name="personal",
                role=SpecialistRole.PERSONAL,
                role_description="User memory, personal notes, reminders, and preference management.",
                allowed_tool_scopes=[
                    "calendar.read",
                    "notes.write",
                    "memory.read",
                    "memory.write",
                    "memory:read",
                    "memory:write",
                    "native:memory:query",
                    "native:memory:write",
                ],
                memory_mode="SHARED",
            ),
            model_gateway=model_gateway,
        )
        self._notes: list[PersonalNote] = []

    @property
    def notes(self) -> list[PersonalNote]:
        """Read-only access to recorded notes."""
        return list(self._notes)

    async def propose(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> SpecialistProposal:
        """Evaluate personal preference or note request with LLM intelligence or fallback."""
        if self.gateway:
            try:
                notes_str = (
                    "\n".join(f"- [{n.category.value}] {n.content}" for n in self._notes)
                    if self._notes
                    else "None"
                )
                formatted_prompt = PERSONAL_SYSTEM_PROMPT.replace("{notes}", notes_str)

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
                    system_instruction=formatted_prompt,
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
                        or "What would you like me to record in your personal notes or reminders?",
                    )

                if action == "save_note":
                    note_text = str(data.get("note_text") or user_message.strip())
                    cat_str = str(data.get("category", "FACT")).upper()
                    category = (
                        NoteCategory.REMINDER
                        if "REMIND" in cat_str
                        else (NoteCategory.PREFERENCE if "PREF" in cat_str else NoteCategory.FACT)
                    )
                    note = PersonalNote(content=note_text, category=category)
                    self._notes.append(note)
                    self.scratchpad.add_note(f"Saved {category.value}: {note_text}")

                    confirm = (
                        data.get("direct_response")
                        or f'I have recorded this in your personal notes [{category.value}]: *"{note_text}"*.'
                    )
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent="Saved personal note",
                        direct_response=confirm,
                    )

                if action == "retrieve_notes":
                    resp_text = self._format_retrieved_notes(user_message)
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent="Retrieved personal notes",
                        direct_response=resp_text,
                    )

                if action == "direct_answer" and data.get("direct_response"):
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(data.get("intent", "Personal preference query")),
                        direct_response=str(data["direct_response"]),
                    )

            except Exception as exc:
                logger.warning("personal_specialist_llm_fallback", error=str(exc))

        return self._heuristic_propose(user_message)

    def _heuristic_propose(self, user_message: str) -> SpecialistProposal:
        """Rule-based fallback for offline test suites and network disconnection."""
        msg = user_message.strip()
        lower = msg.lower()

        # Check for reminder / note creation
        if any(w in lower for w in ("remind me", "take a note", "remember that", "save note")):
            note_content = msg
            for prefix in (
                "remind me to",
                "remind me",
                "take a note:",
                "take a note",
                "remember that",
                "save note",
            ):
                if lower.startswith(prefix):
                    note_content = msg[len(prefix) :].strip()
                    break

            if not note_content or len(note_content) < 3:
                return SpecialistProposal(
                    specialist_role=self.role,
                    intent="Create reminder/note",
                    needs_clarification=True,
                    clarification_question="What would you like me to remind you about? Please specify the reminder topic.",
                )

            category = (
                NoteCategory.REMINDER
                if "remind" in lower
                else (
                    NoteCategory.PREFERENCE
                    if any(w in lower for w in ("prefer", "preference", "favorite", "like"))
                    else NoteCategory.FACT
                )
            )
            note = PersonalNote(content=note_content, category=category)
            self._notes.append(note)
            self.scratchpad.add_note(f"Saved {category.value}: {note_content}")

            return SpecialistProposal(
                specialist_role=self.role,
                intent="Saved personal note",
                direct_response=f'I have recorded this in your personal notes: *"{note_content}"*.',
            )

        # Check for retrieving notes
        if any(
            w in lower
            for w in (
                "show my notes",
                "get my notes",
                "list notes",
                "my reminders",
                "saved notes",
                "preferences",
                "my notes",
                "show notes",
                "view notes",
                "what are my reminders",
            )
        ):
            resp_text = self._format_retrieved_notes(msg)
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Retrieve personal notes",
                direct_response=resp_text,
            )

        return SpecialistProposal(
            specialist_role=self.role,
            intent="General personal query",
            direct_response="I am the Personal Specialist. I can securely store reminders, personal notes, and preferences.",
        )

    def _format_retrieved_notes(self, query: str) -> str:
        """Format matching notes according to query filter."""
        lower = query.lower()
        filtered = self._notes

        if "all" in lower or ("note" in lower and "reminder" in lower):
            filtered = self._notes
        elif "reminder" in lower:
            filtered = [n for n in self._notes if n.category == NoteCategory.REMINDER]
        elif "preference" in lower:
            filtered = [n for n in self._notes if n.category == NoteCategory.PREFERENCE]

        if not filtered:
            return "You currently have no recorded notes or reminders in this category."

        items = "\n".join(
            f"{i}. [{n.category.value}] {n.content}" for i, n in enumerate(filtered, 1)
        )
        self.scratchpad.add_note(f"Retrieved {len(filtered)} personal notes")
        return f"Here are your recorded personal notes:\n\n{items}"

    async def synthesize(
        self,
        proposal: SpecialistProposal,
        tool_result: Any,
        user_message: str,
    ) -> str:
        """Synthesize personal observation into human-readable response."""
        return proposal.direct_response or str(tool_result)

    async def health_probe(self) -> HealthProbeResult:
        """Diagnostic health check for personal memory integrity."""
        return HealthProbeResult(
            component_id="specialist:personal",
            healthy=True,
            details={"notes_count": len(self._notes)},
        )

    async def repair(self) -> bool:
        """Repair personal specialist state."""
        self.reset_scratchpad()
        return True
