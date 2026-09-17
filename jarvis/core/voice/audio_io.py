"""Hardware Audio I/O Manager for Realtime Voice Streaming.

Coordinates local microphone recording (16kHz 16-bit mono PCM) and speaker output
playback (24kHz 16-bit mono PCM) using sounddevice, with zero-latency barge-in flushing.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core.logging import get_logger

logger = get_logger(__name__)

# Wire constants conforming to MODEL_ROSTER.md specifications
DEFAULT_INPUT_SAMPLE_RATE = 16000
DEFAULT_OUTPUT_SAMPLE_RATE = 24000
DEFAULT_CHANNELS = 1
DEFAULT_DTYPE = "int16"
DEFAULT_BLOCK_SIZE = 1600  # 100ms chunks at 16kHz (3200 bytes per chunk)


class AudioIOManager:
    """Asynchronous audio capture and playback manager with hardware abstraction."""

    def __init__(
        self,
        input_sample_rate: int = DEFAULT_INPUT_SAMPLE_RATE,
        output_sample_rate: int = DEFAULT_OUTPUT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        block_size: int = DEFAULT_BLOCK_SIZE,
    ) -> None:
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate
        self.channels = channels
        self.block_size = block_size

        self.is_recording = False
        self.is_playing = False
        self.audio_available = False

        self._in_stream: Any = None
        self._out_stream: Any = None
        self._input_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._output_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._playback_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self._check_hardware_availability()

    def _check_hardware_availability(self) -> None:
        """Inspect host platform for sounddevice availability and active audio devices."""
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            if devices:
                self.audio_available = True
                logger.info("audio_hardware_detected", total_devices=len(devices))
            else:
                logger.warning("no_audio_devices_detected")
        except Exception as exc:
            logger.warning("audio_hardware_check_failed", error=str(exc))
            self.audio_available = False

    def start_input_stream(self) -> None:
        """Start capturing microphone audio chunks into the asynchronous input queue."""
        if not self.audio_available:
            logger.warning("skipping_microphone_start_audio_unavailable")
            return

        import sounddevice as sd

        self._loop = asyncio.get_running_loop()

        def _input_callback(
            indata: bytes,
            frames: int,
            time_info: Any,
            status: sd.CallbackFlags,
        ) -> None:
            if status:
                logger.debug("audio_input_status_flag", status=str(status))
            if self.is_recording and self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._input_queue.put_nowait, bytes(indata))

        self._in_stream = sd.RawInputStream(
            samplerate=self.input_sample_rate,
            channels=self.channels,
            dtype=DEFAULT_DTYPE,
            blocksize=self.block_size,
            callback=_input_callback,
        )
        self._in_stream.start()
        self.is_recording = True
        logger.info("microphone_stream_started", rate=self.input_sample_rate)

    def stop_input_stream(self) -> None:
        """Stop capturing client microphone audio."""
        self.is_recording = False
        if self._in_stream:
            try:
                self._in_stream.stop()
                self._in_stream.close()
            except Exception as e:
                logger.debug("error_stopping_input_stream", error=str(e))
            finally:
                self._in_stream = None

    def start_output_stream(self) -> None:
        """Initialize speaker output playback stream and consumer loop."""
        if not self.audio_available:
            return

        import sounddevice as sd

        self._out_stream = sd.RawOutputStream(
            samplerate=self.output_sample_rate,
            channels=self.channels,
            dtype=DEFAULT_DTYPE,
        )
        self._out_stream.start()
        self.is_playing = True
        self._playback_task = asyncio.create_task(
            self._run_playback_loop(), name="audio_speaker_playback"
        )
        logger.info("speaker_stream_started", rate=self.output_sample_rate)

    async def _run_playback_loop(self) -> None:
        """Continually dequeue and write server audio chunks to hardware speakers."""
        while self.is_playing:
            try:
                pcm_chunk = await self._output_queue.get()
                if self._out_stream and self.is_playing:
                    # Execute synchronous sounddevice write in threadpool to prevent event loop lag
                    await asyncio.to_thread(self._out_stream.write, pcm_chunk)
                self._output_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("audio_playback_error", error=str(exc))

    def play_audio_chunk(self, pcm_data: bytes) -> None:
        """Queue 24kHz PCM chunk received from voice model for speaker playback."""
        if self.is_playing:
            self._output_queue.put_nowait(pcm_data)

    def flush_output(self) -> None:
        """Instantly flush all pending playback audio chunks to support true barge-in."""
        # Empty the queue immediately
        while not self._output_queue.empty():
            try:
                self._output_queue.get_nowait()
                self._output_queue.task_done()
            except Exception:
                break

        # Abort active hardware buffer to cut off current phoneme
        if self._out_stream:
            try:
                self._out_stream.abort()
                self._out_stream.start()
            except Exception as e:
                logger.debug("error_aborting_output_stream", error=str(e))

        logger.debug("audio_playback_flushed_barge_in")

    def stop_output_stream(self) -> None:
        """Terminate speaker playback and release output device."""
        self.is_playing = False
        self.flush_output()
        if self._playback_task and not self._playback_task.done():
            self._playback_task.cancel()
        if self._out_stream:
            try:
                self._out_stream.stop()
                self._out_stream.close()
            except Exception as e:
                logger.debug("error_stopping_output_stream", error=str(e))
            finally:
                self._out_stream = None

    async def get_microphone_stream(self) -> AsyncIterator[bytes]:
        """Asynchronous stream of captured raw 16kHz PCM audio bytes."""
        while self.is_recording:
            try:
                chunk = await asyncio.wait_for(self._input_queue.get(), timeout=0.1)
                yield chunk
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def close(self) -> None:
        """Teardown both input and output streams."""
        self.stop_input_stream()
        self.stop_output_stream()
