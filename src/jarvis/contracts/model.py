"""Canonical Model & Quota Contracts for JARVIS Model Router.

Enforces the strict 6-model runtime allowlist and quota state tracking.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


class ModelFamily(str, Enum):
    """The strict six approved JARVIS runtime model families."""

    GEMINI_3_8_LIVE = "Gemini 3.8 Live"
    GPT_OSS_120B = "GPT-OSS 120B"
    QWEN_3_8_27B = "Qwen 3.8 27B"
    GEMINI_3_1_FLASH_LITE = "Gemini 3.1 Flash-Lite"
    GEMINI_3_5_FLASH_LITE = "Gemini 3.5 Flash-Lite"
    GEMMA_4_31B = "Gemma 4 31B"


class ModelProvider(str, Enum):
    """Underlying infrastructure provider."""

    GOOGLE_GENAI = "google_genai"
    GROQ = "groq"


class ModelRole(str, Enum):
    """Primary operational capability role."""

    REALTIME_VOICE = "REALTIME_VOICE"
    DEEP_REASONING = "DEEP_REASONING"
    MULTIMODAL_VISION = "MULTIMODAL_VISION"
    LIGHTWEIGHT_WORKER = "LIGHTWEIGHT_WORKER"
    HIGH_VOLUME_AGENTIC = "HIGH_VOLUME_AGENTIC"
    MEDIA_ANALYSIS = "MEDIA_ANALYSIS"


class ModelQuota(BaseModel):
    """Dynamic quota and rate-limit tracking for a model family."""

    model_family: ModelFamily
    provider: ModelProvider

    max_rpm: int = 30
    max_tpm: int = 8000
    max_rpd: int = 1000

    current_rpm_used: int = 0
    current_tpm_used: int = 0
    current_rpd_used: int = 0

    remaining_requests: int = 1000
    remaining_tokens: int = 8000
    reset_epoch_seconds: float = 0.0

    current_concurrency: int = 0
    max_concurrency: int = 5

    provider_healthy: bool = True
    recent_latency_ms: float = 0.0
    failure_rate_percent: float = 0.0
    last_checked_at: datetime = Field(default_factory=utc_now)


class ModelInvocationRequest(BaseModel):
    """Normalized input request dispatched to any approved model provider."""

    model_family: ModelFamily
    system_instruction: Optional[str] = None
    prompt: str
    temperature: float = 0.7
    max_output_tokens: int = 4096
    tools: List[Dict[str, Any]] = Field(default_factory=list)
    multimodal_media: List[Dict[str, Any]] = Field(default_factory=list)
    stream: bool = False
    task_id: Optional[str] = None
    session_id: Optional[str] = None


class ModelInvocationResponse(BaseModel):
    """Normalized response returned from model execution."""

    model_family: ModelFamily
    provider: ModelProvider
    text_content: Optional[str] = None
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0

    finish_reason: Optional[str] = None
    error: Optional[str] = None
