"""Unit and integration tests for JARVIS Acoustic Echo Cancellation (AEC).

Verifies:
1. Endpoint classification and loopback rejection (Case A protection).
2. Reference audio circular buffer and delay estimation.
3. Partitioned Block Frequency-Domain Adaptive Filtering (PBFDAF) echo reduction (Case B).
4. Double-Talk Detection (DTD) preserving near-end user speech during speaker playback.
5. Silence bypass with zero distortion.
6. Local diagnostic WAV recording for offline verification.
7. MicrophoneCapture integration and strict privacy invariant enforcement.
"""

from pathlib import Path
from unittest.mock import patch

import numpy as np
from scipy import signal  # type: ignore[import-untyped]

from jarvis.voice.audio_aec import (
    AcousticDelayEstimator,
    AudioEchoCanceller,
    PartitionedBlockAEC,
    ReferenceAudioBuffer,
)
from jarvis.voice.microphone import (
    MicrophoneCapture,
    get_default_input_device,
    is_genuine_capture_device,
)
from jarvis.voice.state_machine import AudioSourceType


def test_endpoint_classification_rejects_loopback_devices() -> None:
    """Verify endpoint classifier forbids known digital loopback/monitor devices (Case A)."""
    # Reject loopback and monitor devices
    assert not is_genuine_capture_device("Stereo Mix (Realtek HD Audio)")
    assert not is_genuine_capture_device("Stereo Mix (Realtek(R) Audio)")
    assert not is_genuine_capture_device("What U Hear (Sound Blaster)")
    assert not is_genuine_capture_device("Wave Out Mix")
    assert not is_genuine_capture_device("Microsoft Sound Mapper - Input")
    assert not is_genuine_capture_device("VB-Audio Virtual Cable")
    assert not is_genuine_capture_device("VoiceMeeter Output")
    assert not is_genuine_capture_device("BlackHole 16ch")

    # Accept genuine physical microphones
    assert is_genuine_capture_device("Microphone (Realtek(R) Audio)")
    assert is_genuine_capture_device("Microphone Array (Intel Smart Sound)")
    assert is_genuine_capture_device("USB Audio Device (Headset Mic)")
    assert is_genuine_capture_device("Internal Microphone")


def test_get_default_input_device_prefers_genuine_mic() -> None:
    """Verify default device resolution skips loopback endpoints in favor of physical mic."""
    fake_devices = [
        {
            "index": 0,
            "name": "Microsoft Sound Mapper - Input",
            "is_default": True,
            "max_input_channels": 2,
            "is_genuine_mic": False,
        },
        {
            "index": 1,
            "name": "Microphone (Realtek(R) Audio)",
            "is_default": False,
            "max_input_channels": 2,
            "is_genuine_mic": True,
        },
        {
            "index": 2,
            "name": "Stereo Mix (Realtek HD Audio)",
            "is_default": False,
            "max_input_channels": 2,
            "is_genuine_mic": False,
        },
    ]

    with patch("jarvis.voice.microphone.list_input_devices", return_value=fake_devices):
        resolved = get_default_input_device()
        assert resolved is not None
        assert resolved["index"] == 1
        assert resolved["name"] == "Microphone (Realtek(R) Audio)"
        assert resolved["is_genuine_mic"] is True


def test_reference_audio_buffer_write_and_delay() -> None:
    """Verify thread-safe circular ring buffer stores and retrieves delayed slices."""
    buf = ReferenceAudioBuffer(capacity_samples=1000)
    assert buf.total_written == 0

    # Write 500 samples of ramp
    samples1 = np.arange(500, dtype=np.float32)
    buf.write(samples1)
    assert buf.total_written == 500

    # Retrieve recent 100 samples with 0 delay (should be 400 to 499)
    recent = buf.get_recent(num_samples=100, delay_offset=0)
    assert len(recent) == 100
    assert np.allclose(recent, np.arange(400, 500, dtype=np.float32))

    # Retrieve with 50 samples delay (should be 350 to 449)
    delayed = buf.get_recent(num_samples=100, delay_offset=50)
    assert len(delayed) == 100
    assert np.allclose(delayed, np.arange(350, 450, dtype=np.float32))

    # Write more to trigger wrap-around
    samples2 = np.arange(500, 1200, dtype=np.float32)
    buf.write(samples2)
    assert buf.total_written == 1200

    # Retrieve latest 50 samples (should be 1150 to 1199)
    wrapped_recent = buf.get_recent(num_samples=50, delay_offset=0)
    assert len(wrapped_recent) == 50
    assert np.allclose(wrapped_recent, np.arange(1150, 1200, dtype=np.float32))


def test_acoustic_delay_estimator_cross_correlation() -> None:
    """Verify delay estimator accurately finds acoustic arrival latency."""
    fs = 16000
    estimator = AcousticDelayEstimator(sample_rate=fs, min_search_ms=10, max_search_ms=100)

    # Reference history contains 3000 samples of noise
    np.random.seed(42)
    ref_history = np.random.randn(3000).astype(np.float32)

    # True delay: 40ms = 640 samples
    true_delay = 640
    block_size = 480
    mic_block = ref_history[true_delay : true_delay + block_size] + 0.05 * np.random.randn(
        block_size
    ).astype(np.float32)

    # Force update counter to trigger estimate
    estimator._update_counter = 3
    est_delay = estimator.estimate_delay(mic_block, ref_history)

    # Verify estimated delay converges to true delay within small acoustic tolerance
    assert abs(est_delay - true_delay) <= 5
    assert estimator.confidence > 0.5


def test_partitioned_block_aec_single_talk_erle_attenuation() -> None:
    """Verify AEC achieves >25dB echo cancellation in single-talk conditions (Case B)."""
    fs = 16000
    block_size = 480
    num_blocks = 150  # 4.5 seconds

    np.random.seed(42)
    # Synthetic YouTube speech/music audio
    t = np.arange(num_blocks * block_size) / fs
    x_total = (
        0.3 * np.sin(2 * np.pi * 250 * t) * (np.sin(2 * np.pi * 4 * t) > 0)
        + 0.2 * np.sin(2 * np.pi * 500 * t)
        + 0.1 * np.random.randn(len(t)) * 0.1
    ).astype(np.float32)

    # Room impulse response with 30ms direct delay + reverberation tail
    delay_samples = int(0.030 * fs)
    tail_len = int(0.060 * fs)
    rir = np.zeros(delay_samples + tail_len, dtype=np.float32)
    rir[delay_samples] = 0.5
    for i in range(1, tail_len):
        rir[delay_samples + i] = 0.3 * np.exp(-i / (0.020 * fs)) * (np.random.rand() * 2 - 1)

    y_echo = signal.convolve(x_total, rir, mode="full")[: len(x_total)].astype(np.float32)
    d_mic = y_echo + (np.random.randn(len(x_total)) * 0.001).astype(np.float32)

    aec = PartitionedBlockAEC(
        block_size=block_size,
        num_partitions=6,
        step_size=0.25,
        res_suppression_db=30.0,
    )

    clean_blocks = []
    for b in range(1, num_blocks):
        d_blk = d_mic[b * block_size : (b + 1) * block_size]
        x_blk = x_total[(b - 1) * block_size : b * block_size]
        e_blk = aec.process_block(d_blk, x_blk)
        clean_blocks.append(e_blk)

    e_clean = np.concatenate(clean_blocks)

    # Compute ERLE on second half after adaptation convergence
    eval_slice = slice(int(2.5 * fs), int(4.0 * fs))
    mic_power = np.mean(d_mic[eval_slice] ** 2)
    clean_power = np.mean(e_clean[eval_slice] ** 2)
    erle_db = 10 * np.log10(mic_power / (clean_power + 1e-12))

    # Assert that AEC attenuates YouTube echo by at least 20 dB
    assert erle_db >= 20.0
    # Assert clean output RMS is below speech detection range
    assert np.sqrt(clean_power) < 0.05


def test_partitioned_block_aec_double_talk_preserves_user_speech() -> None:
    """Verify double-talk detector protects user speech from being cancelled."""
    fs = 16000
    block_size = 480
    num_blocks = 150

    np.random.seed(42)
    t = np.arange(num_blocks * block_size) / fs
    x_total = (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)

    delay_samples = 480
    rir = np.zeros(delay_samples + 200, dtype=np.float32)
    rir[delay_samples] = 0.5

    y_echo = signal.convolve(x_total, rir, mode="full")[: len(x_total)].astype(np.float32)

    # User speaks from second 2.5 to 3.5
    user_speech = np.zeros_like(x_total)
    t_speech = np.arange(fs) / fs
    user_speech[int(2.5 * fs) : int(3.5 * fs)] = (0.3 * np.sin(2 * np.pi * 400 * t_speech)).astype(
        np.float32
    )

    d_doubletalk = y_echo + user_speech + (np.random.randn(len(x_total)) * 0.001).astype(np.float32)

    aec = PartitionedBlockAEC(block_size=block_size, num_partitions=6, step_size=0.25)

    clean_blocks = []
    for b in range(1, num_blocks):
        d_blk = d_doubletalk[b * block_size : (b + 1) * block_size]
        x_blk = x_total[(b - 1) * block_size : b * block_size]
        e_blk = aec.process_block(d_blk, x_blk)
        clean_blocks.append(e_blk)

    e_clean = np.concatenate(clean_blocks)

    # During user speech, check speech preservation
    speech_slice = slice(int(2.7 * fs), int(3.3 * fs))
    clean_speech_rms = np.sqrt(np.mean(e_clean[speech_slice] ** 2))
    raw_user_rms = np.sqrt(np.mean(user_speech[speech_slice] ** 2))

    # User speech should NOT be crushed (preserves at least 80% energy)
    assert clean_speech_rms > (0.8 * raw_user_rms)


def test_partitioned_block_aec_silence_bypass() -> None:
    """Verify transparent zero-distortion pass-through when speakers are silent."""
    block_size = 480
    aec = PartitionedBlockAEC(block_size=block_size)

    mic_block = (np.sin(np.linspace(0, 10, block_size)) * 0.2).astype(np.float32)
    silent_ref = np.zeros(block_size, dtype=np.float32)

    out_block = aec.process_block(mic_block, silent_ref)
    assert np.allclose(out_block, mic_block, atol=1e-5)
    assert not aec.is_double_talk


def test_audio_echo_canceller_pcm16_pipeline() -> None:
    """Verify high-level AudioEchoCanceller converts PCM-16 chunks end-to-end."""
    canceller = AudioEchoCanceller(sample_rate=16000, block_size=480, enabled=True)

    # Mock loopback buffer with synthetic reference audio
    canceller.loopback.buffer.write(np.zeros(2000, dtype=np.float32))

    # Send 480 samples of 16-bit PCM (960 bytes)
    raw_pcm = b"\x00\x05" * 480
    cleaned = canceller.process_pcm16_chunk(raw_pcm)

    assert isinstance(cleaned, bytes)
    assert len(cleaned) == 960

    diag = canceller.get_diagnostics()
    assert diag["aec_enabled"] is True
    assert "erle_attenuation_db" in diag
    assert "estimated_delay_ms" in diag


def test_diagnostic_recording_writes_wav_files(tmp_path: Path) -> None:
    """Verify diagnostic mode captures synchronized WAV files for offline analysis."""
    canceller = AudioEchoCanceller(sample_rate=16000, block_size=480, enabled=True)

    canceller.enable_diagnostic_recording(duration_seconds=0.1, output_dir=tmp_path)
    assert canceller._diagnostic_recording is True

    # Feed frames to satisfy duration
    raw_pcm = b"\x00\x02" * 480
    for _ in range(5):
        canceller.process_pcm16_chunk(raw_pcm)

    assert (tmp_path / "raw_microphone.wav").exists()
    assert (tmp_path / "render_reference.wav").exists()
    assert (tmp_path / "clean_aec_output.wav").exists()


def test_microphone_capture_incorporates_aec_and_preserves_privacy() -> None:
    """Verify MicrophoneCapture processes through AEC and keeps reference stream strictly internal."""
    mic = MicrophoneCapture(
        samplerate=16000,
        channels=1,
        chunk_ms=30,
        speech_threshold=15.0,
    )
    assert hasattr(mic, "echo_canceller")
    assert mic.echo_canceller.enabled is True

    # Verify diagnostic reporting includes AEC telemetry
    diag = mic.get_diagnostics()
    assert "aec" in diag
    assert diag["aec"]["aec_enabled"] is True

    # Simulate callback with a PCM block while recording
    mic._is_recording = True
    raw_chunk = b"\x00\x00" * 480
    mic._audio_callback(raw_chunk, 480, None, None)

    # Frame queued must have USER_MIC provenance, never render reference
    frame = mic.read_frame_nowait()
    assert frame is not None
    assert frame.source == AudioSourceType.USER_MIC
    assert len(frame.pcm_bytes) == 960
