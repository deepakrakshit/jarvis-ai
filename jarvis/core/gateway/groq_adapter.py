"""Groq Cloud Provider Adapter.

Implements ModelProviderAdapter using the official Groq SDK, supporting
speed specialist models (Qwen 3.8-27B, GPT-OSS 120B), duration-based reset parsing,
and fail-closed rate limit header processing.
"""

import os
import re
from collections.abc import AsyncIterator
from typing import Any

from groq import APIError as GroqAPIError
from groq import AsyncGroq, RateLimitError

from jarvis.core.config import get_settings
from jarvis.core.exceptions import ModelProviderError, QuotaExceededError
from jarvis.core.gateway.interfaces import (
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    GenerationResponse,
    ModelProviderAdapter,
    ProviderRateLimitHeaders,
    StreamChunk,
)
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


def parse_groq_reset_duration(duration_str: str) -> float:
    """Parse Groq reset duration strings (e.g., '15s', '2m30s', '1h12m', '500ms') to seconds.

    Fails closed on malformed or unrecognized duration formats by raising ValueError.
    """
    if not duration_str or not duration_str.strip():
        raise ValueError("Empty Groq rate limit reset duration header.")

    cleaned = duration_str.strip().lower()

    # If it's a bare integer or float of seconds
    if cleaned.replace(".", "", 1).isdigit():
        return float(cleaned)

    # Must strictly match sequence of unit patterns: e.g. 1h, 2m, 30s, 250ms
    if not re.fullmatch(r"(?:(\d+(?:\.\d+)?)(ms|h|m|s))+", cleaned):
        raise ValueError(f"Malformed Groq reset duration: '{duration_str}'")

    unit_multipliers = {
        "h": 3600.0,
        "m": 60.0,
        "s": 1.0,
        "ms": 0.001,
    }

    matches = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", cleaned)
    if not matches:
        raise ValueError(f"Failed to parse time units from duration: '{duration_str}'")

    total_seconds = 0.0
    for val_str, unit in matches:
        total_seconds += float(val_str) * unit_multipliers[unit]

    return total_seconds


class GroqAdapter(ModelProviderAdapter):
    """Adapter for Groq Cloud fast-inference models."""

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        key = api_key or os.getenv("GROQ_API_KEY")
        if not key and settings.GROQ_API_KEY:
            key = settings.GROQ_API_KEY.get_secret_value()
        if not key:
            raise ModelProviderError("GROQ_API_KEY is not configured in environment or settings.")
        self._client = AsyncGroq(api_key=key)

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Execute non-streaming generation via Groq Cloud SDK."""
        messages: list[dict[str, Any]] = []

        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})

        for msg in request.messages:
            messages.append({"role": msg.role, "content": msg.content})

        kwargs: dict[str, Any] = {
            "model": request.model_id,
            "messages": messages,
            "temperature": request.temperature,
        }
        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens

        try:
            raw_response = await self._client.chat.completions.with_raw_response.create(**kwargs)
            completion = await raw_response.parse()
            headers = dict(raw_response.headers)
        except RateLimitError as exc:
            logger.error("groq_quota_exceeded", model=request.model_id, error=str(exc))
            raise QuotaExceededError(f"Groq Cloud rate limit exceeded: {exc.message}") from exc
        except GroqAPIError as exc:
            status_code = getattr(exc, "status_code", 500)
            logger.error("groq_api_error", model=request.model_id, code=status_code, error=str(exc))
            raise ModelProviderError(f"Groq API error ({status_code}): {exc.message}") from exc
        except Exception as exc:
            logger.error("groq_generation_failed", model=request.model_id, error=str(exc))
            raise ModelProviderError(f"Groq generation failed: {exc}") from exc

        content = completion.choices[0].message.content or ""
        prompt_tokens = completion.usage.prompt_tokens if completion.usage else 0
        completion_tokens = completion.usage.completion_tokens if completion.usage else 0
        finish_reason = completion.choices[0].finish_reason or "stop"

        # Check tool calls
        tool_calls: list[dict[str, Any]] = []
        if completion.choices[0].message.tool_calls:
            for tc in completion.choices[0].message.tool_calls:
                tool_calls.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                )

        return GenerationResponse(
            content=content,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish_reason,
            provider_raw_headers=headers,
        )

    async def stream(self, request: GenerationRequest) -> AsyncIterator[StreamChunk]:
        """Execute streaming generation via Groq Cloud SDK."""
        messages: list[dict[str, Any]] = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})

        for msg in request.messages:
            messages.append({"role": msg.role, "content": msg.content})

        kwargs: dict[str, Any] = {
            "model": request.model_id,
            "messages": messages,
            "temperature": request.temperature,
            "stream": True,
        }
        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens

        try:
            stream_resp = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream_resp:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield StreamChunk(delta_content=chunk.choices[0].delta.content)
        except RateLimitError as exc:
            raise QuotaExceededError(f"Groq streaming rate limit exceeded: {exc.message}") from exc
        except Exception as exc:
            raise ModelProviderError(f"Groq streaming failed: {exc}") from exc

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Groq does not provide text embedding models."""
        raise ModelProviderError("Groq Cloud does not host text embedding models.")

    def parse_rate_limit_headers(self, headers: dict[str, str]) -> ProviderRateLimitHeaders:
        """Extract Groq RPD, TPM, and duration reset metrics from HTTP headers.

        Groq standard headers:
        - x-ratelimit-remaining-requests
        - x-ratelimit-remaining-tokens
        - x-ratelimit-reset-requests (RPD reset duration string, e.g. '2m30s')
        - x-ratelimit-reset-tokens (TPM reset duration string, e.g. '15s')
        """
        lower = {k.lower(): v for k, v in headers.items()}

        rem_req = lower.get("x-ratelimit-remaining-requests")
        rem_tok = lower.get("x-ratelimit-remaining-tokens")
        reset_req = lower.get("x-ratelimit-reset-requests")
        reset_tok = lower.get("x-ratelimit-reset-tokens")

        reset_req_seconds: float | None = None
        if reset_req:
            try:
                reset_req_seconds = parse_groq_reset_duration(reset_req)
            except ValueError as exc:
                logger.warning("groq_reset_requests_parse_failed", error=str(exc), raw=reset_req)
                # Fail closed on malformed reset headers: assume conservative 60.0s backoff
                reset_req_seconds = 60.0

        reset_tok_seconds: float | None = None
        if reset_tok:
            try:
                reset_tok_seconds = parse_groq_reset_duration(reset_tok)
            except ValueError as exc:
                logger.warning("groq_reset_tokens_parse_failed", error=str(exc), raw=reset_tok)
                reset_tok_seconds = 60.0

        return ProviderRateLimitHeaders(
            remaining_requests_minute=int(rem_req) if rem_req and rem_req.isdigit() else None,
            remaining_tokens_minute=int(rem_tok) if rem_tok and rem_tok.isdigit() else None,
            reset_requests_seconds=reset_req_seconds,
            reset_tokens_seconds=reset_tok_seconds,
            raw_headers=headers,
        )
