"""Microphone Audio Capture and Streaming Subsystem for JARVIS.

Captures real-time audio from the host microphone, converts frames into
signed 16-bit little-endian PCM format, performs energy analysis (RMS / peak),
and streams chunks to Gemini 3.8 Live for multimodal interaction.
"""

import asyncio
import math
import struct
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from jarvis.config import settings
from jarvis.telemetry import logger


@dataclass
class AudioEnergyStats:
    """Audio energy metrics computed over a PCM audio chunk."""

    peak: int
    rms: float
    is_speech: bool


def read_pcm16_audio_stats(audio_bytes: bytes, speech_threshold: float = 15.0) -> AudioEnergyStats:
    """Read RMS and absolute peak energy from signed 16-bit PCM samples."""
    if not audio_bytes:
        return AudioEnergyStats(peak=0, rms=0.0, is_speech=False)

    try:
        import numpy as np

        samples = np.frombuffer(audio_bytes, dtype=np.int16)
        if len(samples) == 0:
            return AudioEnergyStats(peak=0, rms=0.0, is_speech=False)
        peak = int(np.max(np.abs(samples)))
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
        return AudioEnergyStats(
            peak=peak,
            rms=rms,
            is_speech=rms >= speech_threshold,
        )
    except Exception:
        # Fallback pure-python calculation
        sample_count = len(audio_bytes) // 2
        if sample_count == 0:
            return AudioEnergyStats(peak=0, rms=0.0, is_speech=False)
        unpacked = struct.unpack(f"<{sample_count}h", audio_bytes[: sample_count * 2])
        peak = max(abs(s) for s in unpacked)
        sum_sq = sum(s * s for s in unpacked)
        rms = math.sqrt(sum_sq / sample_count)
        return AudioEnergyStats(
            peak=peak,
            rms=rms,
            is_speech=rms >= speech_threshold,
        )


def list_input_devices() -> List[Dict[str, Any]]:
    """Enumerate available audio input devices dynamically from the operating system."""
    devices: List[Dict[str, Any]] = []
    try:
        import sounddevice as sd  # type: ignore[import-untyped]

        all_devs = sd.query_devices()
        default_in, _ = sd.default.device
        for idx, dev in enumerate(all_devs):
            if dev.get("max_input_channels", 0) > 0:
                devices.append(
                    {
                        "index": idx,
                        "name": dev.get("name", "Unknown Input"),
                        "hostapi": dev.get("hostapi", 0),
                        "max_input_channels": dev.get("max_input_channels", 1),
                        "default_samplerate": dev.get("default_samplerate", 16000.0),
                        "is_default": (idx == default_in),
                    }
                )
    except Exception as err:
        logger.debug(f"Device enumeration error: {err}")
    return devices


def get_default_input_device() -> Optional[Dict[str, Any]]:
    """Return the default system audio input device or None if unavailable."""
    devs = list_input_devices()
    for d in devs:
        if d.get("is_default"):
            return d
    return devs[0] if devs else None


class MicrophoneCapture:
    """Manages real-time capture of microphone audio and queues PCM-16 chunks for streaming."""

    def __init__(
        self,
        samplerate: Optional[int] = None,
        channels: Optional[int] = None,
        chunk_ms: Optional[int] = None,
        device_index: Optional[int] = None,
        speech_threshold: Optional[float] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self.samplerate = samplerate or settings.AUDIO_INPUT_SAMPLE_RATE
        self.channels = channels or settings.AUDIO_INPUT_CHANNELS
        self.chunk_ms = chunk_ms or settings.AUDIO_INPUT_CHUNK_MS
        self.blocksize = int(self.samplerate * (self.chunk_ms / 1000.0))
        self.device_index = (
            device_index if device_index is not None else settings.AUDIO_INPUT_DEVICE_INDEX
        )
        self.speech_threshold = (
            speech_threshold
            if speech_threshold is not None
            else settings.AUDIO_VAD_ENERGY_THRESHOLD
        )
        self._loop = loop

        self._stream: Any = None
        self._is_recording: bool = False
        self._is_muted: bool = False
        self._duplex_suppressed: bool = False
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self._audio_chunk_callback: Optional[Callable[[bytes], Any]] = None

        self.current_stats: AudioEnergyStats = AudioEnergyStats(peak=0, rms=0.0, is_speech=False)
        self.total_frames_captured: int = 0
        self.overflow_count: int = 0

    @property
    def is_recording(self) -> bool:
        """Return True if the microphone capture stream is actively running."""
        return self._is_recording

    @property
    def is_muted(self) -> bool:
        """Return True if input is currently muted."""
        return self._is_muted

    def set_muted(self, muted: bool) -> None:
        """Mute or unmute microphone streaming."""
        self._is_muted = muted
        logger.debug(f"Microphone mute state set to: {muted}")

    def set_duplex_suppression(self, suppressed: bool) -> None:
        """Suppress microphone capture while speaker audio is playing to prevent acoustic feedback."""
        self._duplex_suppressed = suppressed

    def _audio_callback(self, indata: bytes, frames: int, time_info: Any, status: Any) -> None:
        """PortAudio thread callback invoked for each captured raw PCM block."""
        if not self._is_recording:
            return

        if status and status.input_overflow:
            self.overflow_count += 1

        stats = read_pcm16_audio_stats(indata, speech_threshold=self.speech_threshold)
        self.current_stats = stats
        self.total_frames_captured += frames

        if self._is_muted:
            return

        # Acoustic feedback suppression: ignore lower-energy chunks if speaker output is active
        if self._duplex_suppressed and stats.rms < (self.speech_threshold * 1.8):
            return

        chunk = bytes(indata)

        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

        if loop and not loop.is_closed():
            # Enqueue into async queue
            try:
                loop.call_soon_threadsafe(self._enqueue_chunk, chunk)
            except Exception:
                pass

            # Invoke optional direct callback
            if self._audio_chunk_callback is not None:
                try:
                    loop.call_soon_threadsafe(self._audio_chunk_callback, chunk)
                except Exception:
                    pass

    def _enqueue_chunk(self, chunk: bytes) -> None:
        """Enqueue chunk into async queue, dropping oldest if buffer is full."""
        if self._audio_queue.full():
            try:
                self._audio_queue.get_nowait()
            except Exception:
                pass
        try:
            self._audio_queue.put_nowait(chunk)
        except Exception:
            pass

    def start(
        self,
        on_chunk: Optional[Callable[[bytes], Any]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        """Open the raw PCM input stream and begin capturing audio."""
        if self._is_recording:
            return

        if loop is not None:
            self._loop = loop
        elif self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

        self._audio_chunk_callback = on_chunk

        import sounddevice as sd

        try:
            self._stream = sd.RawInputStream(
                samplerate=self.samplerate,
                channels=self.channels,
                dtype="int16",
                blocksize=self.blocksize,
                device=self.device_index,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._is_recording = True
            logger.info(
                f"Microphone capture started: rate={self.samplerate}Hz, "
                f"channels={self.channels}, blocksize={self.blocksize} "
                f"({self.chunk_ms}ms), device={self.device_index}"
            )
        except Exception as err:
            logger.error(f"Failed to start microphone stream: {err}")
            self._is_recording = False
            raise

    def stop(self) -> None:
        """Stop and close the audio capture stream."""
        self._is_recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as err:
                logger.debug(f"Error closing microphone stream: {err}")
            self._stream = None
        logger.info("Microphone capture stopped.")

    async def read_chunk(self) -> bytes:
        """Asynchronously read the next available audio chunk from the capture queue."""
        return await self._audio_queue.get()

    def read_chunk_nowait(self) -> Optional[bytes]:
        """Read the next audio chunk without waiting, or return None if queue is empty."""
        try:
            return self._audio_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
