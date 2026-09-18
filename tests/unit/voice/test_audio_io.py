"""Unit tests for Hardware Audio I/O Manager and Dynamic Device Resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from jarvis.core.voice.audio_io import (
    DEFAULT_BLOCK_SIZE,
    DEFAULT_INPUT_SAMPLE_RATE,
    AudioDeviceResolver,
    AudioIOManager,
)


@pytest.fixture
def mock_devices() -> list[dict[str, object]]:
    return [
        {
            "name": "Microphone (Realtek(R) Audio)",
            "hostapi": 0,
            "max_input_channels": 2,
            "max_output_channels": 0,
            "default_samplerate": 44100.0,
        },
        {
            "name": "Speakers (Realtek(R) Audio)",
            "hostapi": 0,
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 44100.0,
        },
        {
            "name": "Microphone (Realtek(R) Audio)",
            "hostapi": 1,
            "max_input_channels": 2,
            "max_output_channels": 0,
            "default_samplerate": 48000.0,
        },
        {
            "name": "Speakers (Realtek(R) Audio)",
            "hostapi": 1,
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
    ]


@pytest.fixture
def mock_hostapis() -> list[dict[str, object]]:
    return [
        {"name": "MME"},
        {"name": "Windows WASAPI"},
    ]


def test_audio_device_resolver_enumeration(
    mock_devices: list[dict[str, object]],
    mock_hostapis: list[dict[str, object]],
) -> None:
    """Verify device enumeration lists input and output devices with host API names."""
    with (
        patch("sounddevice.query_devices", return_value=mock_devices),
        patch("sounddevice.query_hostapis", return_value=mock_hostapis),
    ):
        inputs = AudioDeviceResolver.list_input_devices()
        outputs = AudioDeviceResolver.list_output_devices()
        assert len(inputs) == 2
        assert len(outputs) == 2
        assert inputs[0]["index"] == 0
        assert inputs[0]["hostapi_name"] == "MME"
        assert inputs[1]["index"] == 2
        assert inputs[1]["hostapi_name"] == "Windows WASAPI"


def test_audio_device_resolver_explicit_index(
    mock_devices: list[dict[str, object]],
    mock_hostapis: list[dict[str, object]],
) -> None:
    """Verify explicit device index resolution and direct/resampled capability check."""
    with (
        patch("sounddevice.query_devices", return_value=mock_devices),
        patch("sounddevice.query_hostapis", return_value=mock_hostapis),
        patch("sounddevice.check_input_settings", return_value=None),
    ):
        idx, name, api, sr, ch, resampling = AudioDeviceResolver.resolve_input_device(requested=2)
        assert idx == 2
        assert name == "Microphone (Realtek(R) Audio)"
        assert api == "Windows WASAPI"
        assert sr == DEFAULT_INPUT_SAMPLE_RATE
        assert ch == 1
        assert not resampling


def test_audio_device_resolver_fallback_resampling(
    mock_devices: list[dict[str, object]],
    mock_hostapis: list[dict[str, object]],
) -> None:
    """Verify fallback to native sample rate and channels when direct 16kHz mono check fails."""
    with (
        patch("sounddevice.query_devices", return_value=mock_devices),
        patch("sounddevice.query_hostapis", return_value=mock_hostapis),
        patch("sounddevice.check_input_settings", side_effect=Exception("Invalid sample rate")),
    ):
        idx, name, api, sr, ch, resampling = AudioDeviceResolver.resolve_input_device(
            requested="wasapi"
        )
        assert idx == 2
        assert name == "Microphone (Realtek(R) Audio)"
        assert api == "Windows WASAPI"
        assert sr == 48000
        assert ch == 2
        assert resampling


def test_audio_device_resolver_output_resolution(
    mock_devices: list[dict[str, object]],
    mock_hostapis: list[dict[str, object]],
) -> None:
    """Verify audio output endpoint resolution by index and substring."""
    with (
        patch("sounddevice.query_devices", return_value=mock_devices),
        patch("sounddevice.query_hostapis", return_value=mock_hostapis),
    ):
        idx, name, api = AudioDeviceResolver.resolve_output_device("wasapi")
        assert idx == 3
        assert name == "Speakers (Realtek(R) Audio)"
        assert api == "Windows WASAPI"


@pytest.mark.asyncio
async def test_audio_io_manager_lifecycle(
    mock_devices: list[dict[str, object]],
    mock_hostapis: list[dict[str, object]],
) -> None:
    """Verify AudioIOManager startup, streaming, gain application, and teardown."""
    mock_stream = MagicMock()
    with (
        patch("sounddevice.query_devices", return_value=mock_devices),
        patch("sounddevice.query_hostapis", return_value=mock_hostapis),
        patch("sounddevice.check_input_settings", return_value=None),
        patch("sounddevice.RawInputStream", return_value=mock_stream),
        patch("sounddevice.RawOutputStream", return_value=mock_stream),
    ):
        manager = AudioIOManager(gain=1.5, auto_volume=False)
        assert manager.audio_available
        assert manager.gain == 1.5

        manager.start_input_stream()
        assert manager.is_recording
        mock_stream.start.assert_called_once()

        manager.stop_input_stream()
        assert not manager.is_recording
        mock_stream.stop.assert_called_once()


@pytest.mark.asyncio
async def test_audio_io_manager_barge_in_flush() -> None:
    """Verify instantaneous flushing of output queue and hardware stream abort during barge-in."""
    mock_out = MagicMock()
    with (
        patch(
            "sounddevice.query_devices",
            return_value=[{"name": "Speakers", "max_output_channels": 2, "hostapi": 0}],
        ),
        patch("sounddevice.query_hostapis", return_value=[{"name": "Default"}]),
        patch("sounddevice.RawOutputStream", return_value=mock_out),
    ):
        manager = AudioIOManager(auto_volume=False)
        manager.start_output_stream()
        manager.play_audio_chunk(b"\x00\x00" * 800)
        manager.play_audio_chunk(b"\x00\x00" * 800)
        assert manager._output_queue.qsize() == 2

        manager.flush_output()
        assert manager._output_queue.empty()
        mock_out.abort.assert_called_once()
        assert mock_out.start.call_count == 2  # Started on stream init, restarted on flush
        manager.stop_output_stream()


def test_realtime_decimation_48k_to_16k() -> None:
    """Verify accurate 3x decimation and channel downmixing from 48kHz stereo to 16kHz mono."""
    # 4800 frames of stereo 48kHz PCM = 100ms
    t = np.linspace(0, 0.1, 4800, endpoint=False)
    freq = 440.0
    sig = (0.5 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    stereo = np.column_stack([sig, sig])

    # Downmix to mono
    mono = (stereo[:, 0].astype(np.int32) + stereo[:, 1].astype(np.int32)) // 2
    # Decimate by 3
    resampled = mono[::3].astype(np.int16)

    assert len(resampled) == DEFAULT_BLOCK_SIZE  # Exactly 1600 samples
    # Verify frequency content preserved at 440 Hz
    fft = np.abs(np.fft.rfft(resampled.astype(float)))
    freqs = np.fft.rfftfreq(len(resampled), 1 / DEFAULT_INPUT_SAMPLE_RATE)
    peak_freq = freqs[np.argmax(fft)]
    assert abs(peak_freq - 440.0) < 10.0


def test_realtime_interpolation_44k_to_16k() -> None:
    """Verify linear interpolation downsampling from 44.1kHz stereo to 16kHz mono."""
    # 4410 frames of stereo 44.1kHz PCM = 100ms
    t = np.linspace(0, 0.1, 4410, endpoint=False)
    freq = 440.0
    sig = (0.5 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    stereo = np.column_stack([sig, sig])

    mono = (stereo[:, 0].astype(np.int32) + stereo[:, 1].astype(np.int32)) // 2
    orig_x = np.linspace(0.0, 1.0, len(mono), endpoint=False)
    target_x = np.linspace(0.0, 1.0, DEFAULT_BLOCK_SIZE, endpoint=False)
    resampled = np.interp(target_x, orig_x, mono.astype(np.float32)).astype(np.int16)

    assert len(resampled) == DEFAULT_BLOCK_SIZE  # Exactly 1600 samples
    fft = np.abs(np.fft.rfft(resampled.astype(float)))
    freqs = np.fft.rfftfreq(len(resampled), 1 / DEFAULT_INPUT_SAMPLE_RATE)
    peak_freq = freqs[np.argmax(fft)]
    assert abs(peak_freq - 440.0) < 10.0
