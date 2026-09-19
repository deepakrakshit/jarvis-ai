"""Authoritative Model Router for JARVIS Cognition.

Enforces Section 23 of ARCHITECTURE.md:
Routes tasks dynamically across the strict 6-model runtime allowlist based on
task classification, capability requirements, quota health, and latency budgets,
with turn-local candidate fallback chains.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from jarvis.cognition.providers.base import BaseModelProvider
from jarvis.cognition.providers.google_genai import GoogleGenAIProvider
from jarvis.cognition.providers.groq_provider import GroqProvider
from jarvis.cognition.quota_manager import QuotaManager, quota_manager
from jarvis.contracts.model import (
    ModelFamily,
    ModelInvocationRequest,
    ModelInvocationResponse,
)
from jarvis.telemetry import logger


class TaskClass(str, Enum):
    """Functional task classifications for routing decisions."""

    REALTIME = "realtime"
    SIMPLE_TOOL = "simple_tool"
    MULTIMODAL_WORKER = "multimodal_worker"
    DEEP_REASONING = "deep_reasoning"
    CODING = "coding"
    EXTRACTION_SUMMARIZATION = "extraction_summarization"


# Canonical routing policy mappings from Section 23.2 of ARCHITECTURE.md
DEFAULT_ROUTING_POLICY: Dict[TaskClass, List[ModelFamily]] = {
    TaskClass.REALTIME: [
        ModelFamily.GEMINI_3_8_LIVE,
        ModelFamily.GEMINI_3_5_FLASH_LITE,
        ModelFamily.QWEN_3_8_27B,
    ],
    TaskClass.SIMPLE_TOOL: [
        ModelFamily.GEMINI_3_5_FLASH_LITE,
        ModelFamily.GEMINI_3_1_FLASH_LITE,
        ModelFamily.QWEN_3_8_27B,
    ],
    TaskClass.MULTIMODAL_WORKER: [
        ModelFamily.GEMMA_4_31B,
        ModelFamily.QWEN_3_8_27B,
        ModelFamily.GEMINI_3_5_FLASH_LITE,
    ],
    TaskClass.DEEP_REASONING: [
        ModelFamily.GPT_OSS_120B,
        ModelFamily.QWEN_3_8_27B,
        ModelFamily.GEMINI_3_5_FLASH_LITE,
    ],
    TaskClass.CODING: [
        ModelFamily.GPT_OSS_120B,
        ModelFamily.QWEN_3_8_27B,
        ModelFamily.GEMINI_3_5_FLASH_LITE,
    ],
    TaskClass.EXTRACTION_SUMMARIZATION: [
        ModelFamily.GEMINI_3_1_FLASH_LITE,
        ModelFamily.GEMINI_3_5_FLASH_LITE,
        ModelFamily.QWEN_3_8_27B,
    ],
}


class ModelRouter:
    """Intelligent dispatcher matching requests to optimal runtime models."""

    def __init__(
        self,
        quotas: Optional[QuotaManager] = None,
        providers: Optional[List[BaseModelProvider]] = None,
    ) -> None:
        self.quotas = quotas or quota_manager
        self.providers = providers or [GoogleGenAIProvider(), GroqProvider()]

    def _resolve_provider(self, model_family: ModelFamily) -> Optional[BaseModelProvider]:
        """Find the registered provider capable of executing the model family."""
        for p in self.providers:
            if p.supports(model_family):
                return p
        return None

    def get_candidate_chain(
        self,
        task_class: TaskClass,
        custom_fallback: Optional[List[ModelFamily]] = None,
    ) -> List[ModelFamily]:
        """Resolve ordered list of model candidates for a task class."""
        if custom_fallback:
            return custom_fallback
        return list(DEFAULT_ROUTING_POLICY.get(task_class, [ModelFamily.GEMINI_3_5_FLASH_LITE]))

    async def invoke(
        self,
        request: ModelInvocationRequest,
        task_class: Optional[TaskClass] = None,
        latency_budget_ms: Optional[float] = None,
    ) -> ModelInvocationResponse:
        """Route and execute request through candidate chain with bounded fallback."""
        # Determine candidate chain
        if task_class is not None:
            candidates = self.get_candidate_chain(task_class)
            # Ensure the explicitly requested model is front of chain if valid
            if request.model_family in candidates:
                candidates.remove(request.model_family)
            candidates = [request.model_family] + candidates
        else:
            candidates = [request.model_family]

        last_error = ""

        for candidate in candidates:
            # Check scheduling feasibility
            can_run = await self.quotas.can_schedule(
                model_family=candidate,
                estimated_tokens=request.max_output_tokens // 2,
                max_latency_ms=latency_budget_ms,
            )

            if not can_run and len(candidates) > 1:
                logger.warning(
                    f"Candidate {candidate.value} unschedulable (quota/health/latency); advancing fallback chain"
                )
                continue

            provider = self._resolve_provider(candidate)
            if not provider:
                logger.warning(f"No provider registered for {candidate.value}; advancing fallback")
                continue

            # Clone request for this candidate
            candidate_req = request.model_copy(update={"model_family": candidate})

            response = await provider.invoke(candidate_req)
            if response.error is None and response.finish_reason != "ERROR":
                if candidate != request.model_family:
                    logger.info(
                        f"Turn-local fallback succeeded: answered by {candidate.value} instead of {request.model_family.value}"
                    )
                return response

            last_error = response.error or "Unknown error"
            logger.warning(
                f"Attempt with candidate {candidate.value} failed: {last_error}; advancing fallback chain"
            )

        # If all candidates exhausted, return error response
        logger.error(f"All candidate models exhausted. Last error: {last_error}")
        return ModelInvocationResponse(
            model_family=request.model_family,
            provider=self.providers[0].provider_type,
            error=f"Exhausted all candidates in routing chain. Last error: {last_error}",
            finish_reason="EXHAUSTED",
        )

    async def complete(
        self,
        prompt: str,
        task_class: TaskClass = TaskClass.SIMPLE_TOOL,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: int = 2048,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> ModelInvocationResponse:
        """High-level completion utility routing automatically by task class."""
        candidates = self.get_candidate_chain(task_class)
        primary = candidates[0]

        request = ModelInvocationRequest(
            model_family=primary,
            system_instruction=system_instruction,
            prompt=prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            tools=tools or [],
        )

        return await self.invoke(request=request, task_class=task_class)


# Global singleton instance
model_router = ModelRouter()
