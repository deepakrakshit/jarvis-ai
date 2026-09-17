"""JARVIS Model Gateway and Provider Adapters Subsystem."""

from jarvis.core.gateway.google_adapter import GoogleGenAIAdapter
from jarvis.core.gateway.google_realtime import GoogleRealtimeAdapter
from jarvis.core.gateway.groq_adapter import GroqAdapter, parse_groq_reset_duration
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
from jarvis.core.gateway.mock_realtime import MockRealtimeAdapter
from jarvis.core.gateway.quota import (
    LeaseState,
    ModelQuotaManager,
    QuotaDomain,
    QuotaLease,
    QuotaMeterType,
)
from jarvis.core.gateway.realtime import (
    LiveAudioChunk,
    LiveEvent,
    LiveEventType,
    LiveGoAway,
    LiveInteractionStatus,
    LiveResumptionUpdate,
    LiveSessionConfig,
    LiveToolCall,
    LiveToolResponse,
    LiveTranscription,
    RealtimeModelAdapter,
)
from jarvis.core.gateway.router import (
    FALLBACK_CHAINS,
    POOL_A_FLASH_QUALITY,
    POOL_B_FLASH_LITE,
    POOL_C_COMPRESSION,
    POOL_D_GROQ_SPECIALISTS,
    POOL_E_REALTIME_VOICE,
    ModelGateway,
)

__all__ = [
    "FALLBACK_CHAINS",
    "POOL_A_FLASH_QUALITY",
    "POOL_B_FLASH_LITE",
    "POOL_C_COMPRESSION",
    "POOL_D_GROQ_SPECIALISTS",
    "POOL_E_REALTIME_VOICE",
    "ChatMessage",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "GenerationRequest",
    "GenerationResponse",
    "GoogleGenAIAdapter",
    "GoogleRealtimeAdapter",
    "GroqAdapter",
    "LeaseState",
    "LiveAudioChunk",
    "LiveEvent",
    "LiveEventType",
    "LiveGoAway",
    "LiveInteractionStatus",
    "LiveResumptionUpdate",
    "LiveSessionConfig",
    "LiveToolCall",
    "LiveToolResponse",
    "LiveTranscription",
    "MockRealtimeAdapter",
    "ModelGateway",
    "ModelProviderAdapter",
    "ModelQuotaManager",
    "ProviderRateLimitHeaders",
    "QuotaDomain",
    "QuotaLease",
    "QuotaMeterType",
    "RealtimeModelAdapter",
    "StreamChunk",
    "parse_groq_reset_duration",
]
