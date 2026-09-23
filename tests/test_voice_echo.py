"""Unit and integration tests for JARVIS Voice Echo Prevention Architecture.

Verifies:
1. Voice state machine deterministic transitions and invalid transition rejection.
2. Audio source provenance classification (USER_MIC vs ASSISTANT_PLAYBACK vs SYSTEM).
3. Half-duplex AudioInputGate suppression while SPEAKING or THINKING.
4. PlaybackActivityTracker separation of generation completion from physical audio drain.
5. PcmStreamPlayer drain holding, acoustic margin decay, and barge-in / interruption handling.
6. 20 consecutive simulated clean multi-turn interactions with zero acoustic loopback.
"""

import time
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from jarvis.voice.input_gate import AudioInputGate
from jarvis.voice.output_tracker import PlaybackActivityTracker
from jarvis.voice.state_machine import (
    AudioSourceType,
    TaggedAudioFrame,
    VoiceState,
    VoiceStateMachine,
)
from jarvis.voice.synthesizer import PcmStreamPlayer


def test_voice_state_machine_valid_transitions() -> None:
    """Verify state machine follows deterministic lifecycle rules."""
    sm = VoiceStateMachine(initial_state=VoiceState.IDLE)
    assert not sm.is_speaking()
    assert not sm.is_listening()
    assert sm.get_diagnostics()["voice_state"] == "IDLE"

    # IDLE -> LISTENING
    assert sm.transition(VoiceState.LISTENING, reason="Session connected")
    assert sm.is_listening()
    assert sm.get_diagnostics()["voice_state"] == "LISTENING"

    # LISTENING -> THINKING
    assert sm.transition(VoiceState.THINKING, reason="User input received")
    assert sm.get_diagnostics()["voice_state"] == "THINKING"

    # THINKING -> SPEAKING
    assert sm.transition(VoiceState.SPEAKING, reason="Model response audio")
    assert sm.is_speaking()
    assert sm.get_diagnostics()["voice_state"] == "SPEAKING"

    # SPEAKING -> LISTENING
    assert sm.transition(VoiceState.LISTENING, reason="Playback drained")
    assert sm.is_listening()
    assert sm.get_diagnostics()["voice_state"] == "LISTENING"

    # LISTENING -> STOPPING -> IDLE
    assert sm.transition(VoiceState.STOPPING, reason="Shutdown")
    assert sm.get_diagnostics()["voice_state"] == "STOPPING"
    assert sm.transition(VoiceState.IDLE, reason="Stopped")
    assert sm.get_diagnostics()["voice_state"] == "IDLE"


def test_voice_state_machine_invalid_transitions_rejected() -> None:
    """Verify invalid transitions are rejected without altering state."""
    sm = VoiceStateMachine(initial_state=VoiceState.IDLE)

    # Cannot transition directly from IDLE to SPEAKING
    assert not sm.transition(VoiceState.SPEAKING, reason="Invalid direct transition")
    assert sm.get_diagnostics()["voice_state"] == "IDLE"

    # Cannot transition directly from IDLE to THINKING
    assert not sm.transition(VoiceState.THINKING, reason="Invalid direct transition")
    assert sm.get_diagnostics()["voice_state"] == "IDLE"


def test_voice_state_machine_listener_callback() -> None:
    """Verify state transition listeners are invoked on state change."""
    sm = VoiceStateMachine(initial_state=VoiceState.IDLE)
    transitions: List[str] = []

    def on_change(old_s: VoiceState, new_s: VoiceState) -> None:
        transitions.append(f"{old_s.value}->{new_s.value}")

    sm.register_listener(on_change)
    sm.transition(VoiceState.LISTENING)
    sm.transition(VoiceState.THINKING)
    sm.transition(VoiceState.SPEAKING)

    assert transitions == [
        "IDLE->LISTENING",
        "LISTENING->THINKING",
        "THINKING->SPEAKING",
    ]


def test_tagged_audio_frame_provenance() -> None:
    """Verify audio frame tagging preserves provenance and audio metadata."""
    pcm = b"\x10\x00" * 480  # 30ms at 16kHz mono
    frame = TaggedAudioFrame(
        source=AudioSourceType.USER_MIC,
        pcm_bytes=pcm,
        sample_rate=16000,
        channels=1,
    )
    assert frame.source == AudioSourceType.USER_MIC
    assert len(frame.pcm_bytes) == 960
    assert frame.sample_rate == 16000
    assert frame.channels == 1
    assert frame.timestamp > 0.0


def test_input_gate_rejects_non_mic_audio() -> None:
    """Verify input gate strictly rejects playback or system loopback frames."""
    sm = VoiceStateMachine(initial_state=VoiceState.LISTENING)
    gate = AudioInputGate(state_machine=sm)

    playback_frame = TaggedAudioFrame(
        source=AudioSourceType.ASSISTANT_PLAYBACK,
        pcm_bytes=b"\x00\x01" * 480,
        sample_rate=24000,
        channels=1,
    )
    system_frame = TaggedAudioFrame(
        source=AudioSourceType.SYSTEM,
        pcm_bytes=b"\x00\x02" * 480,
        sample_rate=16000,
        channels=1,
    )

    assert not gate.should_transmit(playback_frame)
    assert not gate.should_transmit(system_frame)
    assert gate.get_diagnostics()["suppressed_chunks"] == 2


def test_input_gate_half_duplex_suppression_while_speaking_or_thinking() -> None:
    """Verify input gate rejects microphone frames while assistant is SPEAKING or THINKING."""
    sm = VoiceStateMachine(initial_state=VoiceState.LISTENING)
    gate = AudioInputGate(state_machine=sm)

    mic_frame = TaggedAudioFrame(
        source=AudioSourceType.USER_MIC,
        pcm_bytes=b"\x05\x00" * 480,
        sample_rate=16000,
        channels=1,
    )

    # In LISTENING: Gate is open
    assert gate.is_open
    assert gate.should_transmit(mic_frame)

    # In THINKING: Gate is closed
    sm.transition(VoiceState.THINKING)
    assert not gate.is_open
    assert not gate.should_transmit(mic_frame)

    # In SPEAKING: Gate is closed (prevents acoustic feedback from speaker output)
    sm.transition(VoiceState.SPEAKING)
    assert not gate.is_open
    assert not gate.should_transmit(mic_frame)

    # When transitioning back to LISTENING: Gate re-opens
    sm.transition(VoiceState.LISTENING)
    assert gate.is_open
    assert gate.should_transmit(mic_frame)


def test_playback_activity_tracker_math_and_drain() -> None:
    """Verify PlaybackActivityTracker calculates exact durations and enforces drain hold margin."""
    tracker = PlaybackActivityTracker(drain_hold_ms=50)
    tracker.mark_stream_opened()

    # Feed 24000 samples @ 24kHz mono 16-bit (2 bytes per sample = 48000 bytes = 1.0 second = 1000ms)
    chunk = b"\x00\x05" * 24000
    tracker.mark_chunk(chunk, sample_rate=24000)

    assert tracker.chunks_received == 1
    assert pytest.approx(tracker.audio_ms, 0.1) == 1000.0

    # Model finishes generation immediately (network transfer complete)
    tracker.mark_generation_finished()
    assert tracker.generation_finished

    # But physical playback is still in progress (0ms elapsed, 1000ms remaining)
    assert not tracker.is_playback_drained()

    # Simulate physical time elapsed past total audio + drain hold margin
    tracker._playback_started_at = time.time() - 1.2
    tracker._last_chunk_at = time.time() - 0.2  # 200ms ago (> 50ms hold)

    assert tracker.remaining_playback_ms() == 0.0
    assert tracker.is_playback_drained()


@pytest.mark.asyncio
async def test_pcm_stream_player_drain_and_barge_in() -> None:
    """Verify PcmStreamPlayer wait_until_drained and barge-in / interruption handling."""
    player = PcmStreamPlayer(samplerate=24000)
    player.tracker.drain_hold_ms = 30

    with patch("sounddevice.RawOutputStream") as mock_out:
        mock_stream = MagicMock()
        mock_out.return_value = mock_stream

        player.start_stream()
        # Feed 2400 samples (100ms)
        player.play_chunk(b"\x00\x01" * 2400)
        assert player.is_playing

        # Mark generation finished
        player.mark_generation_finished()

        # Simulate elapsed time
        player.tracker._playback_started_at = time.time() - 0.2
        player.tracker._last_chunk_at = time.time() - 0.1

        # Await drain
        drained = await player.wait_until_drained(poll_interval=0.01, timeout=1.0)
        assert drained
        assert not player.is_playing

        # Test interruption / barge-in
        player.play_chunk(b"\x00\x01" * 2400)
        assert player.is_playing
        player.interrupt()
        assert not player.is_playing
        assert mock_stream.abort.called


@pytest.mark.asyncio
async def test_twenty_consecutive_clean_turns_no_echo_loop() -> None:
    """Simulate 20 consecutive multi-turn voice dialogues to prove acoustic echo loop is eradicated.

    In each turn:
    1. Operator speaks into microphone (captured by gate in LISTENING).
    2. Cognitive bridge processes turn (transitions to SPEAKING on first audio chunk).
    3. Assistant speaks: speaker audio radiates into the room and physical microphone picks it up.
    4. Input gate intercepts and drops 100% of the leaked speaker chunks.
    5. Generation completes; player drains audio hardware buffer + acoustic margin.
    6. System transitions back to LISTENING; next turn proceeds cleanly.
    """
    sm = VoiceStateMachine(initial_state=VoiceState.IDLE)
    gate = AudioInputGate(state_machine=sm)
    player = PcmStreamPlayer(samplerate=24000)
    player.tracker.drain_hold_ms = 10

    # Live connection established
    sm.transition(VoiceState.LISTENING)

    total_turns = 20
    forwarded_to_model: List[bytes] = []
    dropped_speaker_echo_count = 0

    for turn_idx in range(1, total_turns + 1):
        # 1. Operator speaks a 30ms voice chunk
        operator_chunk = f"user_turn_{turn_idx}".encode().ljust(960, b"\x01")
        user_frame = TaggedAudioFrame(
            source=AudioSourceType.USER_MIC,
            pcm_bytes=operator_chunk,
            sample_rate=16000,
            channels=1,
        )

        assert gate.is_open, f"Gate must be OPEN for user speech in turn {turn_idx}"
        if gate.should_transmit(user_frame):
            forwarded_to_model.append(user_frame.pcm_bytes)

        # 2. Assistant begins outputting speech
        sm.transition(VoiceState.SPEAKING, reason=f"Model audio for turn {turn_idx}")
        assert not gate.is_open, f"Gate must be CLOSED while speaking in turn {turn_idx}"

        # 3. Simulate assistant emitting audio (3 chunks)
        with patch("sounddevice.RawOutputStream"):
            player.start_stream()
            for _ in range(3):
                assistant_chunk = b"\x00\x02" * 240  # 10ms audio
                player.play_chunk(assistant_chunk)

                # Simulate room acoustic feedback: microphone picks up speaker sound
                leaked_echo_frame = TaggedAudioFrame(
                    source=AudioSourceType.USER_MIC,
                    pcm_bytes=assistant_chunk[:960],
                    sample_rate=16000,
                    channels=1,
                )

                # Gate MUST suppress this leaked audio
                if gate.should_transmit(leaked_echo_frame):
                    forwarded_to_model.append(leaked_echo_frame.pcm_bytes)
                else:
                    dropped_speaker_echo_count += 1

        # 4. Generation completes over transport
        player.mark_generation_finished()

        # Simulate hardware playback drain
        player.tracker._playback_started_at = time.time() - 0.1
        player.tracker._last_chunk_at = time.time() - 0.05
        await player.wait_until_drained(poll_interval=0.005, timeout=0.5)
        player.mark_idle()

        # 5. Transition state machine back to LISTENING
        sm.transition(VoiceState.LISTENING, reason="Playback drained")

    # Assertions across all 20 turns:
    # 1. Exactly 20 user utterances forwarded to the model transport
    assert len(forwarded_to_model) == 20
    for idx, forwarded in enumerate(forwarded_to_model, 1):
        expected_prefix = f"user_turn_{idx}".encode()
        assert forwarded.startswith(expected_prefix)

    # 2. Exactly 60 leaked speaker audio chunks (3 per turn * 20 turns) were blocked by the gate
    assert dropped_speaker_echo_count == 60

    # 3. Final state is LISTENING and ready for next turn
    assert sm.state == VoiceState.LISTENING
    assert gate.is_open

    # 4. Input gate diagnostics confirm echo-safe operation
    diag = gate.get_diagnostics()
    assert diag["duplex_mode"] == "HALF-DUPLEX / ECHO-SAFE"
    assert diag["transmitted_chunks"] == 20
    assert diag["suppressed_chunks"] == 60
