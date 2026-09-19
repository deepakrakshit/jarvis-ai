"""Data Models for First-Class JARVIS Artifacts.

Implements Section 40 of ARCHITECTURE.md:
"Artifacts are first-class objects.
 Models receive references/metadata where possible instead of uncontrolled giant blobs."
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class ArtifactType(str, Enum):
    """Supported first-class artifact types."""

    FILE = "file"
    IMAGE = "image"
    PDF = "pdf"
    AUDIO = "audio"
    VIDEO = "video"
    SCREENSHOT = "screenshot"
    CODE_PATCH = "code_patch"
    REPORT = "report"
    LOG = "log"
    BROWSER_CAPTURE = "browser_capture"
    MARKDOWN = "markdown"
    DIFF = "diff"
    PLAN = "plan"


class ArtifactSensitivity(str, Enum):
    """Information security sensitivity rating."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"


class ArtifactRetention(str, Enum):
    """Retention lifecycle for stored artifacts."""

    EPHEMERAL = "ephemeral"
    SESSION = "session"
    DURABLE = "durable"


class ArtifactSummary(BaseModel):
    """Public metadata representation suitable for Gateway listings and model references."""

    id: str
    type: str
    title: str
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    session_key: Optional[str] = None
    task_id: Optional[str] = None
    download_mode: str = "bytes"


class Artifact(BaseModel):
    """Full authoritative representation of a stored artifact."""

    artifact_id: str = Field(default_factory=lambda: f"art_{uuid.uuid4().hex[:12]}")
    artifact_type: ArtifactType
    title: str
    mime_type: str
    size_bytes: int = 0
    checksum: str
    storage_path: Path
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    agent_id: Optional[str] = None
    sensitivity: ArtifactSensitivity = ArtifactSensitivity.INTERNAL
    retention: ArtifactRetention = ArtifactRetention.DURABLE
    created_at: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_summary(self) -> ArtifactSummary:
        """Derive a lightweight public summary for wire transmission."""
        return ArtifactSummary(
            id=self.artifact_id,
            type=self.artifact_type.value,
            title=self.title,
            mime_type=self.mime_type,
            size_bytes=self.size_bytes,
            session_key=self.session_id,
            task_id=self.task_id,
            download_mode="bytes",
        )
