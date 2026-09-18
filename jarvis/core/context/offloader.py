"""JARVIS Dynamic Artifact Offloading Subsystem.

Enforces Control Plane vs Data Plane separation by intercepting large tool observations
and payloads (>1000 tokens or >4KB), persisting them to the Data Plane, and substituting
typed, low-bandwidth ArtifactReference metadata.
"""

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from jarvis.core.config import get_settings
from jarvis.core.context.estimator import TokenEstimator, create_token_estimator
from jarvis.core.context.schemas import ArtifactReference
from jarvis.core.trust.taxonomy import TrustLevel


class DynamicArtifactOffloader:
    """Manages offloading oversized payloads to the Data Plane."""

    def __init__(
        self,
        artifacts_dir: Path | None = None,
        max_inline_tokens: int = 1_000,
        max_inline_bytes: int = 4_096,
        estimator: TokenEstimator | None = None,
    ) -> None:
        self.artifacts_dir = (artifacts_dir or get_settings().ARTIFACTS_DIR).resolve()
        self.max_inline_tokens = max_inline_tokens
        self.max_inline_bytes = max_inline_bytes
        self.estimator = estimator or create_token_estimator()
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def should_offload(
        self,
        content: str | bytes | dict[str, Any] | list[Any],
        estimated_tokens: int | None = None,
    ) -> bool:
        """Determine whether the payload exceeds inline Control Plane thresholds."""
        if isinstance(content, (dict, list)):
            raw_str = json.dumps(content, default=str)
            raw_bytes = raw_str.encode("utf-8")
        elif isinstance(content, str):
            raw_str = content
            raw_bytes = content.encode("utf-8")
        else:
            raw_str = str(content)
            raw_bytes = content

        if len(raw_bytes) > self.max_inline_bytes:
            return True

        tokens = (
            estimated_tokens
            if estimated_tokens is not None
            else self.estimator.estimate_tokens(raw_str)
        )
        return tokens > self.max_inline_tokens

    def offload(
        self,
        task_id: str,
        content: str | bytes | dict[str, Any] | list[Any],
        source_tool: str | None = None,
        trust_level: TrustLevel = TrustLevel.EXTERNAL_UNTRUSTED,
        mime_type: str = "text/plain",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[ArtifactReference, str]:
        """Persist raw payload to Data Plane and produce a low-bandwidth Control Plane reference."""
        # Normalize content to string and bytes
        if isinstance(content, (dict, list)):
            serialized = json.dumps(content, indent=2, default=str)
            raw_bytes = serialized.encode("utf-8")
            if mime_type == "text/plain":
                mime_type = "application/json"
        elif isinstance(content, str):
            serialized = content
            raw_bytes = content.encode("utf-8")
        else:
            raw_bytes = content
            serialized = content.decode("utf-8", errors="replace")

        byte_size = len(raw_bytes)
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        tokens = self.estimator.estimate_tokens(serialized)

        # Guard against path traversal in task_id
        if ".." in task_id or "/" in task_id or "\\" in task_id:
            raise ValueError(f"Path traversal detected in task_id: {task_id}")

        safe_task_id = "".join(c for c in task_id if c.isalnum() or c in "-_")
        if not safe_task_id:
            raise ValueError(f"Invalid task_id: {task_id}")

        task_dir = (self.artifacts_dir / safe_task_id).resolve()

        # Guard against directory traversal
        if not str(task_dir).startswith(str(self.artifacts_dir)):
            raise ValueError(f"Path traversal detected in task_id: {task_id}")

        task_dir.mkdir(parents=True, exist_ok=True)

        artifact_id = f"art-{uuid4()}"
        ext = ".json" if "json" in mime_type else ".txt"
        file_path = task_dir / f"{artifact_id}{ext}"
        file_path.write_bytes(raw_bytes)

        # Generate concise summary for model Control Plane context
        lines = serialized.splitlines()
        line_count = len(lines)
        preview_lines = lines[:10]
        preview_text = "\n".join(preview_lines)
        if line_count > 10:
            preview_text += f"\n... [{line_count - 10} additional lines truncated]"

        summary = (
            f"[Data-Plane Artifact Offloaded: {artifact_id}]\n"
            f"Size: {byte_size} bytes, ~{tokens} tokens, {line_count} lines. MIME: {mime_type}\n"
            f"SHA-256: {sha256}\n"
            f"Preview:\n{preview_text}"
        )

        uri = f"jarvis://artifacts/{safe_task_id}/{artifact_id}"

        ref = ArtifactReference(
            artifact_id=artifact_id,
            task_id=task_id,
            uri=uri,
            file_path=str(file_path),
            mime_type=mime_type,
            byte_size=byte_size,
            sha256_digest=sha256,
            token_estimate=tokens,
            summary=summary,
            trust_level=trust_level,
            source_tool=source_tool,
            metadata=metadata or {},
        )

        replacement_text = (
            f"Observation offloaded to Data Plane (URI: {uri}).\n"
            f"Reference ID: {artifact_id}\n"
            f"{summary}\n"
            f"To read specific lines or slices, invoke the artifact pagination tool."
        )

        return ref, replacement_text

    def read_artifact(self, task_id: str, artifact_id: str) -> str:
        """Read artifact content verifying path containment."""
        safe_task_id = "".join(c for c in task_id if c.isalnum() or c in "-_")
        safe_art_id = "".join(c for c in artifact_id if c.isalnum() or c in "-_")

        task_dir = (self.artifacts_dir / safe_task_id).resolve()
        if not str(task_dir).startswith(str(self.artifacts_dir)):
            raise ValueError(f"Path traversal detected: {task_id}")

        candidates = list(task_dir.glob(f"{safe_art_id}.*"))
        if not candidates:
            raise FileNotFoundError(f"Artifact {artifact_id} not found in task {task_id}")

        target_file = candidates[0].resolve()
        if not str(target_file).startswith(str(task_dir)):
            raise ValueError(f"Path traversal detected in artifact: {artifact_id}")

        return target_file.read_text(encoding="utf-8", errors="replace")
