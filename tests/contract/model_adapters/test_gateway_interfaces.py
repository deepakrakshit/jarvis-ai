"""Contract tests for JARVIS Model Provider Adapter Interfaces."""

from collections.abc import AsyncIterator

import pytest

from jarvis.core.gateway.interfaces import (
    ChatMessage,
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    GenerationResponse,
    ModelProviderAdapter,
    ProviderRateLimitHeaders,
    StreamChunk,
)


class MockProviderAdapter(ModelProviderAdapter):
    """Mock implementation of ModelProviderAdapter for contract testing."""

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        return GenerationResponse(
            content="Mock generation output",
            prompt_tokens=15,
            completion_tokens=25,
            finish_reason="stop",
            provider_raw_headers={"x-ratelimit-remaining-tokens": "5000"},
        )

    async def stream(self, request: GenerationRequest) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta_content="Hello")
        yield StreamChunk(delta_content=" world", finish_reason="stop")

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            embeddings=[[0.1, 0.2, 0.3]],
            total_tokens=5,
        )

    def parse_rate_limit_headers(self, headers: dict[str, str]) -> ProviderRateLimitHeaders:
        return ProviderRateLimitHeaders(
            remaining_tokens_minute=int(headers.get("x-ratelimit-remaining-tokens", 0)),
            raw_headers=headers,
        )


@pytest.mark.asyncio
async def test_provider_adapter_generate_contract() -> None:
    """Verify generate contract fulfillment."""
    adapter = MockProviderAdapter()
    req = GenerationRequest(
        model_id="gemini-2.5-flash",
        messages=[ChatMessage(role="user", content="Test prompt")],
    )
    res = await adapter.generate(req)
    assert res.content == "Mock generation output"
    assert res.prompt_tokens == 15
    assert res.completion_tokens == 25
    assert res.finish_reason == "stop"


@pytest.mark.asyncio
async def test_provider_adapter_stream_contract() -> None:
    """Verify stream contract fulfillment."""
    adapter = MockProviderAdapter()
    req = GenerationRequest(
        model_id="gemini-2.5-flash",
        messages=[ChatMessage(role="user", content="Test stream")],
    )
    chunks = []
    async for chunk in adapter.stream(req):
        chunks.append(chunk.delta_content)
    assert "".join(chunks) == "Hello world"


@pytest.mark.asyncio
async def test_provider_adapter_embed_contract() -> None:
    """Verify embed contract fulfillment."""
    adapter = MockProviderAdapter()
    req = EmbeddingRequest(
        model_id="gemini-embedding-2",
        texts=["Sample text"],
    )
    res = await adapter.embed(req)
    assert len(res.embeddings) == 1
    assert res.embeddings[0] == [0.1, 0.2, 0.3]
    assert res.total_tokens == 5


def test_provider_adapter_rate_limit_headers_contract() -> None:
    """Verify rate limit header parsing contract."""
    adapter = MockProviderAdapter()
    headers = adapter.parse_rate_limit_headers({"x-ratelimit-remaining-tokens": "9999"})
    assert headers.remaining_tokens_minute == 9999
