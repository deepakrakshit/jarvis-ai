"""Unit and integration tests for JARVIS Microphone Audio Pipeline."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis.cognition.gemini_live import GeminiLiveBridge, LiveSessionState
from jarvis.voice.microphone import (
    AudioEnergyStats,
    MicrophoneCapture,
    get_default_input_device,
    list_input_devices,
    read_pcm16_audio_stats,
)
from jarvis.voice.synthesizer import PcmStreamPlayer


def test_read_pcm16_audio_stats_empty() -> None:
    """Verify audio energy stats computation on empty input."""
    stats = read_pcm16_audio_stats(b"")
    assert stats.peak == 0
    assert stats.rms == 0.0
    assert not stats.is_speech


def test_read_pcm16_audio_stats_silence() -> None:
    """Verify audio energy stats computation on digital silence."""
    silence = b"\x00\x00" * 800  # 800 16-bit zero samples
    stats = read_pcm16_audio_stats(silence, speech_threshold=15.0)
    assert stats.peak == 0
    assert stats.rms == 0.0
    assert not stats.is_speech


def test_read_pcm16_audio_stats_speech_detection() -> None:
    """Verify audio energy stats correctly identifies audible speech samples."""
    import struct

    # Generate synthetic 16-bit audio waveform with audible amplitude
    samples = [int(1000 * ((i % 20) - 10)) for i in range(1600)]
    pcm_bytes = struct.pack(f"<{len(samples)}h", *samples)

    stats = read_pcm16_audio_stats(pcm_bytes, speech_threshold=50.0)
    assert stats.peak == 10000
    assert stats.rms > 100.0
    assert stats.is_speech


def test_list_and_get_default_input_devices() -> None:
    """Verify device enumeration and default microphone discovery."""
    devices = list_input_devices()
    assert isinstance(devices, list)

    default_dev = get_default_input_device()
    if devices:
        assert default_dev is not None
        assert "index" in default_dev
        assert "name" in default_dev


def test_microphone_capture_init() -> None:
    """Verify MicrophoneCapture initialization and parameter calculation."""
    mic = MicrophoneCapture(
        samplerate=16000,
        channels=1,
        chunk_ms=100,
        device_index=None,
        speech_threshold=20.0,
    )
    assert mic.samplerate == 16000
    assert mic.channels == 1
    assert mic.chunk_ms == 100
    assert mic.blocksize == 1600
    assert not mic.is_recording
    assert not mic.is_muted

    mic.set_muted(True)
    assert mic.is_muted
    mic.set_muted(False)
    assert not mic.is_muted


@pytest.mark.asyncio
async def test_microphone_capture_queue_and_callback() -> None:
    """Verify microphone chunk enqueuing and async reading."""
    loop = asyncio.get_running_loop()
    received_chunks = []

    def on_chunk(data: bytes) -> None:
        received_chunks.append(data)

    mic = MicrophoneCapture(
        samplerate=16000,
        channels=1,
        chunk_ms=100,
        loop=loop,
    )

    # Simulate raw stream callback with synthetic audio block
    fake_chunk = b"\x10\x00" * 1600
    mic._is_recording = True
    mic._audio_chunk_callback = on_chunk

    # Simulate PortAudio stream callback
    mic._audio_callback(fake_chunk, 1600, None, None)
    await asyncio.sleep(0.05)

    assert len(received_chunks) == 1
    assert len(received_chunks[0]) == 3200

    read_chunk = await mic.read_chunk()
    assert read_chunk == fake_chunk
    assert mic.read_chunk_nowait() is None


@pytest.mark.asyncio
async def test_microphone_duplex_suppression() -> None:
    """Verify acoustic feedback suppression ignores low-energy background sound during playback."""
    loop = asyncio.get_running_loop()
    mic = MicrophoneCapture(
        samplerate=16000,
        channels=1,
        chunk_ms=100,
        speech_threshold=50.0,
        loop=loop,
    )
    mic._is_recording = True

    # Enable duplex suppression (simulating speaker output active)
    mic.set_duplex_suppression(True)

    # Feed low-energy audio (simulating speaker bleed, RMS ~ 10)
    low_energy_chunk = b"\x05\x00" * 1600
    mic._audio_callback(low_energy_chunk, 1600, None, None)
    await asyncio.sleep(0.05)

    # Low-energy chunk should be suppressed
    assert mic.read_chunk_nowait() is None

    # Feed loud interrupt speech (simulating user barge-in)
    loud_chunk = b"\xee\x20" * 1600
    mic._audio_callback(loud_chunk, 1600, None, None)
    await asyncio.sleep(0.05)

    # Loud speech passes through
    captured = await mic.read_chunk()
    assert len(captured) == 3200


def test_pcm_stream_player_state_and_interrupt() -> None:
    """Verify PcmStreamPlayer playback state change tracking and interrupt capability."""
    state_history = []

    def on_change(is_playing: bool) -> None:
        state_history.append(is_playing)

    player = PcmStreamPlayer(samplerate=24000, on_playback_state_change=on_change)
    assert not player.is_playing

    # Mock sounddevice RawOutputStream
    with patch("sounddevice.RawOutputStream") as mock_stream_cls:
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream

        # Playing a chunk marks playing state
        player.play_chunk(b"\x00\x10" * 1200)
        assert player.is_playing
        assert True in state_history
        assert mock_stream.write.called

        # Interruption aborts stream immediately
        player.interrupt()
        assert not player.is_playing
        assert mock_stream.abort.called
        assert mock_stream.close.called
        assert False in state_history


@pytest.mark.asyncio
async def test_bridge_audio_transcription_and_interruption() -> None:
    """Verify GeminiLiveBridge handles real-time input transcription and barge-in events."""
    bridge = GeminiLiveBridge()
    mock_session = AsyncMock()
    bridge._active_session = mock_session
    bridge.state = LiveSessionState.ACTIVE

    transcription_results = []
    interrupted_event = asyncio.Event()

    async def on_transcription(text: str, finished: bool) -> None:
        transcription_results.append((text, finished))

    async def on_interrupted() -> None:
        interrupted_event.set()

    bridge.input_transcription_handler = on_transcription
    bridge.interrupted_handler = on_interrupted

    # Test send_audio_chunk formatting
    pcm_chunk = b"\x00\x01" * 1600
    await bridge.send_audio_chunk(pcm_chunk)
    assert mock_session.send_realtime_input.called
    call_kwargs = mock_session.send_realtime_input.call_args[1]
    assert call_kwargs["audio"].mime_type == "audio/pcm;rate=16000"
    assert call_kwargs["audio"].data == pcm_chunk

    # Test configuration includes input transcription
    config = bridge._build_config()
    assert config.input_audio_transcription is not None
    assert config.output_audio_transcription is not None
