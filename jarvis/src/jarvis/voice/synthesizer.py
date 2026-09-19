"""Voice and Audio Synthesizer for JARVIS.

Provides local speech synthesis via Windows SAPI and live PCM streaming playback
via sounddevice for real-time auditory interaction with the operator.
"""

import asyncio
import re
import time
from typing import Any, Callable, Dict, Optional

from jarvis.telemetry import logger
from jarvis.voice.output_tracker import PlaybackActivityTracker


def clean_speech_text(text: str) -> str:
    """Strip markdown code blocks, links, and formatting symbols for clean speech output."""
    # Remove code blocks
    cleaned = re.sub(r"```[\s\S]*?```", "", text)
    # Remove markdown link URLs, preserve anchor text
    cleaned = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", cleaned)
    # Remove bold, italics, inline code, and header symbols
    cleaned = re.sub(r"[*_#`~]", "", cleaned)
    # Collapse multiple whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class VoiceSynthesizer:
    """Local text-to-speech synthesizer using Windows SAPI with safe headless fallbacks."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._speaker: Any = None
        self._initialized = False

    def _ensure_speaker(self) -> Any:
        """Lazy-initialize the Windows SAPI SpVoice COM object."""
        if not self._initialized:
            self._initialized = True
            try:
                import win32com.client  # type: ignore[import-untyped]

                self._speaker = win32com.client.Dispatch("SAPI.SpVoice")
            except Exception as err:
                logger.debug(f"SAPI voice unavailable: {err}")
                self._speaker = None
        return self._speaker

    def speak(self, text: str, async_mode: bool = True) -> None:
        """Speak the given text aloud through the default audio output device."""
        if not self.enabled:
            return

        cleaned = clean_speech_text(text)
        if not cleaned:
            return

        speaker = self._ensure_speaker()
        if speaker is None:
            return

        try:
            # SVSFlagsAsync = 1 (speak asynchronously without blocking event loop)
            flags = 1 if async_mode else 0
            speaker.Speak(cleaned, flags)
        except Exception as err:
            logger.debug(f"Speech playback error: {err}")


class PcmStreamPlayer:
    """Streams raw PCM audio chunks (e.g. 24kHz 16-bit mono from Gemini Live) to audio hardware."""

    def __init__(
        self,
        samplerate: int = 24000,
        tracker: Optional[PlaybackActivityTracker] = None,
        on_playback_state_change: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self.samplerate = samplerate
        self.tracker = tracker or PlaybackActivityTracker()
        self.on_playback_state_change = on_playback_state_change
        self._stream: Any = None
        self._is_playing: bool = False

    @property
    def is_playing(self) -> bool:
        """Return True if speaker audio playback is currently active."""
        return self._is_playing

    def _set_playing(self, state: bool) -> None:
        if self._is_playing != state:
            self._is_playing = state
            if self.on_playback_state_change is not None:
                try:
                    self.on_playback_state_change(state)
                except Exception:
                    pass

    def start_stream(self) -> None:
        """Reset output activity tracker for a new response turn."""
        self.tracker.mark_stream_opened()

    def play_chunk(self, chunk: bytes) -> None:
        """Write PCM audio bytes to the active audio output stream."""
        if not chunk:
            return

        try:
            import sounddevice as sd  # type: ignore[import-untyped]

            if self._stream is None:
                self._stream = sd.RawOutputStream(
                    samplerate=self.samplerate,
                    channels=1,
                    dtype="int16",
                )
                self._stream.start()

            self.tracker.mark_chunk(chunk, sample_rate=self.samplerate)
            self._set_playing(True)
            self._stream.write(chunk)
        except Exception as err:
            logger.debug(f"PCM stream playback error: {err}")

    def mark_generation_finished(self) -> None:
        """Mark that model generation has finished delivering audio chunks over transport."""
        self.tracker.mark_generation_finished()

    async def wait_until_drained(self, poll_interval: float = 0.04, timeout: float = 25.0) -> bool:
        """Asynchronously wait until all buffered audio has played and acoustic reverb has decayed."""
        start_time = time.time()
        while not self.tracker.is_playback_drained():
            if time.time() - start_time > timeout:
                logger.warning("Playback drain timeout exceeded; forcing drain.")
                break
            await asyncio.sleep(poll_interval)

        self._set_playing(False)
        return True

    def mark_idle(self) -> None:
        """Mark audio playback as completed and return to idle state."""
        self._set_playing(False)

    def interrupt(self) -> None:
        """Immediately abort active playback upon operator interruption (barge-in)."""
        self.tracker.reset()
        self._set_playing(False)
        if self._stream is not None:
            try:
                self._stream.abort()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def stop(self) -> None:
        """Close the active audio output stream cleanly."""
        self.tracker.reset()
        self._set_playing(False)
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return comprehensive player and buffer drain observability metrics."""
        data = {
            "playback_state": "PLAYING" if self._is_playing else "IDLE",
            "stream_active": self._stream is not None,
            "samplerate": self.samplerate,
        }
        data.update(self.tracker.get_diagnostics())
        return data


# Global singleton synthesizer
voice_synthesizer = VoiceSynthesizer()
