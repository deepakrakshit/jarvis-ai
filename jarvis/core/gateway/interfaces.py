"""JARVIS Model Provider Adapter Interfaces.

Defines the strongly typed contracts and abstract interfaces for upstream LLM providers (Google, Groq, OpenRouter).
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """Normalized chat message structure."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class GenerationRequest(BaseModel):
    """Normalized text and tool generation request."""

    model_id: str
    messages: list[ChatMessage]
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    thinking_budget: int | None = Field(default=None, ge=0)
    system_instruction: str | None = None
    tools: list[dict[str, Any]] | None = None
    response_schema: dict[str, Any] | None = None


class GenerationResponse(BaseModel):
    """Normalized model generation response with token metrics and provider headers."""

    content: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    finish_reason: str = "stop"
    provider_raw_headers: dict[str, str] = Field(default_factory=dict)


class StreamChunk(BaseModel):
    """Single incremental delta chunk from a streaming generation request."""

    delta_content: str = ""
    delta_tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str | None = None


class EmbeddingRequest(BaseModel):
    """Normalized text embedding request."""

    model_id: str
    texts: list[str]
    dimension: int | None = None


class EmbeddingResponse(BaseModel):
    """Normalized embedding vector output."""

    embeddings: list[list[float]]
    total_tokens: int = Field(default=0, ge=0)


class ProviderRateLimitHeaders(BaseModel):
    """Structured rate limit metrics observed from provider HTTP headers."""

    remaining_requests_minute: int | None = None
    remaining_tokens_minute: int | None = None
    remaining_requests_day: int | None = None
    reset_requests_seconds: float | None = None
    reset_tokens_seconds: float | None = None
    raw_headers: dict[str, str] = Field(default_factory=dict)


class ModelProviderAdapter(ABC):
    """Abstract interface for LLM provider adapters."""

    @abstractmethod
    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Execute a non-streaming generation request."""
        pass

    @abstractmethod
    def stream(self, request: GenerationRequest) -> AsyncIterator[StreamChunk]:
        """Execute a streaming generation request."""
        pass

    @abstractmethod
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Generate vector embeddings for input texts."""
        pass

    @abstractmethod
    def parse_rate_limit_headers(self, headers: dict[str, str]) -> ProviderRateLimitHeaders:
        """Extract standardized rate limit and reset metrics from provider response headers."""
        pass
