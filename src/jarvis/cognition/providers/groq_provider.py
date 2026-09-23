"""Groq Provider Implementation for JARVIS.

Connects to Groq for ultra-low-latency reasoning models:
GPT-OSS 120B and Qwen 3.8 27B.
"""

import time
from typing import Any, Dict, List, Optional

from groq import AsyncGroq

from jarvis.cognition.providers.base import BaseModelProvider
from jarvis.cognition.quota_manager import quota_manager
from jarvis.config import settings
from jarvis.contracts.model import (
    ModelFamily,
    ModelInvocationRequest,
    ModelInvocationResponse,
    ModelProvider,
)
from jarvis.telemetry import logger


class GroqProvider(BaseModelProvider):
    """Execution provider for Groq-hosted open weights models."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or settings.GROQ_API_KEY
        self._client: Optional[AsyncGroq] = None

    @property
    def provider_type(self) -> ModelProvider:
        return ModelProvider.GROQ

    def _get_client(self) -> AsyncGroq:
        """Lazy initialization of AsyncGroq client."""
        if self._client is None:
            if not self._api_key:
                raise ValueError("GROQ_API_KEY is not configured.")
            self._client = AsyncGroq(api_key=self._api_key)
        return self._client

    def supports(self, model_family: ModelFamily) -> bool:
        """Return True if model family is handled by Groq."""
        return model_family in (
            ModelFamily.GPT_OSS_120B,
            ModelFamily.QWEN_3_8_27B,
        )

    def resolve_model_id(self, model_family: ModelFamily) -> str:
        """Dynamically resolve upstream Groq model ID."""
        if model_family == ModelFamily.GPT_OSS_120B:
            return settings.MODEL_MAP_GPT_OSS_120B
        elif model_family == ModelFamily.QWEN_3_8_27B:
            return settings.MODEL_MAP_QWEN_3_8_27B
        raise ValueError(f"Unsupported model family: {model_family}")

    async def invoke(self, request: ModelInvocationRequest) -> ModelInvocationResponse:
        """Execute request against Groq with quota and header absorption."""
        client = self._get_client()
        model_id = self.resolve_model_id(request.model_family)
        start_time = time.perf_counter()

        messages: List[Dict[str, Any]] = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})
        messages.append({"role": "user", "content": request.prompt})

        # Build tools if provided
        groq_tools: Optional[List[Dict[str, Any]]] = None
        if request.tools:
            groq_tools = [
                {"type": "function", "function": tool_spec} for tool_spec in request.tools
            ]

        await quota_manager.acquire(
            request.model_family, estimated_tokens=request.max_output_tokens // 2
        )

        try:
            logger.info(f"Invoking {request.model_family.value} [{model_id}] via Groq")
            kwargs: Dict[str, Any] = {
                "model": model_id,
                "messages": messages,
                "temperature": request.temperature,
                "max_tokens": request.max_output_tokens,
            }
            if groq_tools:
                kwargs["tools"] = groq_tools
                kwargs["tool_choice"] = "auto"

            completion = await client.chat.completions.create(**kwargs)
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            choice = completion.choices[0]
            msg = choice.message
            text_content = msg.content or ""

            # Extract tool calls
            tool_calls: List[Dict[str, Any]] = []
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append(
                        {
                            "id": tc.id,
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    )

            # Usage metrics
            input_tokens = completion.usage.prompt_tokens if completion.usage else 0
            output_tokens = completion.usage.completion_tokens if completion.usage else 0
            total_tokens = (
                completion.usage.total_tokens
                if completion.usage
                else (input_tokens + output_tokens)
            )

            await quota_manager.release(
                model_family=request.model_family,
                tokens_used=total_tokens,
                latency_ms=duration_ms,
                success=True,
            )

            return ModelInvocationResponse(
                model_family=request.model_family,
                provider=ModelProvider.GROQ,
                text_content=text_content if text_content else None,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                latency_ms=duration_ms,
                finish_reason=choice.finish_reason or "stop",
            )

        except Exception as err:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(f"Groq invocation failed for {request.model_family.value}: {err}")
            await quota_manager.release(
                model_family=request.model_family,
                tokens_used=0,
                latency_ms=duration_ms,
                success=False,
            )
            await quota_manager.record_error(request.model_family, str(err))
            return ModelInvocationResponse(
                model_family=request.model_family,
                provider=ModelProvider.GROQ,
                error=str(err),
                latency_ms=duration_ms,
                finish_reason="ERROR",
            )
