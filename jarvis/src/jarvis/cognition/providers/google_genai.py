"""Google GenAI Provider Implementation for JARVIS.

Connects to Google GenAI for Gemini 3.1 Flash-Lite, Gemini 3.5 Flash-Lite,
Gemma 4 31B, and non-streaming Gemini Live fallbacks.
"""

import time
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

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


class GoogleGenAIProvider(BaseModelProvider):
    """Execution provider for Google GenAI models."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._client: Optional[genai.Client] = None

    @property
    def provider_type(self) -> ModelProvider:
        return ModelProvider.GOOGLE_GENAI

    def _get_client(self) -> genai.Client:
        """Lazy initialization of GenAI Client."""
        if self._client is None:
            if not self._api_key:
                raise ValueError("GEMINI_API_KEY is not configured.")
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def supports(self, model_family: ModelFamily) -> bool:
        """Return True if model family belongs to Google GenAI."""
        return model_family in (
            ModelFamily.GEMINI_3_8_LIVE,
            ModelFamily.GEMINI_3_1_FLASH_LITE,
            ModelFamily.GEMINI_3_5_FLASH_LITE,
            ModelFamily.GEMMA_4_31B,
        )

    def resolve_model_id(self, model_family: ModelFamily) -> str:
        """Dynamically resolve upstream model ID from settings."""
        if model_family == ModelFamily.GEMINI_3_1_FLASH_LITE:
            return settings.MODEL_MAP_GEMINI_3_1_FLASH_LITE
        elif model_family == ModelFamily.GEMINI_3_5_FLASH_LITE:
            return settings.MODEL_MAP_GEMINI_3_5_FLASH_LITE
        elif model_family == ModelFamily.GEMMA_4_31B:
            return settings.MODEL_MAP_GEMMA_4_31B
        elif model_family == ModelFamily.GEMINI_3_8_LIVE:
            return settings.MODEL_MAP_GEMINI_LIVE
        raise ValueError(f"Unsupported model family: {model_family}")

    async def invoke(self, request: ModelInvocationRequest) -> ModelInvocationResponse:
        """Execute request against Google GenAI with quota tracking."""
        client = self._get_client()
        model_id = self.resolve_model_id(request.model_family)
        start_time = time.perf_counter()

        # Build tools if provided
        genai_tools: Optional[List[types.Tool]] = None
        if request.tools:
            func_decls: List[types.FunctionDeclaration] = []
            for tool_spec in request.tools:
                decl = types.FunctionDeclaration(
                    name=tool_spec.get("name", ""),
                    description=tool_spec.get("description", ""),
                    parameters=tool_spec.get("parameters"),
                )
                func_decls.append(decl)
            if func_decls:
                genai_tools = [types.Tool(function_declarations=func_decls)]

        config = types.GenerateContentConfig(
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            system_instruction=request.system_instruction,
            tools=genai_tools,
        )

        await quota_manager.acquire(
            request.model_family, estimated_tokens=request.max_output_tokens // 2
        )

        try:
            logger.info(f"Invoking {request.model_family.value} [{model_id}] via Google GenAI")
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=request.prompt,
                config=config,
            )
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            # Extract usage metrics
            input_tokens = 0
            output_tokens = 0
            total_tokens = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                input_tokens = response.usage_metadata.prompt_token_count or 0
                output_tokens = response.usage_metadata.candidates_token_count or 0
                total_tokens = response.usage_metadata.total_token_count or (
                    input_tokens + output_tokens
                )

            # Extract function calls
            tool_calls: List[Dict[str, Any]] = []
            if hasattr(response, "function_calls") and response.function_calls:
                for fc in response.function_calls:
                    tool_calls.append(
                        {
                            "name": fc.name,
                            "arguments": dict(fc.args) if fc.args else {},
                        }
                    )

            await quota_manager.release(
                model_family=request.model_family,
                tokens_used=total_tokens,
                latency_ms=duration_ms,
                success=True,
            )

            return ModelInvocationResponse(
                model_family=request.model_family,
                provider=ModelProvider.GOOGLE_GENAI,
                text_content=response.text if hasattr(response, "text") else None,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                latency_ms=duration_ms,
                finish_reason="STOP",
            )

        except Exception as err:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(f"Google GenAI invocation failed for {request.model_family.value}: {err}")
            await quota_manager.release(
                model_family=request.model_family,
                tokens_used=0,
                latency_ms=duration_ms,
                success=False,
            )
            await quota_manager.record_error(request.model_family, str(err))
            return ModelInvocationResponse(
                model_family=request.model_family,
                provider=ModelProvider.GOOGLE_GENAI,
                error=str(err),
                latency_ms=duration_ms,
                finish_reason="ERROR",
            )
