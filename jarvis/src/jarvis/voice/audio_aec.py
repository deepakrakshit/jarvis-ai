"""Acoustic Echo Cancellation (AEC) and Reference Loopback Subsystem for JARVIS.

Captures a private internal reference stream from Windows audio render endpoints
(WASAPI Loopback), synchronizes and aligns it with incoming physical microphone audio,
and applies Partitioned Block Frequency-Domain Adaptive Filtering (PBFDAF) with
Double-Talk Detection (DTD) and Non-Linear Residual Echo Suppression (RES).

This ensures external application audio (e.g. YouTube, Spotify, games, system sounds)
radiating from physical speakers is cancelled before microphone PCM frames reach
the Gemini Live transport.

Strict Invariants:
1. The render reference stream is strictly internal and NEVER forwarded to Gemini Live.
2. Assistant half-duplex self-echo suppression is preserved.
3. Fallback to transparent pass-through if audio hardware or loopback is unavailable.
"""

import math
import platform
import threading
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from scipy import signal  # type: ignore[import-untyped]

from jarvis.config import settings
from jarvis.telemetry import logger

FloatArray = np.ndarray[Any, np.dtype[np.float32]]
ComplexArray = np.ndarray[Any, np.dtype[np.complex64]]


class ReferenceAudioBuffer:
    """Thread-safe circular ring buffer holding recent reference audio samples."""

    def __init__(self, capacity_samples: int = 32000) -> None:
        self.capacity = capacity_samples
        self._buffer: FloatArray = np.zeros(self.capacity, dtype=np.float32)
        self._write_pos = 0
        self._total_written = 0
        self._lock = threading.Lock()

    def write(self, samples: FloatArray) -> None:
        """Append new float32 reference audio samples to the ring buffer."""
        if len(samples) == 0:
            return
        n = len(samples)
        with self._lock:
            if n >= self.capacity:
                self._buffer[:] = samples[-self.capacity :]
                self._write_pos = 0
            else:
                end_pos = self._write_pos + n
                if end_pos <= self.capacity:
                    self._buffer[self._write_pos : end_pos] = samples
                else:
                    first_part = self.capacity - self._write_pos
                    self._buffer[self._write_pos :] = samples[:first_part]
                    self._buffer[: n - first_part] = samples[first_part:]
                self._write_pos = end_pos % self.capacity
            self._total_written += n

    def get_recent(self, num_samples: int, delay_offset: int = 0) -> FloatArray:
        """Retrieve recent samples delayed by delay_offset samples from latest write."""
        if num_samples <= 0:
            empty: FloatArray = np.zeros(0, dtype=np.float32)
            return empty
        with self._lock:
            if self._total_written == 0:
                zeros: FloatArray = np.zeros(num_samples, dtype=np.float32)
                return zeros

            effective_delay = max(0, delay_offset)
            start_offset = self._write_pos - effective_delay - num_samples
            indices = np.arange(start_offset, start_offset + num_samples) % self.capacity
            res: FloatArray = self._buffer[indices].copy()
            return res

    @property
    def total_written(self) -> int:
        """Return total count of samples written since initialization."""
        with self._lock:
            return self._total_written


class WasapiLoopbackStream:
    """Captures Windows default render audio using native WASAPI Loopback.

    The captured stream is private and internal to the AEC engine.
    """

    def __init__(self, target_sample_rate: int = 16000) -> None:
        self.target_sample_rate = target_sample_rate
        self.buffer = ReferenceAudioBuffer(capacity_samples=target_sample_rate * 2)
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.total_packets_captured = 0
        self.total_samples_captured = 0
        self.is_active = False
        self.current_rms: float = 0.0

    def start(self) -> None:
        """Start the background WASAPI loopback capture worker thread."""
        if self.is_running:
            return
        if platform.system() != "Windows":
            logger.debug(
                "WASAPI Loopback is only supported on Windows; AEC operating in pass-through."
            )
            return

        self.is_running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True, name="JarvisAecLoopback")
        self._thread.start()
        logger.info("WASAPI Loopback render capture thread started.")

    def stop(self) -> None:
        """Signal worker thread to stop and wait for completion."""
        self.is_running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("WASAPI Loopback render capture stopped.")

    def _worker(self) -> None:
        """Background worker loop capturing render loopback frames via Windows Core Audio."""
        try:
            from ctypes import POINTER, c_byte, c_uint32, c_uint64, c_ulong, c_void_p, cast

            import comtypes
            import pycaw.pycaw as pycaw
            from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
            from pycaw.constants import EDataFlow, ERole
            from pycaw.utils import AudioUtilities

            comtypes.CoInitialize()
        except Exception as err:
            logger.debug(f"Failed to initialize COM for WASAPI Loopback: {err}")
            self.is_running = False
            return

        audio_client = None
        capture_client = None

        try:
            audclnt_streamflags_loopback = 0x00020000
            audclnt_sharemode_shared = 0

            class IAudioCaptureClient(IUnknown):  # type: ignore[misc]
                _iid_ = GUID("{c8adbd64-e71e-48a0-a4de-185c395cd317}")
                _methods_ = [
                    COMMETHOD(
                        [],
                        HRESULT,
                        "GetBuffer",
                        (["out"], POINTER(POINTER(c_byte)), "ppData"),
                        (["out"], POINTER(c_uint32), "pNumFramesToRead"),
                        (["out"], POINTER(c_ulong), "pdwFlags"),
                        (["out"], POINTER(c_uint64), "pu64DevicePosition"),
                        (["out"], POINTER(c_uint64), "pu64QPCPosition"),
                    ),
                    COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "NumFramesRead")),
                    COMMETHOD(
                        [],
                        HRESULT,
                        "GetNextPacketSize",
                        (["out"], POINTER(c_uint32), "pNumFramesInNextPacket"),
                    ),
                ]

            enumerator = AudioUtilities.GetDeviceEnumerator()
            default_render = enumerator.GetDefaultAudioEndpoint(
                EDataFlow.eRender.value, ERole.eConsole.value
            )
            audio_client_p = default_render.Activate(
                pycaw.IAudioClient._iid_, comtypes.CLSCTX_ALL, None
            )
            audio_client = audio_client_p.QueryInterface(pycaw.IAudioClient)

            mix_format_p = audio_client.GetMixFormat()
            mix_format = mix_format_p.contents
            channels = int(mix_format.nChannels)
            sample_rate = int(mix_format.nSamplesPerSec)
            bits = int(mix_format.wBitsPerSample)
            bytes_per_sample = bits // 8

            buffer_duration = 2000000  # 200ms
            audio_client.Initialize(
                audclnt_sharemode_shared,
                audclnt_streamflags_loopback,
                buffer_duration,
                0,
                mix_format_p,
                None,
            )

            capture_client_p = audio_client.GetService(IAudioCaptureClient._iid_)
            capture_client = capture_client_p.QueryInterface(IAudioCaptureClient)
            audio_client.Start()
            self.is_active = True

            decimate_factor = (
                sample_rate // self.target_sample_rate
                if sample_rate % self.target_sample_rate == 0
                else 1
            )

            while not self._stop_event.is_set():
                packet_size = capture_client.GetNextPacketSize()
                if packet_size == 0:
                    time.sleep(0.005)
                    continue

                data_p, num_frames, flags, dev_pos, qpc_pos = capture_client.GetBuffer()
                self.total_packets_captured += 1

                if flags & 1:  # AUDCLNT_BUFFERFLAGS_SILENT
                    samples = np.zeros(num_frames * channels, dtype=np.float32)
                else:
                    byte_count = num_frames * channels * bytes_per_sample
                    raw_bytes = bytes(
                        (c_byte * byte_count).from_address(cast(data_p, c_void_p).value)
                    )
                    if bits == 32:
                        samples = np.frombuffer(raw_bytes, dtype=np.float32)
                    elif bits == 16:
                        samples = (
                            np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                        )
                    else:
                        samples = np.zeros(num_frames * channels, dtype=np.float32)

                capture_client.ReleaseBuffer(num_frames)

                if len(samples) == 0:
                    continue

                stereo = samples.reshape(-1, channels)
                mono = np.mean(stereo, axis=1)

                if decimate_factor > 1:
                    mono_resampled: FloatArray = mono[::decimate_factor].astype(np.float32)
                elif sample_rate != self.target_sample_rate:
                    target_len = int(len(mono) * self.target_sample_rate / sample_rate)
                    mono_resampled = signal.resample(mono, target_len).astype(np.float32)
                else:
                    mono_resampled = mono.astype(np.float32)

                self.current_rms = float(np.sqrt(np.mean(mono_resampled**2)))
                self.total_samples_captured += len(mono_resampled)
                self.buffer.write(mono_resampled)

        except Exception as err:
            logger.debug(f"WASAPI Loopback capture worker encountered error: {err}")
        finally:
            if audio_client:
                try:
                    audio_client.Stop()
                except Exception:
                    pass
            self.is_active = False
            self.is_running = False
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass


class AcousticDelayEstimator:
    """Estimates acoustic transmission latency between speaker playback and microphone arrival."""

    def __init__(
        self,
        sample_rate: int = 16000,
        max_search_ms: int = 200,
        min_search_ms: int = 10,
    ) -> None:
        self.sample_rate = sample_rate
        self.min_delay_samples = int(min_search_ms * sample_rate / 1000)
        self.max_delay_samples = int(max_search_ms * sample_rate / 1000)
        self.estimated_delay_samples: int = self.min_delay_samples + (
            (self.max_delay_samples - self.min_delay_samples) // 3
        )
        self.confidence: float = 0.0
        self._update_counter = 0

    def estimate_delay(self, mic_block: FloatArray, ref_history: FloatArray) -> int:
        """Estimate delay using normalized cross-correlation between mic chunk and reference history."""
        self._update_counter += 1
        # Only re-estimate every 4 blocks to minimize CPU usage while maintaining tracking
        if self._update_counter % 4 != 0:
            return self.estimated_delay_samples

        mic_rms = float(np.sqrt(np.mean(mic_block**2)))
        ref_rms = float(np.sqrt(np.mean(ref_history**2)))

        if mic_rms < 0.01 or ref_rms < 0.01:
            return self.estimated_delay_samples

        block_len = len(mic_block)
        if len(ref_history) < block_len + self.max_delay_samples:
            return self.estimated_delay_samples

        # Cross-correlate valid search range
        search_window = ref_history[: self.max_delay_samples + block_len]
        corr = signal.correlate(search_window, mic_block, mode="valid")

        if len(corr) == 0:
            return self.estimated_delay_samples

        peak_idx = int(np.argmax(corr))
        max_corr = float(corr[peak_idx])
        norm = (mic_rms * ref_rms * block_len) + 1e-8
        conf = max_corr / norm

        if self.min_delay_samples <= peak_idx <= self.max_delay_samples and conf > 0.15:
            # Smooth delay estimate
            alpha = 0.7
            self.estimated_delay_samples = int(
                alpha * self.estimated_delay_samples + (1.0 - alpha) * peak_idx
            )
            self.confidence = float(conf)

        return self.estimated_delay_samples


class PartitionedBlockAEC:
    """Partitioned Block Frequency-Domain Adaptive Filter (PBFDAF) with DTD and RES."""

    def __init__(
        self,
        block_size: int = 480,
        num_partitions: int = 6,
        step_size: float = 0.25,
        leak: float = 0.9999,
        res_suppression_db: float = 30.0,
    ) -> None:
        self.block_size = block_size
        self.fft_size = 2 * block_size
        self.num_partitions = num_partitions
        self.step_size = step_size
        self.leak = leak
        self.res_linear = float(10.0 ** (-res_suppression_db / 20.0))

        self.num_bins = self.fft_size // 2 + 1
        self.W: ComplexArray = np.zeros((self.num_partitions, self.num_bins), dtype=np.complex64)
        self.X_history: List[ComplexArray] = [
            np.zeros(self.num_bins, dtype=np.complex64) for _ in range(self.num_partitions)
        ]
        self.P_X: FloatArray = np.ones(self.num_bins, dtype=np.float32) * 1e-4
        self.x_old: FloatArray = np.zeros(self.block_size, dtype=np.float32)

        self.p_x_smooth: float = 1e-6
        self.p_d_smooth: float = 1e-6
        self.p_e_smooth: float = 1e-6
        self.p_y_smooth: float = 1e-6
        self.is_double_talk: bool = False
        self.last_erle_db: float = 0.0

        # Coherence tracking buffers for spectral suppression
        self.spec_bins = self.block_size // 2 + 1
        self.S_xx: FloatArray = np.ones(self.spec_bins, dtype=np.float32) * 1e-4
        self.S_dd: FloatArray = np.ones(self.spec_bins, dtype=np.float32) * 1e-4
        self.S_xd: ComplexArray = np.zeros(self.spec_bins, dtype=np.complex64)

    def process_block(self, d_block: FloatArray, x_block: FloatArray) -> FloatArray:
        """Process one block of microphone input d and aligned reference input x."""
        ref_rms = float(np.sqrt(np.mean(x_block**2)))

        # Transparent pass-through if speaker reference is silent
        if ref_rms < 1e-4:
            self.x_old = x_block.copy()
            X_zero: ComplexArray = np.fft.rfft(np.zeros(self.fft_size, dtype=np.float32)).astype(
                np.complex64
            )
            self.X_history.pop()
            self.X_history.insert(0, X_zero)
            self.is_double_talk = False
            self.last_erle_db = 0.0
            return d_block.copy()

        # Step 1: Overlap-save 2B-point input for reference: [x_old, x_block]
        x_2b = np.concatenate([self.x_old, x_block])
        self.x_old = x_block.copy()

        X_curr: ComplexArray = np.fft.rfft(x_2b).astype(np.complex64)
        self.X_history.pop()
        self.X_history.insert(0, X_curr)

        # Step 2: Compute estimated echo Y = sum_{k=0}^{K-1} W[k] * X_history[k]
        Y_freq = np.zeros(self.num_bins, dtype=np.complex64)
        for k in range(self.num_partitions):
            Y_freq += self.W[k] * self.X_history[k]

        y_full = np.fft.irfft(Y_freq, n=self.fft_size)
        y_hat: FloatArray = y_full[self.block_size :].astype(np.float32)

        # Step 3: Compute linear error: e[n] = d[n] - y_hat[n]
        e_linear: FloatArray = (d_block - y_hat).astype(np.float32)

        # Step 4: Power tracking & Double-Talk Detection (DTD)
        pow_x = float(np.mean(x_block**2))
        pow_d = float(np.mean(d_block**2))
        pow_e = float(np.mean(e_linear**2))
        pow_y = float(np.mean(y_hat**2))

        alpha = 0.8
        self.p_x_smooth = alpha * self.p_x_smooth + (1.0 - alpha) * pow_x
        self.p_d_smooth = alpha * self.p_d_smooth + (1.0 - alpha) * pow_d
        self.p_e_smooth = alpha * self.p_e_smooth + (1.0 - alpha) * pow_e
        self.p_y_smooth = alpha * self.p_y_smooth + (1.0 - alpha) * pow_y

        corr_dy = (
            float(np.dot(d_block, y_hat)) / (math.sqrt(pow_d * pow_y) + 1e-8)
            if (pow_d > 1e-8 and pow_y > 1e-8)
            else 0.0
        )

        is_dt = False
        if pow_d > 1e-4:
            if pow_y > 1e-6:
                if pow_e > (0.4 * pow_y) and corr_dy < 0.8:
                    is_dt = True
            elif pow_d > 0.01 and pow_x < 0.001:
                is_dt = True
        self.is_double_talk = is_dt

        # Step 5: Update filter weights (PBFDAF gradient constraint)
        e_padded = np.pad(e_linear, (self.block_size, 0))
        E_freq = np.fft.rfft(e_padded)

        for k in range(self.num_partitions):
            self.P_X = (0.9 * self.P_X + 0.1 * (np.abs(self.X_history[k]) ** 2)).astype(np.float32)

        norm_factor = self.P_X * float(self.fft_size) + 1e-2
        adapt_rate = self.step_size if not self.is_double_talk else (self.step_size * 0.05)

        for k in range(self.num_partitions):
            grad_freq = (np.conj(self.X_history[k]) * E_freq) / norm_factor
            grad_time = np.fft.irfft(grad_freq, n=self.fft_size)
            grad_time[self.block_size :] = 0.0
            grad_constrained = np.fft.rfft(grad_time)
            self.W[k] = (self.leak * self.W[k] + adapt_rate * grad_constrained).astype(np.complex64)
            # Bound weights for unconditional numerical stability
            mag = np.abs(self.W[k])
            self.W[k] = np.where(mag > 2.0, self.W[k] / (mag + 1e-6) * 2.0, self.W[k]).astype(
                np.complex64
            )

        # Step 6: Coherence-Based Non-Linear Residual Echo Suppression (RES)
        D_freq = np.fft.rfft(d_block)
        X_freq = np.fft.rfft(x_block)

        alpha_spec = 0.7
        self.S_xx = (alpha_spec * self.S_xx + (1.0 - alpha_spec) * (np.abs(X_freq) ** 2)).astype(
            np.float32
        )
        self.S_dd = (alpha_spec * self.S_dd + (1.0 - alpha_spec) * (np.abs(D_freq) ** 2)).astype(
            np.float32
        )
        self.S_xd = (
            alpha_spec * self.S_xd + (1.0 - alpha_spec) * (X_freq * np.conj(D_freq))
        ).astype(np.complex64)

        gamma2 = (np.abs(self.S_xd) ** 2) / (self.S_xx * self.S_dd + 1e-6)
        gamma2 = np.clip(gamma2, 0.0, 1.0)
        mean_coherence = float(np.mean(gamma2))

        if mean_coherence < 0.3 and pow_d > 1e-3:
            self.is_double_talk = True

        spec_gain = np.maximum(self.res_linear, 1.0 - np.sqrt(gamma2))
        spec_gain = np.clip(spec_gain, self.res_linear, 1.0)

        E_block_freq = np.fft.rfft(e_linear)
        E_clean_freq = E_block_freq * spec_gain
        e_clean: FloatArray = np.fft.irfft(E_clean_freq, n=self.block_size).astype(np.float32)

        clean_pow = float(np.mean(e_clean**2))
        erle_ratio = pow_d / (clean_pow + 1e-8)
        self.last_erle_db = float(10.0 * np.log10(max(1.0, erle_ratio)))

        return e_clean


class AudioEchoCanceller:
    """Comprehensive Acoustic Echo Canceller integrating loopback capture and adaptive DSP."""

    def __init__(
        self,
        sample_rate: Optional[int] = None,
        block_size: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self.sample_rate = sample_rate or settings.AUDIO_INPUT_SAMPLE_RATE
        chunk_ms = settings.AUDIO_INPUT_CHUNK_MS
        self.block_size = block_size or int(self.sample_rate * (chunk_ms / 1000.0))
        self.enabled = enabled if enabled is not None else settings.AUDIO_AEC_ENABLED

        self.loopback = WasapiLoopbackStream(target_sample_rate=self.sample_rate)
        self.delay_estimator = AcousticDelayEstimator(
            sample_rate=self.sample_rate,
            max_search_ms=settings.AUDIO_AEC_DELAY_MAX_MS,
        )
        self.filter = PartitionedBlockAEC(
            block_size=self.block_size,
            num_partitions=settings.AUDIO_AEC_PARTITIONS,
            step_size=settings.AUDIO_AEC_STEP_SIZE,
            res_suppression_db=settings.AUDIO_AEC_SUPPRESSION_DB,
        )

        self._diagnostic_recording = False
        self._diag_frames_remaining = 0
        self._diag_mic_chunks: List[bytes] = []
        self._diag_ref_chunks: List[bytes] = []
        self._diag_clean_chunks: List[bytes] = []
        self._diag_out_dir: Optional[Path] = None

    def start(self) -> None:
        """Start the AEC reference stream capture worker."""
        if not self.enabled:
            return
        try:
            self.loopback.start()
        except Exception as err:
            logger.debug(f"AEC loopback initialization failed, falling back to bypass: {err}")

    def stop(self) -> None:
        """Stop the AEC reference stream capture worker."""
        self.loopback.stop()

    def process_pcm16_chunk(self, pcm_bytes: bytes) -> bytes:
        """Process incoming 16-bit PCM microphone samples and return echo-cancelled PCM samples."""
        if not self.enabled or not pcm_bytes:
            return pcm_bytes

        # Convert raw PCM bytes to float32 normalized [-1.0, 1.0]
        sample_count = len(pcm_bytes) // 2
        if sample_count == 0:
            return pcm_bytes

        # Check if reference stream has energy; if silent, bypass with 100% byte fidelity
        ref_recent = self.loopback.buffer.get_recent(num_samples=self.block_size, delay_offset=0)
        if float(np.sqrt(np.mean(ref_recent**2))) < 1e-4:
            if self._diagnostic_recording and self._diag_frames_remaining > 0:
                self._diag_mic_chunks.append(pcm_bytes)
                self._diag_ref_chunks.append(b"\x00\x00" * sample_count)
                self._diag_clean_chunks.append(pcm_bytes)
                self._diag_frames_remaining -= 1
                if self._diag_frames_remaining <= 0:
                    self._flush_diagnostic_recording()
            return pcm_bytes

        d_samples: FloatArray = (
            np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        )

        # Handle size mismatches gracefully by padding or truncation
        if len(d_samples) != self.block_size:
            if len(d_samples) < self.block_size:
                d_proc: FloatArray = np.pad(
                    d_samples, (0, self.block_size - len(d_samples))
                ).astype(np.float32)
            else:
                d_proc = d_samples[: self.block_size]
        else:
            d_proc = d_samples

        # Retrieve recent reference stream history for delay estimation and alignment
        search_history = self.loopback.buffer.get_recent(
            num_samples=self.block_size * 6,
            delay_offset=0,
        )
        delay_samples = self.delay_estimator.estimate_delay(d_proc, search_history)

        # Retrieve aligned reference audio block
        x_block = self.loopback.buffer.get_recent(
            num_samples=self.block_size,
            delay_offset=delay_samples,
        )

        # Process through partitioned adaptive filter
        clean_proc = self.filter.process_block(d_proc, x_block)
        clean_samples = clean_proc[: len(d_samples)]

        # Clip and convert back to signed 16-bit PCM
        clean_int16 = np.clip(np.round(clean_samples * 32768.0), -32768.0, 32767.0).astype(np.int16)
        out_bytes = clean_int16.tobytes()

        # Handle diagnostic recording if active
        if self._diagnostic_recording and self._diag_frames_remaining > 0:
            self._diag_mic_chunks.append(pcm_bytes)
            ref_int16 = np.clip(x_block[: len(d_samples)] * 32767.0, -32768.0, 32767.0).astype(
                np.int16
            )
            self._diag_ref_chunks.append(ref_int16.tobytes())
            self._diag_clean_chunks.append(out_bytes)
            self._diag_frames_remaining -= 1
            if self._diag_frames_remaining <= 0:
                self._flush_diagnostic_recording()

        return out_bytes

    def enable_diagnostic_recording(
        self, duration_seconds: float = 5.0, output_dir: Optional[Path] = None
    ) -> None:
        """Capture a multi-second diagnostic recording of mic, reference, and clean streams."""
        frame_ms = settings.AUDIO_INPUT_CHUNK_MS
        total_frames = int((duration_seconds * 1000) / frame_ms)
        self._diag_frames_remaining = total_frames
        self._diag_mic_chunks.clear()
        self._diag_ref_chunks.clear()
        self._diag_clean_chunks.clear()
        self._diag_out_dir = (
            output_dir
            or settings.AUDIO_AEC_DIAGNOSTICS_DIR
            or (settings.WORKSPACE_DIR / "jarvis" / "data" / "logs" / "aec_diag")
        )
        self._diagnostic_recording = True
        logger.info(
            f"AEC diagnostic recording initiated for {duration_seconds}s to {self._diag_out_dir}"
        )

    def _flush_diagnostic_recording(self) -> None:
        """Write recorded diagnostic audio streams to WAV files."""
        self._diagnostic_recording = False
        if not self._diag_out_dir:
            return
        try:
            self._diag_out_dir.mkdir(parents=True, exist_ok=True)
            files = [
                ("raw_microphone.wav", b"".join(self._diag_mic_chunks)),
                ("render_reference.wav", b"".join(self._diag_ref_chunks)),
                ("clean_aec_output.wav", b"".join(self._diag_clean_chunks)),
            ]
            for filename, data in files:
                out_path = self._diag_out_dir / filename
                with wave.open(str(out_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self.sample_rate)
                    wf.writeframes(data)
            logger.info(f"AEC diagnostic recordings successfully written to {self._diag_out_dir}")
        except Exception as err:
            logger.debug(f"Failed to write AEC diagnostic files: {err}")

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return diagnostic health and performance telemetry for the AEC subsystem."""
        return {
            "aec_enabled": self.enabled,
            "loopback_running": self.loopback.is_running,
            "loopback_active": self.loopback.is_active,
            "loopback_packets": self.loopback.total_packets_captured,
            "loopback_samples": self.loopback.total_samples_captured,
            "loopback_rms": round(self.loopback.current_rms, 4),
            "estimated_delay_ms": round(
                self.delay_estimator.estimated_delay_samples * 1000 / self.sample_rate, 1
            ),
            "delay_confidence": round(self.delay_estimator.confidence, 2),
            "double_talk_detected": self.filter.is_double_talk,
            "erle_attenuation_db": round(self.filter.last_erle_db, 2),
        }
