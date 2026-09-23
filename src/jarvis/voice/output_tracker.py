"""Playback Activity Tracker for Assistant Audio Output.

Adapted from Section 4 of Voice Architecture (and native output-activity-tracker):
Tracks audio duration emitted by the model, distinguishes between generationFinished
and playbackFinished, and ensures the microphone input gate remains closed until
all speaker output has physically drained from hardware buffers and room acoustics settle.
"""

import time
from typing import Any, Dict, Optional

from jarvis.config import settings


class PlaybackActivityTracker:
    """Tracks emitted audio duration and enforces genuine playback drain."""

    def __init__(self, drain_hold_ms: Optional[int] = None) -> None:
        self.drain_hold_ms = (
            drain_hold_ms
            if drain_hold_ms is not None
            else getattr(settings, "VOICE_DRAIN_HOLD_MS", 250)
        )

        self._audio_ms: float = 0.0
        self._chunks_received: int = 0
        self._source_bytes: int = 0
        self._generation_finished: bool = False
        self._playback_started_at: Optional[float] = None
        self._last_chunk_at: Optional[float] = None
        self._drain_completed_at: Optional[float] = None

    @property
    def generation_finished(self) -> bool:
        """Return True if model turn generation has completed over the transport."""
        return self._generation_finished

    @property
    def chunks_received(self) -> int:
        """Return total chunks received in current response turn."""
        return self._chunks_received

    @property
    def audio_ms(self) -> float:
        """Return cumulative milliseconds of audio received from the model."""
        return self._audio_ms

    def mark_stream_opened(self) -> None:
        """Mark start of a new response stream from the model."""
        self._audio_ms = 0.0
        self._chunks_received = 0
        self._source_bytes = 0
        self._generation_finished = False
        self._playback_started_at = None
        self._last_chunk_at = None
        self._drain_completed_at = None

    def mark_chunk(self, chunk_bytes: bytes, sample_rate: int = 24000) -> None:
        """Record an incoming audio chunk and update cumulative duration."""
        now = time.time()
        if self._playback_started_at is None:
            self._playback_started_at = now

        byte_len = len(chunk_bytes)
        self._source_bytes += byte_len
        self._chunks_received += 1
        self._last_chunk_at = now

        # 16-bit mono PCM = 2 bytes per sample
        bytes_per_sample = 2
        chunk_duration_ms = (byte_len / (sample_rate * bytes_per_sample)) * 1000.0
        self._audio_ms += max(0.0, chunk_duration_ms)

    def mark_generation_finished(self) -> None:
        """Mark that model generation has finished sending chunks over the network."""
        self._generation_finished = True

    def elapsed_playback_ms(self) -> float:
        """Return elapsed physical milliseconds since first audio chunk arrived."""
        if self._playback_started_at is None:
            return 0.0
        return max(0.0, (time.time() - self._playback_started_at) * 1000.0)

    def remaining_playback_ms(self) -> float:
        """Return estimated milliseconds of audio still physically playing out of hardware."""
        if self._chunks_received == 0:
            return 0.0
        elapsed = self.elapsed_playback_ms()
        remaining = self._audio_ms - elapsed
        return max(0.0, remaining)

    def is_playback_drained(self) -> bool:
        """Return True only if model generation finished AND physical playback buffer has drained."""
        if not self._generation_finished:
            return False

        if self._chunks_received == 0:
            return True

        now = time.time()
        # Must have completed audio duration
        if self.remaining_playback_ms() > 0.0:
            return False

        # Must have satisfied drain hold cooldown to allow acoustic decay
        if self._last_chunk_at is not None:
            time_since_last_chunk_ms = (now - self._last_chunk_at) * 1000.0
            if time_since_last_chunk_ms < self.drain_hold_ms:
                return False

        return True

    def reset(self) -> None:
        """Reset all tracking counters back to initial idle state."""
        self._audio_ms = 0.0
        self._chunks_received = 0
        self._source_bytes = 0
        self._generation_finished = False
        self._playback_started_at = None
        self._last_chunk_at = None
        self._drain_completed_at = None

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return current output activity metrics for observability."""
        return {
            "chunks_received": self._chunks_received,
            "total_source_bytes": self._source_bytes,
            "total_audio_ms": round(self._audio_ms, 1),
            "elapsed_playback_ms": round(self.elapsed_playback_ms(), 1),
            "remaining_playback_ms": round(self.remaining_playback_ms(), 1),
            "generation_finished": self._generation_finished,
            "is_playback_drained": self.is_playback_drained(),
            "drain_hold_ms": self.drain_hold_ms,
        }
