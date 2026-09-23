"""Persistent Storage and Retrieval for JARVIS Artifacts.

Implements Section 40 of ARCHITECTURE.md:
- Durable filesystem storage with SHA-256 content addressing
- Database indexing across sessions, tasks, and agents
- Efficient byte and text reading
"""

import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from jarvis.config import settings
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger

from .models import (
    Artifact,
    ArtifactRetention,
    ArtifactSensitivity,
    ArtifactSummary,
    ArtifactType,
    utc_now,
)

DEFAULT_MIME_TYPES: Dict[ArtifactType, str] = {
    ArtifactType.MARKDOWN: "text/markdown",
    ArtifactType.DIFF: "text/x-diff",
    ArtifactType.PLAN: "application/yaml",
    ArtifactType.REPORT: "text/markdown",
    ArtifactType.LOG: "text/plain",
    ArtifactType.FILE: "application/octet-stream",
    ArtifactType.IMAGE: "image/png",
    ArtifactType.SCREENSHOT: "image/png",
    ArtifactType.PDF: "application/pdf",
    ArtifactType.AUDIO: "audio/wav",
    ArtifactType.VIDEO: "video/mp4",
    ArtifactType.CODE_PATCH: "text/x-diff",
    ArtifactType.BROWSER_CAPTURE: "image/png",
}

DEFAULT_EXTENSIONS: Dict[ArtifactType, str] = {
    ArtifactType.MARKDOWN: ".md",
    ArtifactType.DIFF: ".diff",
    ArtifactType.PLAN: ".yaml",
    ArtifactType.REPORT: ".md",
    ArtifactType.LOG: ".log",
    ArtifactType.FILE: ".bin",
    ArtifactType.IMAGE: ".png",
    ArtifactType.SCREENSHOT: ".png",
    ArtifactType.PDF: ".pdf",
    ArtifactType.AUDIO: ".wav",
    ArtifactType.VIDEO: ".mp4",
    ArtifactType.CODE_PATCH: ".patch",
    ArtifactType.BROWSER_CAPTURE: ".png",
}


class ArtifactStore:
    """Manages file storage and metadata tracking for all generated artifacts."""

    def __init__(
        self,
        storage_dir: Optional[Path] = None,
        database: Optional[DatabaseEngine] = None,
    ) -> None:
        self.storage_dir = (
            storage_dir or (settings.WORKSPACE_DIR / "data" / "artifacts")
        ).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.db = database or db

    def save_artifact(
        self,
        title: str,
        artifact_type: ArtifactType,
        content: str | bytes,
        mime_type: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        sensitivity: ArtifactSensitivity = ArtifactSensitivity.INTERNAL,
        retention: ArtifactRetention = ArtifactRetention.DURABLE,
        metadata: Optional[Dict[str, Any]] = None,
        artifact_id: Optional[str] = None,
    ) -> Artifact:
        """Write artifact content to disk and register it in the database."""
        art_id = artifact_id or f"art_{uuid.uuid4().hex[:12]}"
        ext = DEFAULT_EXTENSIONS.get(artifact_type, ".dat")
        file_path = (self.storage_dir / f"{art_id}{ext}").resolve()

        # Convert content to bytes for hashing and write
        if isinstance(content, str):
            raw_bytes = content.encode("utf-8")
        else:
            raw_bytes = content

        checksum = hashlib.sha256(raw_bytes).hexdigest()
        size_bytes = len(raw_bytes)
        effective_mime = mime_type or DEFAULT_MIME_TYPES.get(
            artifact_type, "application/octet-stream"
        )

        # Write to disk
        file_path.write_bytes(raw_bytes)

        # Record in database
        self.db.save_artifact(
            artifact_id=art_id,
            session_id=session_id,
            task_id=task_id,
            agent_id=agent_id,
            artifact_type=artifact_type.value,
            title=title,
            mime_type=effective_mime,
            size_bytes=size_bytes,
            checksum=checksum,
            storage_path=str(file_path),
            sensitivity=sensitivity.value,
            retention=retention.value,
            metadata=metadata,
        )

        logger.info(
            f"Stored artifact '{title}' ({art_id}) [{artifact_type.value}, {size_bytes} bytes]"
        )

        return Artifact(
            artifact_id=art_id,
            artifact_type=artifact_type,
            title=title,
            mime_type=effective_mime,
            size_bytes=size_bytes,
            checksum=checksum,
            storage_path=file_path,
            session_id=session_id,
            task_id=task_id,
            agent_id=agent_id,
            sensitivity=sensitivity,
            retention=retention,
            created_at=utc_now(),
            metadata=metadata or {},
        )

    def get_artifact(self, artifact_id: str) -> Optional[Artifact]:
        """Fetch artifact record by identifier."""
        row = self.db.get_artifact(artifact_id)
        if not row:
            return None

        # Parse created_at datetime safely
        try:
            created_at = datetime.fromisoformat(row["created_at"])
        except Exception:
            created_at = utc_now()

        return Artifact(
            artifact_id=row["artifact_id"],
            artifact_type=ArtifactType(row["artifact_type"]),
            title=row["title"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            checksum=row["checksum"],
            storage_path=Path(row["storage_path"]),
            session_id=row["session_id"],
            task_id=row["task_id"],
            agent_id=row["agent_id"],
            sensitivity=ArtifactSensitivity(row["sensitivity"]),
            retention=ArtifactRetention(row["retention"]),
            created_at=created_at,
            metadata=row["metadata"],
        )

    def read_bytes(self, artifact_id: str) -> bytes:
        """Read binary data of an artifact."""
        artifact = self.get_artifact(artifact_id)
        if not artifact:
            raise FileNotFoundError(f"Artifact {artifact_id} does not exist.")
        if not artifact.storage_path.exists():
            raise FileNotFoundError(f"Artifact file on disk {artifact.storage_path} missing.")
        return artifact.storage_path.read_bytes()

    def read_text(self, artifact_id: str) -> str:
        """Read text content of an artifact."""
        artifact = self.get_artifact(artifact_id)
        if not artifact:
            raise FileNotFoundError(f"Artifact {artifact_id} does not exist.")
        if not artifact.storage_path.exists():
            raise FileNotFoundError(f"Artifact file on disk {artifact.storage_path} missing.")
        return artifact.storage_path.read_text(encoding="utf-8")

    def list_artifacts(
        self,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        artifact_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[ArtifactSummary]:
        """List artifact summaries filtered by session, task, or type."""
        rows = self.db.list_artifacts(
            session_id=session_id,
            task_id=task_id,
            artifact_type=artifact_type,
            limit=limit,
        )
        summaries: List[ArtifactSummary] = []
        for r in rows:
            summaries.append(
                ArtifactSummary(
                    id=r["artifact_id"],
                    type=r["artifact_type"],
                    title=r["title"],
                    mime_type=r["mime_type"],
                    size_bytes=r["size_bytes"],
                    session_key=r["session_id"],
                    task_id=r["task_id"],
                    download_mode="bytes",
                )
            )
        return summaries

    def delete_artifact(self, artifact_id: str) -> bool:
        """Delete artifact record and its on-disk payload."""
        artifact = self.get_artifact(artifact_id)
        if artifact and artifact.storage_path.exists():
            try:
                artifact.storage_path.unlink()
            except Exception as err:
                logger.warning(f"Failed to unlink file {artifact.storage_path}: {err}")

        return self.db.delete_artifact(artifact_id)


# Default singleton instance
artifact_store = ArtifactStore()
