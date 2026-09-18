"""Dynamic Model Selection and Discovery for Governed Deep Agents.

Discovers and selects candidate models from the existing JARVIS model roster
(MODEL_ROSTER.md and jarvis.core.gateway.router) without hardcoding fixed models.
Enforces tool-calling capability verification and fails closed if credentials
or capabilities are unavailable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel

from jarvis.core.config import Settings, get_settings
from jarvis.core.exceptions import SandboxBackendUnavailableError
from jarvis.core.gateway.router import (
    POOL_B_FLASH_LITE,
    POOL_C_COMPRESSION,
)
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

# Known models supporting function / tool calling
TOOL_CAPABLE_MODELS: frozenset[str] = frozenset(
    {
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-3.5-flash",
        "gemini-3.8-flash",
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
        "gemma-4-31b-it",
    }
)


@dataclass(frozen=True)
class SelectedModelMetadata:
    """Metadata describing a dynamically resolved model candidate."""

    provider: str
    model_id: str
    source: str
    supports_tools: bool
    chat_model: BaseChatModel


def discover_model_candidates(settings: Settings | None = None) -> list[tuple[str, str, str]]:
    """Build an ordered list of candidate (provider, model_id, source) triples from the existing roster.

    Adheres to the preferred candidate priority:
    1. Environment override (JARVIS_DEEP_AGENTS_MODEL / GEMINI_MODEL)
    2. High-throughput Flash-Lite pool (POOL_B_FLASH_LITE: gemini-3.5-flash-lite, gemini-3.1-flash-lite)
    3. Compression / autonomous pool (POOL_C_COMPRESSION: gemma-4-31b-it)
    4. Primary configured model from settings
    """
    settings = settings or get_settings()
    candidates: list[tuple[str, str, str]] = []

    # 1. Direct explicit environment overrides
    env_override = os.getenv("JARVIS_DEEP_AGENTS_MODEL") or os.getenv("GEMINI_MODEL")
    if env_override:
        candidates.append(
            (
                "Google Gemini",
                env_override,
                "environment override (JARVIS_DEEP_AGENTS_MODEL/GEMINI_MODEL)",
            )
        )

    # 2. Preferred high-volume Flash-Lite candidates (POOL_B_FLASH_LITE)
    for model_id in POOL_B_FLASH_LITE:
        candidates.append(
            (
                "Google Gemini",
                model_id,
                f"existing JARVIS model roster (POOL_B_FLASH_LITE: {model_id})",
            )
        )

    # 3. Autonomous compression candidates (POOL_C_COMPRESSION)
    for model_id in POOL_C_COMPRESSION:
        candidates.append(
            (
                "Google Gemini",
                model_id,
                f"existing JARVIS model roster (POOL_C_COMPRESSION: {model_id})",
            )
        )

    # 4. Configured primary model
    if hasattr(settings, "PRIMARY_MODEL_ID") and settings.PRIMARY_MODEL_ID:
        candidates.append(
            (
                "Google Gemini",
                settings.PRIMARY_MODEL_ID,
                f"existing JARVIS configuration (PRIMARY_MODEL_ID: {settings.PRIMARY_MODEL_ID})",
            )
        )

    return candidates


def resolve_governed_agent_model(
    model: str | BaseChatModel | None = None,
    settings: Settings | None = None,
) -> SelectedModelMetadata:
    """Dynamically resolve, verify, and instantiate an authorized model for Deep Agents.

    Invariants:
    1. Never None: Upstream Deep Agents defaults to Anthropic Claude when model=None.
       This resolver ensures an explicit BaseChatModel is always selected and returned.
    2. Dynamic Discovery: Discovers candidates from the existing model roster rather
       than hardcoding a single fixed model.
    3. Tool-Capability Verification: Verifies candidate supports bind_tools/function calling.
    4. Zero Dependency on OpenAI/Anthropic: Never checks or requires OPENAI_API_KEY
       or ANTHROPIC_API_KEY.
    """
    settings = settings or get_settings()

    # Case A: Caller supplied an instantiated BaseChatModel
    if isinstance(model, BaseChatModel):
        model_name = getattr(model, "model_name", None) or getattr(
            model, "model", "custom_chat_model"
        )
        supports_tools = hasattr(model, "bind_tools")
        return SelectedModelMetadata(
            provider="Custom / Caller-Provided",
            model_id=str(model_name),
            source="caller-supplied BaseChatModel instance",
            supports_tools=supports_tools,
            chat_model=model,
        )

    # Case B: Caller supplied a specific model identifier string
    if isinstance(model, str) and model.strip():
        chosen_id = model.strip()
        chat_model = _instantiate_google_model(chosen_id, settings)
        supports_tools = hasattr(chat_model, "bind_tools")
        return SelectedModelMetadata(
            provider="Google Gemini",
            model_id=chosen_id,
            source=f"caller-specified model string ('{chosen_id}')",
            supports_tools=supports_tools,
            chat_model=chat_model,
        )

    # Case C: Dynamic candidate discovery across the configured roster
    candidates = discover_model_candidates(settings)

    # Verify Google Gemini API credentials exist
    gemini_key: str | None = None
    if settings.GEMINI_API_KEY:
        gemini_key = settings.GEMINI_API_KEY.get_secret_value()
    if not gemini_key:
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if not gemini_key:
        raise SandboxBackendUnavailableError(
            "No valid model provider credentials configured in settings/.env. "
            "Google Gemini credentials required."
        )

    # Discover the first candidate that supports tool-calling
    for provider, model_id, source in candidates:
        if model_id not in TOOL_CAPABLE_MODELS and "flash" not in model_id.lower():
            logger.debug("candidate_skipped_lacks_tool_capability", model=model_id)
            continue

        try:
            chat_model = _instantiate_google_model(model_id, settings, gemini_key=gemini_key)
            if not hasattr(chat_model, "bind_tools"):
                continue

            logger.info(
                "governed_model_candidate_selected",
                provider=provider,
                model_id=model_id,
                source=source,
            )
            return SelectedModelMetadata(
                provider=provider,
                model_id=model_id,
                source=source,
                supports_tools=True,
                chat_model=chat_model,
            )
        except Exception as err:
            logger.warning(
                "candidate_instantiation_failed",
                model=model_id,
                error=str(err),
            )
            continue

    raise SandboxBackendUnavailableError(
        "Failed to resolve any tool-capable candidate model from the configured roster."
    )


def _instantiate_google_model(
    model_id: str,
    settings: Settings,
    gemini_key: str | None = None,
) -> BaseChatModel:
    """Instantiate a ChatGoogleGenerativeAI instance for a candidate model."""
    if not gemini_key:
        if settings.GEMINI_API_KEY:
            gemini_key = settings.GEMINI_API_KEY.get_secret_value()
        else:
            gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if not gemini_key:
        raise SandboxBackendUnavailableError(
            f"Cannot instantiate Google model '{model_id}': GEMINI_API_KEY not configured."
        )

    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=model_id,
        api_key=gemini_key,
    )
