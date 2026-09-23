"""JARVIS First-Class Artifacts & Sidecar Subsystem.

Implements Section 40 of ARCHITECTURE.md:
- Structured artifact generation, indexing, and storage
- Metadata and reference emission for LLM context
- Native sidecar process management
"""

from .emitters import ArtifactEmitter, artifact_emitter
from .models import (
    Artifact,
    ArtifactRetention,
    ArtifactSensitivity,
    ArtifactSummary,
    ArtifactType,
)
from .sidecar import SidecarManager, SidecarProcess, sidecar_manager
from .store import ArtifactStore, artifact_store

__all__ = [
    "Artifact",
    "ArtifactType",
    "ArtifactSensitivity",
    "ArtifactRetention",
    "ArtifactSummary",
    "ArtifactStore",
    "artifact_store",
    "ArtifactEmitter",
    "artifact_emitter",
    "SidecarProcess",
    "SidecarManager",
    "sidecar_manager",
]
