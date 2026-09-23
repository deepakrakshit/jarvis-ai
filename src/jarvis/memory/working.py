"""Working Memory Buffer for Active JARVIS Sessions.

Provides fast, in-memory state tracking for active turns, current plan steps,
scratchpad key-values, and short-term task observations.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InteractionTurn(BaseModel):
    """Single conversational turn in working memory."""

    role: str  # user, assistant, system, tool
    content: str
    name: Optional[str] = None
    timestamp: datetime = Field(default_factory=utc_now)


class WorkingMemory:
    """Ephemeral working context maintained per active session."""

    def __init__(self, session_id: str, max_turns: int = 20) -> None:
        self.session_id = session_id
        self.max_turns = max_turns
        self.turns: List[InteractionTurn] = []
        self.scratchpad: Dict[str, Any] = {}
        self.current_goal: Optional[str] = None
        self.active_plan: List[str] = []
        self.recent_observations: List[str] = []
        self.last_updated: datetime = utc_now()

    def add_turn(self, role: str, content: str, name: Optional[str] = None) -> None:
        """Add an interaction turn, compacting older turns if max_turns is exceeded."""
        turn = InteractionTurn(role=role, content=content, name=name)
        self.turns.append(turn)
        if len(self.turns) > self.max_turns:
            # Preserve the very first turn (often the original goal) and trim oldest intermediate turns
            self.turns = [self.turns[0]] + self.turns[-(self.max_turns - 1) :]
        self.last_updated = utc_now()

    def set_scratchpad(self, key: str, value: Any) -> None:
        """Record ephemeral variable or intermediate calculation."""
        self.scratchpad[key] = value
        self.last_updated = utc_now()

    def get_scratchpad(self, key: str, default: Any = None) -> Any:
        """Retrieve ephemeral variable."""
        return self.scratchpad.get(key, default)

    def set_plan(self, steps: List[str]) -> None:
        """Set or update current active execution plan."""
        self.active_plan = list(steps)
        self.last_updated = utc_now()

    def add_observation(self, observation: str) -> None:
        """Record an action result observation."""
        self.recent_observations.append(observation)
        if len(self.recent_observations) > 10:
            self.recent_observations = self.recent_observations[-10:]
        self.last_updated = utc_now()

    def clear(self) -> None:
        """Reset working memory."""
        self.turns.clear()
        self.scratchpad.clear()
        self.active_plan.clear()
        self.recent_observations.clear()
        self.current_goal = None
        self.last_updated = utc_now()

    def format_summary(self) -> str:
        """Format working memory state into a compact text block for prompt injection."""
        sections: List[str] = []
        if self.current_goal:
            sections.append(f"Current Goal: {self.current_goal}")
        if self.active_plan:
            plan_lines = "\n".join(
                f"  {idx + 1}. {step}" for idx, step in enumerate(self.active_plan)
            )
            sections.append(f"Active Plan:\n{plan_lines}")
        if self.scratchpad:
            scratch_lines = "\n".join(f"  {k}: {v}" for k, v in self.scratchpad.items())
            sections.append(f"Scratchpad State:\n{scratch_lines}")
        if self.recent_observations:
            obs_lines = "\n".join(f"  - {obs}" for obs in self.recent_observations[-5:])
            sections.append(f"Recent Action Observations:\n{obs_lines}")

        return "\n\n".join(sections) if sections else "No active working state."
