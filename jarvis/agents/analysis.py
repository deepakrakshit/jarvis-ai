"""JARVIS Analysis Specialist.

Handles deterministic mathematical calculation, data transformations,
statistical analysis, and model evaluation with LLM intelligence (ARCHITECTURE.md Layer 8 & 10).
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

ANALYSIS_SYSTEM_PROMPT = """You are the JARVIS Analysis Specialist.
Your domain covers deterministic mathematics, formula evaluation, numeric transformations, and data analysis.

Available Tools:
- "native:calc:evaluate": Safely evaluates mathematical and scientific expressions (e.g. sqrt, log, sin, cos, round, pi, arithmetic).
  Arguments: {"expression": string} (e.g. "sqrt(256) * 4 + 10")

Analyze the user's message.
You MUST output ONLY a valid JSON object matching this schema:
{
  "action": "tool_call" | "clarify" | "direct_answer",
  "tool_id": "native:calc:evaluate" | null,
  "arguments": dict,
  "intent": string,
  "clarification_question": string | null,
  "direct_response": string | null,
  "target_resource": string | null
}

Rules:
1. If the user provides a mathematical calculation, expression, or formula to compute (e.g. "calculate sqrt(144) * 5", "how much is 15 * 3", "25 + 4"), extract the clean mathematical expression into arguments["expression"], set action='tool_call', tool_id='native:calc:evaluate', target_resource='math_engine'.
2. If the user asks for vague or underspecified analysis (e.g. "analyze the metrics please", "can you check the numbers?") without providing numbers or formulas, set action='clarify' and ask what numbers or metrics they want evaluated.
3. If it is a conceptual math or statistics question, set action='direct_answer'.
"""


class AnalysisSpecialist(BaseSpecialist):
    """Specialist for math computation, metric evaluation, and data transformations."""

    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
        super().__init__(
            manifest=SpecialistManifest(
                name="analysis",
                role=SpecialistRole.ANALYSIS,
                role_description="Deterministic mathematical computation, data transformations, and metric analysis.",
                allowed_tool_scopes=["native:calc:evaluate", "data.parse", "data.transform"],
                memory_mode="PER_SPECIALIST",
            ),
            model_gateway=model_gateway,
        )

    async def propose(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> SpecialistProposal:
        """Evaluate mathematical or data analysis request with LLM intelligence or fallback."""
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
                    system_instruction=ANALYSIS_SYSTEM_PROMPT,
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
                        or "Which mathematical expression or dataset would you like me to analyze?",
                    )

                if action == "tool_call" and data.get("tool_id"):
                    tool_id = str(data["tool_id"])
                    args = data.get("arguments") or {}
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(
                            data.get("intent", f"Evaluate {args.get('expression', 'math')}")
                        ),
                        tool_id=tool_id,
                        arguments=args,
                        target_resource="math_engine",
                    )

                if action == "direct_answer" and data.get("direct_response"):
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(data.get("intent", "Direct math response")),
                        direct_response=str(data["direct_response"]),
                    )

            except Exception as exc:
                logger.warning("analysis_specialist_llm_fallback", error=str(exc))

        return self._heuristic_propose(user_message)

    def _heuristic_propose(self, user_message: str) -> SpecialistProposal:
        """Rule-based fallback for offline test suites and network disconnection."""
        msg = user_message.strip()
        lower = msg.lower()

        # Check for calculation requests: calculate, compute, evaluate, math
        math_prefixes = ("calculate", "compute", "evaluate", "what is", "how much is")
        for prefix in math_prefixes:
            if lower.startswith(prefix):
                expr = msg[len(prefix) :].strip().rstrip("?")
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

        if any(w in lower for w in ("analyze", "metrics", "data", "numbers")):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Clarification for metric analysis",
                needs_clarification=True,
                clarification_question="What specific numerical dataset or mathematical expression would you like me to analyze?",
            )

        return SpecialistProposal(
            specialist_role=self.role,
            intent="General analysis query",
            direct_response="I am the Analysis Specialist. I can compute safe mathematical formulas, parse structured data, and analyze metrics.",
        )

    async def synthesize(
        self,
        proposal: SpecialistProposal,
        tool_result: Any,
        user_message: str,
    ) -> str:
        """Synthesize calculation observation into human-readable response."""
        if self.gateway:
            try:
                prompt = (
                    f"User asked: '{user_message}'\n"
                    f"Calculation result: {tool_result}\n\n"
                    "Provide a crisp, clear, accurate statement of the mathematical result for the user."
                )
                req = GenerationRequest(
                    model_id="gemini-3.5-flash-lite",
                    system_instruction="You are the JARVIS Analysis Specialist. Present mathematical results cleanly and accurately.",
                    messages=[ChatMessage(role="user", content=prompt)],
                    temperature=0.1,
                    max_tokens=1500,
                )
                res = await self.gateway.generate(req)
                if res.content.strip():
                    return res.content.strip()
            except Exception as exc:
                logger.warning("analysis_specialist_synth_fallback", error=str(exc))

        if proposal.tool_id == "native:calc:evaluate":
            expr = tool_result.get("expression", proposal.arguments.get("expression", ""))
            val = tool_result.get("result", "")
            return f"Calculated: `{expr}` = **{val}**"

        return str(tool_result)
