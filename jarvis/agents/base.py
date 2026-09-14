"""JARVIS Capability Specialist Base Interface.

Defines the foundation for domain capability specialists.
"""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class SpecialistManifest(BaseModel):
    """Declarative capability specialist manifest."""

    name: str
    role_description: str
    allowed_tool_scopes: list[str] = Field(default_factory=list)
    memory_mode: str = "PER_SPECIALIST"  # SHARED or PER_SPECIALIST


class BaseAgent(ABC):
    """Abstract interface for all JARVIS capability specialists."""

    def __init__(self, manifest: SpecialistManifest) -> None:
        self.manifest = manifest

    @abstractmethod
    async def process_task(self, task_payload: dict[str, Any]) -> dict[str, Any]:
        """Process an assigned task and return structured output."""
        pass
