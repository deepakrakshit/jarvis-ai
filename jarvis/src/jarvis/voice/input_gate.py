"""Deterministic Half-Duplex Audio Input Gate for JARVIS Voice.

Enforces Section 10 and Section 11 of Voice Architecture:
Gates raw microphone input frames before they reach the Gemini Live transport.
While the assistant is speaking or processing a response turn, all incoming
microphone samples are rejected by the gate to prevent acoustic feedback loops.
"""

from typing import Any, Dict

from jarvis.voice.state_machine import (
    AudioSourceType,
    TaggedAudioFrame,
    VoiceState,
    VoiceStateMachine,
)


class AudioInputGate:
    """Controls forwarding of microphone audio into the cognitive transport."""

    def __init__(self, state_machine: VoiceStateMachine) -> None:
        self.state_machine = state_machine
        self._transmitted_chunks: int = 0
        self._suppressed_chunks: int = 0
        self._last_suppress_reason: str = ""

    @property
    def is_open(self) -> bool:
        """Return True if the gate currently allows microphone audio transmission."""
        return self.state_machine.state == VoiceState.LISTENING

    def should_transmit(self, frame: TaggedAudioFrame) -> bool:
        """Determine whether an audio frame may be forwarded to Gemini Live."""
        # 1. Enforce frame provenance (never transmit assistant playback or system loops)
        if frame.source != AudioSourceType.USER_MIC:
            self._suppressed_chunks += 1
            self._last_suppress_reason = f"Rejected non-mic source: {frame.source.value}"
            return False

        current_state = self.state_machine.state

        # 2. Half-duplex echo prevention: reject mic audio during SPEAKING or THINKING
        if current_state == VoiceState.SPEAKING:
            self._suppressed_chunks += 1
            self._last_suppress_reason = "Assistant playback active (SPEAKING)"
            return False

        if current_state == VoiceState.THINKING:
            self._suppressed_chunks += 1
            self._last_suppress_reason = "Model turn processing (THINKING)"
            return False

        if current_state == VoiceState.LISTENING:
            self._transmitted_chunks += 1
            return True

        # In IDLE, STOPPING, or ERROR states, do not forward audio
        self._suppressed_chunks += 1
        self._last_suppress_reason = f"Inactive state: {current_state.value}"
        return False

    def reset_counters(self) -> None:
        """Reset transmission and suppression metrics."""
        self._transmitted_chunks = 0
        self._suppressed_chunks = 0
        self._last_suppress_reason = ""

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return input gate diagnostic status."""
        return {
            "input_gate_state": "OPEN" if self.is_open else "CLOSED",
            "duplex_mode": "HALF-DUPLEX / ECHO-SAFE",
            "transmitted_chunks": self._transmitted_chunks,
            "suppressed_chunks": self._suppressed_chunks,
            "last_suppress_reason": self._last_suppress_reason,
            "current_voice_state": self.state_machine.state.value,
        }
