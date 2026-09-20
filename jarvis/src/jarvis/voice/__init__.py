"""Voice and Audio Capture/Synthesis Subsystems for JARVIS."""

from .audio_aec import AudioEchoCanceller, PartitionedBlockAEC, WasapiLoopbackStream
from .input_gate import AudioInputGate
from .microphone import (
    AudioEnergyStats,
    MicrophoneCapture,
    get_default_input_device,
    is_genuine_capture_device,
    list_input_devices,
    read_pcm16_audio_stats,
)
from .output_tracker import PlaybackActivityTracker
from .state_machine import AudioSourceType, TaggedAudioFrame, VoiceState, VoiceStateMachine
from .synthesizer import PcmStreamPlayer, VoiceSynthesizer, voice_synthesizer

__all__ = [
    "VoiceState",
    "VoiceStateMachine",
    "AudioSourceType",
    "TaggedAudioFrame",
    "AudioInputGate",
    "PlaybackActivityTracker",
    "VoiceSynthesizer",
    "PcmStreamPlayer",
    "voice_synthesizer",
    "MicrophoneCapture",
    "AudioEchoCanceller",
    "PartitionedBlockAEC",
    "WasapiLoopbackStream",
    "AudioEnergyStats",
    "read_pcm16_audio_stats",
    "list_input_devices",
    "get_default_input_device",
    "is_genuine_capture_device",
]
