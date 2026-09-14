"""JARVIS Computer Specialist Stub (Stage 6)."""

from typing import Any

from jarvis.agents.base import BaseAgent, SpecialistManifest


class ComputerSpecialist(BaseAgent):
    """Specialist for desktop OS control, window focus, keyboard/mouse interaction, and apps."""

    def __init__(self) -> None:
        super().__init__(
            manifest=SpecialistManifest(
                name="computer",
                role_description="Desktop OS automation, native Windows application control, and GUI interaction.",
                allowed_tool_scopes=["os.window", "os.process", "gui.input", "audio.control"],
                memory_mode="PER_SPECIALIST",
            )
        )

    async def process_task(self, task_payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("ComputerSpecialist will be implemented in Stage 6.")
