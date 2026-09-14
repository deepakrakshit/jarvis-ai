"""Deterministic unit tests for Model Gateway adapters and failover routing using mocks."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.core.gateway.google_adapter import GoogleGenAIAdapter
from jarvis.core.gateway.groq_adapter import GroqAdapter
from jarvis.core.gateway.interfaces import (
    ChatMessage,
    GenerationRequest,
    GenerationResponse,
)
from jarvis.core.gateway.router import ModelGateway


def test_google_adapter_rate_limit_headers_parsing() -> None:
    """Verify parsing of Google GenAI rate limit headers."""
    adapter = GoogleGenAIAdapter(api_key="fake-test-key")
    headers = {
        "x-ratelimit-remaining-requests": "450",
        "x-ratelimit-remaining-tokens": "245000",
    }
    parsed = adapter.parse_rate_limit_headers(headers)
    assert parsed.remaining_requests_minute == 450
    assert parsed.remaining_tokens_minute == 245000


def test_groq_adapter_rate_limit_headers_parsing() -> None:
    """Verify parsing of Groq Cloud reset durations and counters."""
    adapter = GroqAdapter(api_key="gsk_fake_key_for_testing_1234567890")
    headers = {
        "x-ratelimit-remaining-requests": "28",
        "x-ratelimit-remaining-tokens": "7800",
        "x-ratelimit-reset-requests": "2m30s",
        "x-ratelimit-reset-tokens": "15s",
    }
    parsed = adapter.parse_rate_limit_headers(headers)
    assert parsed.remaining_requests_minute == 28
    assert parsed.remaining_tokens_minute == 7800
    assert parsed.reset_requests_seconds == 150.0
    assert parsed.reset_tokens_seconds == 15.0


@pytest.mark.asyncio
async def test_model_gateway_failover_when_primary_exhausted() -> None:
    """Verify that when primary model fails, the gateway automatically falls back."""
    mock_groq = MagicMock(spec=GroqAdapter)
    # Simulate primary coding specialist throwing Exception
    mock_groq.generate = AsyncMock(side_effect=Exception("Primary rate limited"))

    mock_google = MagicMock(spec=GoogleGenAIAdapter)
    # Simulate fallback model succeeding
    mock_google.generate = AsyncMock(
        return_value=GenerationResponse(
            content="def add(a, b): return a + b",
            prompt_tokens=20,
            completion_tokens=15,
        )
    )

    gateway = ModelGateway(google_adapter=mock_google, groq_adapter=mock_groq)

    req = GenerationRequest(
        model_id="qwen/qwen3.8-27b",  # Primary coding model
        messages=[ChatMessage(role="user", content="Write add function")],
    )

    resp = await gateway.generate(req)
    assert "def add(a, b): return a + b" in resp.content
    assert mock_groq.generate.called
    assert mock_google.generate.called
