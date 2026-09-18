"""Hardware Audio I/O Manager for Realtime Voice Streaming.

Coordinates local microphone recording (16kHz 16-bit mono PCM) and speaker output
playback (24kHz 16-bit mono PCM) using sounddevice, with zero-latency barge-in flushing,
dynamic hardware device discovery, adaptive native sample rate negotiation, and
vectorized real-time resampling.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from jarvis.core.logging import get_logger

logger = get_logger(__name__)

# Canonical wire specifications conforming to Gemini Live specifications
DEFAULT_INPUT_SAMPLE_RATE = 16000
DEFAULT_OUTPUT_SAMPLE_RATE = 24000
DEFAULT_CHANNELS = 1
DEFAULT_DTYPE = "int16"
DEFAULT_BLOCK_SIZE = 1600  # 100ms chunks at 16kHz (3200 bytes per chunk)


class AudioDeviceResolver:
    """Dynamic discovery and capability negotiation helper for audio hardware endpoints."""

    @staticmethod
    def list_devices() -> list[dict[str, Any]]:
        """Enumerate all available physical and virtual host audio endpoints."""
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            apis = sd.query_hostapis()
            results: list[dict[str, Any]] = []
            for idx, d in enumerate(devices):
                api_info = apis[d["hostapi"]] if d["hostapi"] < len(apis) else {}
                results.append(
                    {
                        "index": idx,
                        "name": str(d.get("name", "")),
                        "hostapi_index": d.get("hostapi", 0),
                        "hostapi_name": str(api_info.get("name", "")),
                        "max_input_channels": int(d.get("max_input_channels", 0)),
                        "max_output_channels": int(d.get("max_output_channels", 0)),
                        "default_samplerate": float(d.get("default_samplerate", 44100.0)),
                    }
                )
            return results
        except Exception as exc:
            logger.warning("audio_device_enumeration_failed", error=str(exc))
            return []

    @classmethod
    def list_input_devices(cls) -> list[dict[str, Any]]:
        """Enumerate endpoints supporting physical audio capture."""
        return [d for d in cls.list_devices() if d["max_input_channels"] > 0]

    @classmethod
    def list_output_devices(cls) -> list[dict[str, Any]]:
        """Enumerate endpoints supporting audio playback."""
        return [d for d in cls.list_devices() if d["max_output_channels"] > 0]

    @classmethod
    def resolve_input_device(
        cls,
        requested: int | str | None = None,
        target_sample_rate: int = DEFAULT_INPUT_SAMPLE_RATE,
        target_channels: int = DEFAULT_CHANNELS,
    ) -> tuple[int | None, str, str, int, int, bool]:
        """Resolve the optimal input capture device and negotiate native hardware parameters.

        Returns:
            tuple of (device_index, device_name, hostapi_name, hardware_sr, hardware_channels, needs_resampling)
        """
        inputs = cls.list_input_devices()
        if not inputs:
            return None, "Default Input", "Unknown", target_sample_rate, target_channels, False

        selected_dev: dict[str, Any] | None = None

        # 1. Explicit selection if provided
        if requested is not None:
            req_str = str(requested).strip()
            if req_str.isdigit():
                idx = int(req_str)
                selected_dev = next((d for d in inputs if d["index"] == idx), None)
            if selected_dev is None:
                q = req_str.lower()
                selected_dev = next(
                    (d for d in inputs if q in d["name"].lower() or q in d["hostapi_name"].lower()),
                    None,
                )

        # 2. Auto-discovery fallback
        if selected_dev is None:
            if sys.platform == "win32":
                # On Windows, prefer Windows WASAPI for low latency and zero MME channel truncation
                wasapi_mics = [
                    d
                    for d in inputs
                    if "wasapi" in d["hostapi_name"].lower()
                    and ("mic" in d["name"].lower() or "input" in d["name"].lower())
                ]
                if wasapi_mics:
                    selected_dev = wasapi_mics[0]
                else:
                    # Fall back to any WASAPI capture endpoint
                    wasapi_any = [d for d in inputs if "wasapi" in d["hostapi_name"].lower()]
                    if wasapi_any:
                        selected_dev = wasapi_any[0]

            if selected_dev is None:
                # Default system input device
                try:
                    import sounddevice as sd

                    def_in = sd.default.device[0]
                    selected_dev = next((d for d in inputs if d["index"] == def_in), inputs[0])
                except Exception:
                    selected_dev = inputs[0]

        dev_idx = int(selected_dev["index"])
        dev_name = str(selected_dev["name"])
        api_name = str(selected_dev["hostapi_name"])

        # 3. Test if device natively accepts target sample rate and channels directly
        can_direct = False
        try:
            import sounddevice as sd

            sd.check_input_settings(
                device=dev_idx,
                samplerate=target_sample_rate,
                channels=target_channels,
                dtype=DEFAULT_DTYPE,
            )
            can_direct = True
        except Exception:
            can_direct = False

        if can_direct:
            return dev_idx, dev_name, api_name, target_sample_rate, target_channels, False

        # Native hardware parameters requiring software resampling
        hw_sr = int(selected_dev["default_samplerate"])
        hw_channels = min(int(selected_dev["max_input_channels"]), 2)
        return dev_idx, dev_name, api_name, hw_sr, hw_channels, True

    @classmethod
    def resolve_output_device(
        cls, requested: int | str | None = None
    ) -> tuple[int | None, str, str]:
        """Resolve the active playback device."""
        outputs = cls.list_output_devices()
        if not outputs:
            return None, "Default Output", "Unknown"

        if requested is not None:
            req_str = str(requested).strip()
            if req_str.isdigit():
                idx = int(req_str)
                m = next((d for d in outputs if d["index"] == idx), None)
                if m:
                    return m["index"], m["name"], m["hostapi_name"]
            q = req_str.lower()
            m = next(
                (d for d in outputs if q in d["name"].lower() or q in d["hostapi_name"].lower()),
                None,
            )
            if m:
                return m["index"], m["name"], m["hostapi_name"]

        try:
            import sounddevice as sd

            def_out = sd.default.device[1]
            m = next((d for d in outputs if d["index"] == def_out), outputs[0])
            return m["index"], m["name"], m["hostapi_name"]
        except Exception:
            return outputs[0]["index"], outputs[0]["name"], outputs[0]["hostapi_name"]


class AudioIOManager:
    """Asynchronous audio capture and playback manager with dynamic hardware discovery."""

    def __init__(
        self,
        input_sample_rate: int = DEFAULT_INPUT_SAMPLE_RATE,
        output_sample_rate: int = DEFAULT_OUTPUT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        block_size: int = DEFAULT_BLOCK_SIZE,
        gain: float | None = None,
        input_device: int | str | None = None,
        output_device: int | str | None = None,
        auto_volume: bool | None = None,
    ) -> None:
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate
        self.channels = channels
        self.block_size = block_size

        try:
            from jarvis.core.config import get_settings

            settings = get_settings()
            self.input_device = (
                input_device if input_device is not None else settings.VOICE_INPUT_DEVICE
            )
            self.output_device = (
                output_device if output_device is not None else settings.VOICE_OUTPUT_DEVICE
            )
            self.gain = gain if gain is not None else settings.VOICE_MIC_GAIN
            self.auto_volume = (
                auto_volume if auto_volume is not None else settings.VOICE_MIC_AUTO_VOLUME
            )
        except Exception:
            self.input_device = input_device
            self.output_device = output_device
            self.gain = gain if gain is not None else 1.0
            self.auto_volume = True if auto_volume is None else auto_volume

        self.is_recording = False
        self.is_playing = False
        self.audio_available = False

        # Active hardware parameters
        self.active_input_device: int | None = None
        self.active_input_name: str = "Unknown"
        self.active_input_api: str = "Unknown"
        self.active_input_sample_rate: int = self.input_sample_rate
        self.active_input_channels: int = self.channels
        self.active_needs_resampling: bool = False

        self.active_output_device: int | None = None
        self.active_output_name: str = "Unknown"
        self.active_output_api: str = "Unknown"

        self._in_stream: Any = None
        self._out_stream: Any = None
        self._input_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._output_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._playback_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self._check_hardware_availability()
        if self.auto_volume:
            self.ensure_system_microphone_volume()

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

    def ensure_system_microphone_volume(self) -> None:
        """Verify and optimize Windows master microphone capture endpoint volume."""
        if sys.platform != "win32":
            return
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            mic_dev = AudioUtilities.GetMicrophone()
            if not mic_dev:
                return
            vol_iface = mic_dev.Activate(IAudioEndpointVolume._iid_, 1, None).QueryInterface(
                IAudioEndpointVolume
            )
            if vol_iface.GetMute():
                logger.info("unmuting_system_microphone")
                vol_iface.SetMute(False, None)
            curr_vol = vol_iface.GetMasterVolumeLevelScalar()
            if curr_vol < 0.85:
                logger.info(
                    "optimizing_system_microphone_volume",
                    previous=round(curr_vol, 2),
                    target=1.0,
                )
                vol_iface.SetMasterVolumeLevelScalar(1.0, None)
        except Exception as exc:
            logger.debug("auto_microphone_volume_check_failed", error=str(exc))

    def start_input_stream(self) -> None:
        """Start capturing microphone audio with hardware-negotiated sample rate & downsampling."""
        if not self.audio_available:
            logger.warning("skipping_microphone_start_audio_unavailable")
            return

        import sounddevice as sd

        self._loop = asyncio.get_running_loop()

        (
            dev_idx,
            dev_name,
            api_name,
            hw_sr,
            hw_channels,
            needs_resampling,
        ) = AudioDeviceResolver.resolve_input_device(
            requested=self.input_device,
            target_sample_rate=self.input_sample_rate,
            target_channels=self.channels,
        )

        self.active_input_device = dev_idx
        self.active_input_name = dev_name
        self.active_input_api = api_name
        self.active_input_sample_rate = hw_sr
        self.active_input_channels = hw_channels
        self.active_needs_resampling = needs_resampling

        # Sizing input blocksize to match target 100ms duration
        if needs_resampling:
            hw_block_size = round(hw_sr * (self.block_size / self.input_sample_rate))
        else:
            hw_block_size = self.block_size

        target_block = self.block_size
        gain_val = float(self.gain)

        def _input_callback(
            indata: bytes,
            frames: int,
            time_info: Any,
            status: sd.CallbackFlags,
        ) -> None:
            if status:
                logger.debug("audio_input_status_flag", status=str(status))
            if self.is_recording and self._loop and not self._loop.is_closed():
                if needs_resampling:
                    raw = np.frombuffer(indata, dtype=np.int16).reshape(-1, hw_channels)
                    if hw_channels == 2:
                        mono = (raw[:, 0].astype(np.int32) + raw[:, 1].astype(np.int32)) // 2
                    else:
                        mono = raw[:, 0].astype(np.int32)

                    if hw_sr == 48000:
                        resampled = mono[::3].astype(np.float64)
                    else:
                        orig_x = np.linspace(0.0, 1.0, len(mono), endpoint=False)
                        target_x = np.linspace(0.0, 1.0, target_block, endpoint=False)
                        resampled = np.interp(target_x, orig_x, mono.astype(np.float64))

                    if gain_val != 1.0:
                        resampled = resampled * gain_val

                    chunk_bytes = np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()
                else:
                    if gain_val != 1.0 and len(indata) >= 2:
                        arr = np.frombuffer(indata, dtype=np.int16).astype(np.float32)
                        chunk_bytes = (
                            np.clip(arr * gain_val, -32768, 32767).astype(np.int16).tobytes()
                        )
                    else:
                        chunk_bytes = bytes(indata)

                self._loop.call_soon_threadsafe(self._input_queue.put_nowait, chunk_bytes)

        self._in_stream = sd.RawInputStream(
            samplerate=hw_sr,
            channels=hw_channels,
            dtype=DEFAULT_DTYPE,
            blocksize=hw_block_size,
            device=dev_idx,
            callback=_input_callback,
        )
        self._in_stream.start()
        self.is_recording = True
        logger.info(
            "microphone_stream_started",
            device=dev_name,
            api=api_name,
            hardware_rate=hw_sr,
            hardware_channels=hw_channels,
            target_rate=self.input_sample_rate,
            resampling=needs_resampling,
            gain=self.gain,
        )

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

        dev_idx, dev_name, api_name = AudioDeviceResolver.resolve_output_device(self.output_device)
        self.active_output_device = dev_idx
        self.active_output_name = dev_name
        self.active_output_api = api_name

        self._out_stream = sd.RawOutputStream(
            samplerate=self.output_sample_rate,
            channels=self.channels,
            dtype=DEFAULT_DTYPE,
            device=dev_idx,
        )
        self._out_stream.start()
        self.is_playing = True
        self._playback_task = asyncio.create_task(
            self._run_playback_loop(), name="audio_speaker_playback"
        )
        logger.info(
            "speaker_stream_started",
            device=dev_name,
            api=api_name,
            rate=self.output_sample_rate,
        )

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
