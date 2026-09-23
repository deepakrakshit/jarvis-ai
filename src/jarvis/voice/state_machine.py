"""Voice Subsystem Explicit State Machine and Audio Source Tagging.

Enforces Section 2 of Voice Architecture:
Maintains deterministic voice states (IDLE, LISTENING, THINKING, SPEAKING,
INTERRUPTED, STOPPING, ERROR), controls gate transitions, and prevents
assistant audio output from leaking back into the cognitive input pipeline.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from jarvis.telemetry import logger


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class VoiceState(str, Enum):
    """Authoritative lifecycle states for the JARVIS voice subsystem."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class AudioSourceType(str, Enum):
    """Audio frame provenance classifications preventing speaker loopback."""

    USER_MIC = "USER_MIC"
    ASSISTANT_PLAYBACK = "ASSISTANT_PLAYBACK"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


@dataclass
class TaggedAudioFrame:
    """Audio chunk tagged with authoritative source classification and metadata."""

    source: AudioSourceType
    pcm_bytes: bytes
    sample_rate: int
    channels: int
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()


# Authoritative valid state transitions
VALID_VOICE_TRANSITIONS: Dict[VoiceState, Set[VoiceState]] = {
    VoiceState.IDLE: {VoiceState.LISTENING, VoiceState.STOPPING, VoiceState.ERROR},
    VoiceState.LISTENING: {
        VoiceState.THINKING,
        VoiceState.SPEAKING,
        VoiceState.IDLE,
        VoiceState.STOPPING,
        VoiceState.ERROR,
    },
    VoiceState.THINKING: {
        VoiceState.SPEAKING,
        VoiceState.LISTENING,
        VoiceState.INTERRUPTED,
        VoiceState.STOPPING,
        VoiceState.ERROR,
    },
    VoiceState.SPEAKING: {
        VoiceState.LISTENING,
        VoiceState.INTERRUPTED,
        VoiceState.STOPPING,
        VoiceState.ERROR,
    },
    VoiceState.INTERRUPTED: {
        VoiceState.LISTENING,
        VoiceState.SPEAKING,
        VoiceState.IDLE,
        VoiceState.STOPPING,
        VoiceState.ERROR,
    },
    VoiceState.STOPPING: {VoiceState.IDLE, VoiceState.ERROR},
    VoiceState.ERROR: {VoiceState.IDLE, VoiceState.STOPPING, VoiceState.LISTENING},
}


class VoiceStateMachine:
    """Deterministic state machine governing voice interaction lifecycle."""

    def __init__(self, initial_state: VoiceState = VoiceState.IDLE) -> None:
        self._state = initial_state
        self._last_transition_time = time.time()
        self._last_interruption_time: Optional[float] = None
        self._listeners: List[Callable[[VoiceState, VoiceState], Any]] = []

    @property
    def state(self) -> VoiceState:
        """Return the current authoritative voice state."""
        return self._state

    @property
    def last_interruption_time(self) -> Optional[float]:
        """Return timestamp of the most recent interruption, if any."""
        return self._last_interruption_time

    def register_listener(self, listener: Callable[[VoiceState, VoiceState], Any]) -> None:
        """Register a callback invoked whenever the state transitions."""
        self._listeners.append(listener)

    def transition(self, new_state: VoiceState, reason: str = "") -> bool:
        """Attempt to transition to a new voice state according to valid rules."""
        if self._state == new_state:
            return True

        allowed = VALID_VOICE_TRANSITIONS.get(self._state, set())
        if new_state not in allowed:
            logger.warning(
                f"Invalid voice state transition rejected: {self._state.value} -> {new_state.value} "
                f"(reason: {reason})"
            )
            return False

        old_state = self._state
        self._state = new_state
        self._last_transition_time = time.time()

        if new_state == VoiceState.INTERRUPTED:
            self._last_interruption_time = self._last_transition_time

        logger.info(
            f"VoiceState transition: {old_state.value} -> {new_state.value}"
            + (f" [{reason}]" if reason else "")
        )

        for listener in self._listeners:
            try:
                listener(old_state, new_state)
            except Exception as err:
                logger.debug(f"Voice state listener error: {err}")

        return True

    def is_speaking(self) -> bool:
        """Return True if assistant is actively outputting speech."""
        return self._state == VoiceState.SPEAKING

    def is_listening(self) -> bool:
        """Return True if system is actively accepting user microphone input."""
        return self._state == VoiceState.LISTENING

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return current state machine diagnostic snapshot."""
        return {
            "voice_state": self._state.value,
            "seconds_in_state": round(time.time() - self._last_transition_time, 2),
            "last_interruption_time": self._last_interruption_time,
        }
