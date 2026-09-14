"""JARVIS Model Gateway and Capability Router.

Directs tasks to appropriate specialist models according to MODEL_ROSTER.md.
Enforces two-phase quota reservation leases, failover ladders, and telemetry metrics.
"""

from collections.abc import AsyncIterator

from jarvis.core.exceptions import ModelProviderError, QuotaExceededError
from jarvis.core.gateway.google_adapter import GoogleGenAIAdapter
from jarvis.core.gateway.groq_adapter import GroqAdapter
from jarvis.core.gateway.interfaces import (
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    GenerationResponse,
    ModelProviderAdapter,
    StreamChunk,
)
from jarvis.core.gateway.quota import ModelQuotaManager, QuotaDomain
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

# Fallback chains per model role (derived from MODEL_ROSTER.md)
FALLBACK_CHAINS: dict[str, list[str]] = {
    # Workhorse Quality: Flash -> Flash-Lite -> Groq
    "gemini-2.5-flash": ["gemini-2.5-flash-lite", "gemini-3.1-flash-lite", "qwen/qwen3.8-27b"],
    "gemini-3.6-flash": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.1-flash-lite"],
    "gemini-3.7-flash": ["gemini-3.6-flash", "gemini-2.5-flash"],
    "gemini-3.8-flash": ["gemini-3.7-flash", "gemini-2.5-flash"],
    # Coding specialist: Groq speed -> Gemini Flash-Lite / Flash
    "qwen/qwen3.8-27b": ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-3.1-flash-lite"],
    # Research specialist: Groq -> Gemini Flash-Lite / Flash
    "openai/gpt-oss-120b": ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-3.1-flash-lite"],
    # Router / Classifier: Flash-Lite 3.1 -> 2.5-flash-lite -> 2.5-flash
    "gemini-3.1-flash-lite": ["gemini-2.5-flash-lite", "gemini-2.5-flash"],
    # Default everyday brain: Flash-Lite 3.5 -> 2.5-flash-lite -> 2.5-flash
    "gemini-3.5-flash-lite": ["gemini-2.5-flash-lite", "gemini-2.5-flash"],
    "gemini-2.5-flash-lite": ["gemini-3.1-flash-lite", "gemini-2.5-flash"],
}


class ModelGateway:
    """Central gateway coordinating model dispatch, quota leases, and failovers."""

    def __init__(
        self,
        google_adapter: GoogleGenAIAdapter | None = None,
        groq_adapter: GroqAdapter | None = None,
    ) -> None:
        self.google_adapter = google_adapter
        self.groq_adapter = groq_adapter

        self._quota_managers: dict[str, ModelQuotaManager] = {}

    def get_quota_manager(self, model_id: str) -> ModelQuotaManager:
        """Get or lazily initialize ModelQuotaManager for a specific model."""
        if model_id not in self._quota_managers:
            # Configure limits according to MODEL_ROSTER.md specifications
            rpm = 30 if "qwen" in model_id or "gpt-oss" in model_id else 15
            rpd = 1000 if "qwen" in model_id or "gpt-oss" in model_id else 500
            tpm = 8000 if "qwen" in model_id or "gpt-oss" in model_id else 250000

            self._quota_managers[model_id] = ModelQuotaManager(
                model_id=model_id,
                rpm_limit=rpm,
                rpd_limit=rpd,
                tpm_limit=tpm,
            )
        return self._quota_managers[model_id]

    def _get_adapter_for_model(self, model_id: str) -> ModelProviderAdapter:
        """Resolve adapter instance for model ID."""
        if "qwen" in model_id or "gpt-oss" in model_id or "groq" in model_id:
            if not self.groq_adapter:
                self.groq_adapter = GroqAdapter()
            return self.groq_adapter

        if not self.google_adapter:
            self.google_adapter = GoogleGenAIAdapter()
        return self.google_adapter

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Execute generation with quota lease reservation and automatic fallback ladder."""
        candidate_models = [request.model_id]
        if request.model_id in FALLBACK_CHAINS:
            candidate_models.extend(FALLBACK_CHAINS[request.model_id])

        last_error: Exception | None = None

        for model in candidate_models:
            manager = self.get_quota_manager(model)
            try:
                # 1. Tentative reservation lease
                lease = await manager.reserve_lease(
                    estimated_tokens=request.max_tokens or 150,
                    domain=QuotaDomain.GENERATION,
                )
            except QuotaExceededError as q_err:
                logger.warning("model_skipped_quota", model=model, error=str(q_err))
                last_error = q_err
                continue

            # 2. Dispatch to adapter
            adapter = self._get_adapter_for_model(model)
            model_req = request.model_copy(update={"model_id": model})

            try:
                response = await adapter.generate(model_req)
                # 3. Reconcile lease
                actual_tokens = response.prompt_tokens + response.completion_tokens
                await manager.reconcile_lease(lease.lease_id, actual_tokens)

                if response.provider_raw_headers:
                    parsed_headers = adapter.parse_rate_limit_headers(response.provider_raw_headers)
                    await manager.update_from_headers(parsed_headers)

                return response

            except Exception as call_err:
                logger.error(
                    "model_call_failed_initiating_failover", model=model, error=str(call_err)
                )
                await manager.fail_lease(lease.lease_id, reason=str(call_err))
                last_error = call_err
                # Loop continues to next fallback model

        raise ModelProviderError(
            f"All candidate models exhausted in failover chain. Last error: {last_error}"
        )

    async def stream(self, request: GenerationRequest) -> AsyncIterator[StreamChunk]:
        """Stream generation output directly from appropriate adapter."""
        adapter = self._get_adapter_for_model(request.model_id)
        async for chunk in adapter.stream(request):
            yield chunk

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Execute embedding request via Google GenAI adapter."""
        if not self.google_adapter:
            self.google_adapter = GoogleGenAIAdapter()
        return await self.google_adapter.embed(request)
