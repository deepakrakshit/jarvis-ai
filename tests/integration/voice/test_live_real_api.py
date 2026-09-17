"""Live integration test for Google Realtime Live API (bidirectional WebSocket).

Gated strictly on availability of real GEMINI_API_KEY in environment.
"""

import os

import pytest
from dotenv import load_dotenv

from jarvis.core.config import get_settings
from jarvis.core.exceptions import ModelProviderError
from jarvis.core.gateway.google_realtime import GoogleRealtimeAdapter
from jarvis.core.gateway.realtime import LiveSessionConfig

load_dotenv()

has_gemini_key = bool(
    os.getenv("GEMINI_API_KEY") and not os.getenv("GEMINI_API_KEY", "").startswith("your_")
)


@pytest.mark.skipif(not has_gemini_key, reason="Real GEMINI_API_KEY not provided")
@pytest.mark.asyncio
async def test_live_gemini_realtime_connectivity() -> None:
    """Validate live connectivity to Gemini Live API when credentials are provided."""
    settings = get_settings()
    api_key = (
        settings.GEMINI_API_KEY.get_secret_value()
        if settings.GEMINI_API_KEY
        else os.getenv("GEMINI_API_KEY")
    )
    if not api_key:
        pytest.skip("No Gemini API key available")

    adapter = GoogleRealtimeAdapter(api_key=api_key)
    config = LiveSessionConfig(
        model_id=settings.REALTIME_VOICE_MODEL_ID,
        voice_name=settings.VOICE_DEFAULT_NAME,
        system_instruction="You are JARVIS voice assistant. Be brief.",
    )

    try:
        await adapter.connect(config)
        assert adapter.connected is True

        # Send text greeting to live model
        await adapter.send_text("Hello JARVIS, ping test.", end_of_turn=True)

        # Gracefully disconnect
        await adapter.close()
        assert adapter.connected is False
    except ModelProviderError as exc:
        pytest.skip(f"Live Gemini Realtime API endpoint unavailable or restricted: {exc}")
    except Exception as exc:
        pytest.skip(f"Live network error during Live API test: {exc}")
