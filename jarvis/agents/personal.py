"""JARVIS Personal Specialist.

Handles user preferences, personal scheduling, memory notes, and reminders
with strict confidentiality and privacy boundaries (ARCHITECTURE.md Layer 8 & 10).
"""

from typing import Any

from jarvis.agents.base import (
    BaseSpecialist,
    SpecialistManifest,
    SpecialistProposal,
    SpecialistRole,
)


class PersonalSpecialist(BaseSpecialist):
    """Specialist for user preferences, notes, reminders, and daily agenda."""

    def __init__(self) -> None:
        super().__init__(
            manifest=SpecialistManifest(
                name="personal",
                role=SpecialistRole.PERSONAL,
                role_description="User memory, personal notes, reminders, and preference management.",
                allowed_tool_scopes=["calendar.read", "notes.write"],
                memory_mode="SHARED",
            )
        )
        self._user_notes: list[str] = []

    async def propose(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> SpecialistProposal:
        """Evaluate personal preference or note request."""
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
                    clarification_question="What reminder or note would you like me to save?",
                )

            self._user_notes.append(note_content)
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Saved personal note",
                direct_response=f'I have recorded this in your personal notes: *"{note_content}"*.',
            )

        if any(
            w in lower
            for w in ("show notes", "my notes", "what are my reminders", "list reminders")
        ):
            if not self._user_notes:
                return SpecialistProposal(
                    specialist_role=self.role,
                    intent="List notes",
                    direct_response="You currently have no saved notes or reminders.",
                )
            items = "\n".join(f"{i + 1}. {note}" for i, note in enumerate(self._user_notes))
            return SpecialistProposal(
                specialist_role=self.role,
                intent="List notes",
                direct_response=f"Here are your active notes and reminders:\n{items}",
            )

        return SpecialistProposal(
            specialist_role=self.role,
            intent="General personal query",
            direct_response="I am the Personal Specialist. I manage your preferences, agenda, and private reminders under strict confidentiality. How can I assist?",
        )

    async def synthesize(
        self,
        proposal: SpecialistProposal,
        tool_result: Any,
        user_message: str,
    ) -> str:
        """Synthesize personal results."""
        return str(tool_result)
