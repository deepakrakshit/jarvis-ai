"""Google GenAI Provider Adapter.

Implements ModelProviderAdapter using the official google-genai SDK, supporting
Gemini 3.x/2.5 models, streaming, multimodal embeddings, and rate limit parsing.
"""

import os
from collections.abc import AsyncIterator
from typing import Any, cast

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

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


class GoogleGenAIAdapter(ModelProviderAdapter):
    """Adapter for Google Gemini models via google-genai SDK."""

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key and settings.GEMINI_API_KEY:
            key = settings.GEMINI_API_KEY.get_secret_value()
        if not key:
            raise ModelProviderError("GEMINI_API_KEY is not configured in environment or settings.")
        self._client = genai.Client(api_key=key)

    def _normalize_model_name(self, model_id: str) -> str:
        """Strip 'models/' prefix if present for uniform SDK invocation."""
        if model_id.startswith("models/"):
            return model_id[7:]
        return model_id

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Execute non-streaming generation via Google GenAI SDK."""
        model_name = self._normalize_model_name(request.model_id)

        # Build contents from messages
        contents: list[Any] = []
        for msg in request.messages:
            contents.append(msg.content)

        config_dict: dict[str, Any] = {
            "temperature": request.temperature,
            "automatic_function_calling": genai_types.AutomaticFunctionCallingConfig(disable=True),
        }
        if request.max_tokens:
            config_dict["max_output_tokens"] = request.max_tokens
        if request.system_instruction:
            config_dict["system_instruction"] = request.system_instruction
        if request.response_schema:
            config_dict["response_mime_type"] = "application/json"
            config_dict["response_schema"] = request.response_schema
        if request.thinking_budget is not None:
            config_dict["thinking_config"] = genai_types.ThinkingConfig(
                thinking_budget=request.thinking_budget
            )
        elif request.max_tokens is not None and request.max_tokens <= 100:
            config_dict["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=0)

        config = genai_types.GenerateContentConfig(**config_dict)

        try:
            response = await self._client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )
        except genai_errors.APIError as exc:
            if exc.code == 429 or "RESOURCE_EXHAUSTED" in str(exc):
                logger.error("google_quota_exceeded", model=model_name, error=str(exc))
                raise QuotaExceededError(f"Google Gemini quota exceeded: {exc.message}") from exc
            logger.error("google_api_error", model=model_name, code=exc.code, error=str(exc))
            raise ModelProviderError(f"Google GenAI API error ({exc.code}): {exc.message}") from exc
        except Exception as exc:
            logger.error("google_generation_failed", model=model_name, error=str(exc))
            raise ModelProviderError(f"Google GenAI generation failed: {exc}") from exc

        # Extract usage metrics
        prompt_tokens = 0
        completion_tokens = 0
        if response.usage_metadata:
            prompt_tokens = response.usage_metadata.prompt_token_count or 0
            completion_tokens = response.usage_metadata.candidates_token_count or 0

        # Extract text content
        content_text = response.text or ""

        # Extract finish reason
        finish_reason = "stop"
        if response.candidates and len(response.candidates) > 0:
            cand = response.candidates[0]
            if cand.finish_reason:
                finish_reason = str(cand.finish_reason).split(".")[-1].lower()

        # Extract function call / tool calls if any
        tool_calls: list[dict[str, Any]] = []
        if response.function_calls:
            for fc in response.function_calls:
                tool_calls.append(
                    {
                        "id": getattr(fc, "id", None) or f"call_{fc.name}",
                        "type": "function",
                        "function": {
                            "name": fc.name,
                            "arguments": fc.args or {},
                        },
                    }
                )

        return GenerationResponse(
            content=content_text,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish_reason,
        )

    async def stream(self, request: GenerationRequest) -> AsyncIterator[StreamChunk]:
        """Execute streaming generation via Google GenAI SDK."""
        model_name = self._normalize_model_name(request.model_id)
        contents = [msg.content for msg in request.messages]

        config_dict: dict[str, Any] = {
            "temperature": request.temperature,
            "automatic_function_calling": genai_types.AutomaticFunctionCallingConfig(disable=True),
        }
        if request.max_tokens:
            config_dict["max_output_tokens"] = request.max_tokens
        if request.system_instruction:
            config_dict["system_instruction"] = request.system_instruction
        if request.thinking_budget is not None:
            config_dict["thinking_config"] = genai_types.ThinkingConfig(
                thinking_budget=request.thinking_budget
            )
        elif request.max_tokens is not None and request.max_tokens <= 100:
            config_dict["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=0)

        config = genai_types.GenerateContentConfig(**config_dict)

        try:
            stream_resp = await self._client.aio.models.generate_content_stream(
                model=model_name,
                contents=cast("Any", contents),
                config=config,
            )
            async for chunk in stream_resp:
                text_chunk = chunk.text or ""
                yield StreamChunk(delta_content=text_chunk)
        except genai_errors.APIError as exc:
            if exc.code == 429:
                raise QuotaExceededError(
                    f"Google Gemini streaming quota exceeded: {exc.message}"
                ) from exc
            raise ModelProviderError(
                f"Google GenAI streaming error ({exc.code}): {exc.message}"
            ) from exc
        except Exception as exc:
            raise ModelProviderError(f"Google GenAI streaming failed: {exc}") from exc

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Execute vector embedding request via Google GenAI SDK."""
        model_name = self._normalize_model_name(request.model_id)

        try:
            resp = await self._client.aio.models.embed_content(
                model=model_name,
                contents=cast("Any", request.texts),
            )
        except genai_errors.APIError as exc:
            if exc.code == 429:
                raise QuotaExceededError(
                    f"Google Gemini embedding quota exceeded: {exc.message}"
                ) from exc
            raise ModelProviderError(
                f"Google GenAI embedding error ({exc.code}): {exc.message}"
            ) from exc
        except Exception as exc:
            raise ModelProviderError(f"Google GenAI embedding failed: {exc}") from exc

        embeddings: list[list[float]] = []
        if resp.embeddings:
            for emb in resp.embeddings:
                if emb.values:
                    embeddings.append(list(emb.values))

        return EmbeddingResponse(
            embeddings=embeddings,
            total_tokens=0,
        )

    def parse_rate_limit_headers(self, headers: dict[str, str]) -> ProviderRateLimitHeaders:
        """Extract Google rate limit headers if present."""
        lower_headers = {k.lower(): v for k, v in headers.items()}
        rem_req = lower_headers.get("x-ratelimit-remaining-requests")
        rem_tok = lower_headers.get("x-ratelimit-remaining-tokens")

        return ProviderRateLimitHeaders(
            remaining_requests_minute=int(rem_req) if rem_req and rem_req.isdigit() else None,
            remaining_tokens_minute=int(rem_tok) if rem_tok and rem_tok.isdigit() else None,
            raw_headers=headers,
        )
