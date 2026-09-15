"""JARVIS Analysis Specialist.

Handles deterministic mathematical calculation, data transformations,
statistical analysis, and model evaluation (ARCHITECTURE.md Layer 8 & 10).
"""

import re
from typing import Any

from jarvis.agents.base import (
    BaseSpecialist,
    SpecialistManifest,
    SpecialistProposal,
    SpecialistRole,
)


class AnalysisSpecialist(BaseSpecialist):
    """Specialist for math computation, metric evaluation, and data transformations."""

    def __init__(self) -> None:
        super().__init__(
            manifest=SpecialistManifest(
                name="analysis",
                role=SpecialistRole.ANALYSIS,
                role_description="Deterministic mathematical computation, data transformations, and metric analysis.",
                allowed_tool_scopes=["native:calc:evaluate", "data.parse", "data.transform"],
                memory_mode="PER_SPECIALIST",
            )
        )

    async def propose(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> SpecialistProposal:
        """Evaluate mathematical or data analysis request."""
        msg = user_message.strip()
        lower = msg.lower()

        # Check for calculation requests: calculate, compute, evaluate, math
        math_prefixes = ("calculate", "compute", "evaluate", "what is", "how much is")
        for prefix in math_prefixes:
            if lower.startswith(prefix):
                expr = msg[len(prefix) :].strip().rstrip("?")
                # Check if contains arithmetic characters
                if any(c in expr for c in "+-*/%^0123456789()"):
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=f"Evaluate math expression '{expr}'",
                        tool_id="native:calc:evaluate",
                        arguments={"expression": expr},
                        target_resource="math_engine",
                    )

        # Check if the whole string is an arithmetic expression, e.g. "2 + 2", "sqrt(144) * 10"
        if re.match(r"^[\d\s\+\-\*\/\(\)\.\%\^eE\,sqrt|sin|cos|tan|log|pi|abs]+$", msg):
            return SpecialistProposal(
                specialist_role=self.role,
                intent=f"Evaluate math expression '{msg}'",
                tool_id="native:calc:evaluate",
                arguments={"expression": msg},
                target_resource="math_engine",
            )

        if any(w in lower for w in ("analyze", "metrics", "statistics", "data analysis")):
            # Vague analysis -> ask clarifying question!
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Data analysis",
                needs_clarification=True,
                clarification_question="What data or metric would you like me to analyze? Please provide the dataset, numbers, or formula.",
            )

        return SpecialistProposal(
            specialist_role=self.role,
            intent="General analysis query",
            direct_response="I am the Analysis Specialist. I provide deterministic calculations, statistical modeling, and data transformations. How can I help?",
        )

    async def synthesize(
        self,
        proposal: SpecialistProposal,
        tool_result: Any,
        user_message: str,
    ) -> str:
        """Synthesize calculation output."""
        if proposal.tool_id == "native:calc:evaluate":
            expr = tool_result.get("expression", "")
            res = tool_result.get("result")
            return f"Calculated: `{expr}` = **{res}**"

        return str(tool_result)
