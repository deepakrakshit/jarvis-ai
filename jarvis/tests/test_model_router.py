"""Tests for Model Router and Multi-Model Execution."""

from typing import List

import pytest

from jarvis.cognition.model_router import ModelRouter, TaskClass
from jarvis.cognition.providers.base import BaseModelProvider
from jarvis.cognition.quota_manager import QuotaManager
from jarvis.config import settings
from jarvis.contracts.model import (
    ModelFamily,
    ModelInvocationRequest,
    ModelInvocationResponse,
    ModelProvider,
)


class MockProvider(BaseModelProvider):
    """Predictable mock provider for testing fallback chains and routing."""

    def __init__(self, failing_models: List[ModelFamily]) -> None:
        self.failing_models = set(failing_models)
        self.invoked_models: List[ModelFamily] = []

    @property
    def provider_type(self) -> ModelProvider:
        return ModelProvider.GOOGLE_GENAI

    def supports(self, model_family: ModelFamily) -> bool:
        return True

    async def invoke(self, request: ModelInvocationRequest) -> ModelInvocationResponse:
        self.invoked_models.append(request.model_family)
        if request.model_family in self.failing_models:
            return ModelInvocationResponse(
                model_family=request.model_family,
                provider=ModelProvider.GOOGLE_GENAI,
                error="Simulated provider failure",
                finish_reason="ERROR",
            )
        return ModelInvocationResponse(
            model_family=request.model_family,
            provider=ModelProvider.GOOGLE_GENAI,
            text_content=f"Response from {request.model_family.value}",
            total_tokens=42,
            finish_reason="STOP",
        )


@pytest.mark.asyncio
async def test_model_router_candidate_chains() -> None:
    """Verify Section 23 default candidate chains."""
    router = ModelRouter()

    deep_chain = router.get_candidate_chain(TaskClass.DEEP_REASONING)
    assert deep_chain[0] == ModelFamily.GPT_OSS_120B
    assert ModelFamily.QWEN_3_8_27B in deep_chain

    coding_chain = router.get_candidate_chain(TaskClass.CODING)
    assert coding_chain[0] == ModelFamily.GPT_OSS_120B

    tool_chain = router.get_candidate_chain(TaskClass.SIMPLE_TOOL)
    assert tool_chain[0] == ModelFamily.GEMINI_3_5_FLASH_LITE


@pytest.mark.asyncio
async def test_model_router_fallback_execution() -> None:
    """Verify turn-local fallback when primary candidate fails."""
    # Mock primary (GPT-OSS 120B) failing, secondary (Qwen 3.8 27B) succeeding
    mock = MockProvider(failing_models=[ModelFamily.GPT_OSS_120B])
    router = ModelRouter(quotas=QuotaManager(), providers=[mock])

    req = ModelInvocationRequest(
        model_family=ModelFamily.GPT_OSS_120B,
        prompt="Synthesize architecture invariants",
    )

    res = await router.invoke(req, task_class=TaskClass.DEEP_REASONING)
    assert res.error is None
    assert res.model_family == ModelFamily.QWEN_3_8_27B
    assert "Response from Qwen 3.8 27B" in (res.text_content or "")
    assert mock.invoked_models == [ModelFamily.GPT_OSS_120B, ModelFamily.QWEN_3_8_27B]


@pytest.mark.asyncio
async def test_live_groq_reasoning_completion() -> None:
    """Live verification of Groq provider execution with real API credentials."""
    if not settings.GROQ_API_KEY:
        pytest.skip("GROQ_API_KEY not configured")

    router = ModelRouter()
    req = ModelInvocationRequest(
        model_family=ModelFamily.QWEN_3_8_27B,
        prompt="Respond with exactly 'JARVIS Cognition Online'",
        max_output_tokens=60,
    )
    res = await router.invoke(req)
    assert res.error is None
    assert res.text_content is not None
    assert len(res.text_content) > 0
    assert res.total_tokens > 0


@pytest.mark.asyncio
async def test_live_google_genai_completion() -> None:
    """Live verification of Google GenAI provider execution with real API credentials."""
    if not settings.GEMINI_API_KEY:
        pytest.skip("GEMINI_API_KEY not configured")

    router = ModelRouter()
    req = ModelInvocationRequest(
        model_family=ModelFamily.GEMINI_3_5_FLASH_LITE,
        prompt="Respond with exactly 'JARVIS Flash Online'",
        max_output_tokens=60,
    )
    res = await router.invoke(req)
    assert res.error is None
    assert res.text_content is not None
    assert len(res.text_content) > 0
    assert res.total_tokens > 0
