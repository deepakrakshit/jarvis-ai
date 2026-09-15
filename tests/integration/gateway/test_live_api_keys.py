"""Live integration smoke tests using real Google GenAI and Groq API keys from .env."""

import os

import pytest
from dotenv import load_dotenv

from jarvis.core.gateway.google_adapter import GoogleGenAIAdapter
from jarvis.core.gateway.groq_adapter import GroqAdapter
from jarvis.core.gateway.interfaces import (
    ChatMessage,
    EmbeddingRequest,
    GenerationRequest,
)
from jarvis.core.gateway.router import ModelGateway

load_dotenv()

has_gemini_key = bool(
    os.getenv("GEMINI_API_KEY") and not os.getenv("GEMINI_API_KEY", "").startswith("your_")
)
has_groq_key = bool(
    os.getenv("GROQ_API_KEY") and not os.getenv("GROQ_API_KEY", "").startswith("your_")
)


@pytest.mark.skipif(not has_gemini_key, reason="Real GEMINI_API_KEY not provided")
@pytest.mark.asyncio
async def test_live_google_genai_generate_and_stream() -> None:
    """Live call verifying Gemini generation and streaming with actual API key."""
    adapter = GoogleGenAIAdapter()

    from jarvis.core.exceptions import QuotaExceededError

    # 1. Non-streaming generation
    req = GenerationRequest(
        model_id="gemini-2.5-flash",
        messages=[ChatMessage(role="user", content="Reply with the single word 'CONFIRMED'")],
        temperature=0.1,
        max_tokens=20,
    )
    try:
        resp = await adapter.generate(req)
        assert len(resp.content.strip()) > 0
        assert resp.prompt_tokens > 0
    except QuotaExceededError:
        pytest.skip(
            "Google Gemini free-tier quota window reached; skipping non-deterministic rate-limited test"
        )

    # 2. Streaming generation
    try:
        chunks: list[str] = []
        async for chunk in adapter.stream(req):
            chunks.append(chunk.delta_content)
        full_stream_text = "".join(chunks)
        assert len(full_stream_text.strip()) > 0
    except QuotaExceededError:
        pytest.skip("Google Gemini free-tier quota window reached during stream")


@pytest.mark.skipif(not has_gemini_key, reason="Real GEMINI_API_KEY not provided")
@pytest.mark.asyncio
async def test_live_google_genai_gemini_embedding_2() -> None:
    """Live call verifying multimodal vector embedding using gemini-embedding-2."""
    adapter = GoogleGenAIAdapter()

    req = EmbeddingRequest(
        model_id="gemini-embedding-2",
        texts=["JARVIS Personal AI Operating System zero-trust architecture"],
    )
    resp = await adapter.embed(req)
    assert len(resp.embeddings) == 1
    # Gemini Embedding 2 produces 3072-dimensional vectors by default
    assert len(resp.embeddings[0]) == 3072


@pytest.mark.skipif(not has_groq_key, reason="Real GROQ_API_KEY not provided")
@pytest.mark.asyncio
async def test_live_groq_speed_specialist_generation() -> None:
    """Live call verifying Groq inference with Qwen 3.8-27B speed specialist."""
    adapter = GroqAdapter()

    req = GenerationRequest(
        model_id="qwen/qwen3.8-27b",
        messages=[
            ChatMessage(role="user", content="Write a 1-line python lambda that squares a number.")
        ],
        temperature=0.1,
        max_tokens=50,
    )
    resp = await adapter.generate(req)
    assert len(resp.content.strip()) > 0
    assert "lambda" in resp.content.lower()
    assert resp.prompt_tokens > 0


@pytest.mark.skipif(not (has_gemini_key and has_groq_key), reason="Both real keys required")
@pytest.mark.asyncio
async def test_live_model_gateway_end_to_end_dispatch() -> None:
    """Live end-to-end dispatch through ModelGateway coordinating adapters and quota leases."""
    gateway = ModelGateway()

    # Call Groq Qwen model through gateway
    req_groq = GenerationRequest(
        model_id="qwen/qwen3.8-27b",
        messages=[ChatMessage(role="user", content="Say 'ONLINE'")],
        max_tokens=10,
    )
    resp_groq = await gateway.generate(req_groq)
    assert len(resp_groq.content.strip()) > 0

    # Call Google Gemini model through gateway
    req_gemini = GenerationRequest(
        model_id="gemini-2.5-flash",
        messages=[ChatMessage(role="user", content="Say 'ONLINE'")],
        max_tokens=10,
    )
    resp_gemini = await gateway.generate(req_gemini)
    assert len(resp_gemini.content.strip()) > 0
