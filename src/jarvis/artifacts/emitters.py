"""Structured Artifact Emitters for Common Engineering Outputs.

Implements Section 40 of ARCHITECTURE.md:
- Standardized emission of markdown documents, diffs, plans, reports, and captures
- Consistent metadata association with active tasks and sessions
"""

from typing import Any, Dict, Optional

import yaml  # type: ignore[import-untyped]

from .models import Artifact, ArtifactType
from .store import ArtifactStore, artifact_store


class ArtifactEmitter:
    """Convenience factory for generating and persisting structured artifacts."""

    def __init__(self, store: Optional[ArtifactStore] = None) -> None:
        self.store = store or artifact_store

    def emit_markdown(
        self,
        title: str,
        content: str,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Artifact:
        """Emit a markdown document artifact."""
        return self.store.save_artifact(
            title=title,
            artifact_type=ArtifactType.MARKDOWN,
            content=content,
            session_id=session_id,
            task_id=task_id,
            agent_id=agent_id,
            metadata=metadata,
        )

    def emit_diff(
        self,
        title: str,
        diff_text: str,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Artifact:
        """Emit a unified code diff artifact."""
        return self.store.save_artifact(
            title=title,
            artifact_type=ArtifactType.DIFF,
            content=diff_text,
            session_id=session_id,
            task_id=task_id,
            agent_id=agent_id,
            metadata=metadata,
        )

    def emit_plan(
        self,
        title: str,
        plan_content: str | Dict[str, Any],
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Artifact:
        """Emit an execution plan artifact in YAML serialization."""
        if isinstance(plan_content, dict):
            text = yaml.safe_dump(plan_content, sort_keys=False)
        else:
            text = str(plan_content)

        return self.store.save_artifact(
            title=title,
            artifact_type=ArtifactType.PLAN,
            content=text,
            session_id=session_id,
            task_id=task_id,
            agent_id=agent_id,
            metadata=metadata,
        )

    def emit_report(
        self,
        title: str,
        report_markdown: str,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Artifact:
        """Emit an analytical summary report artifact."""
        return self.store.save_artifact(
            title=title,
            artifact_type=ArtifactType.REPORT,
            content=report_markdown,
            session_id=session_id,
            task_id=task_id,
            agent_id=agent_id,
            metadata=metadata,
        )

    def emit_screenshot(
        self,
        title: str,
        png_bytes: bytes,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Artifact:
        """Emit a visual capture screenshot artifact."""
        return self.store.save_artifact(
            title=title,
            artifact_type=ArtifactType.SCREENSHOT,
            content=png_bytes,
            mime_type="image/png",
            session_id=session_id,
            task_id=task_id,
            agent_id=agent_id,
            metadata=metadata,
        )

    def emit_log(
        self,
        title: str,
        log_text: str,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Artifact:
        """Emit an execution log artifact."""
        return self.store.save_artifact(
            title=title,
            artifact_type=ArtifactType.LOG,
            content=log_text,
            session_id=session_id,
            task_id=task_id,
            agent_id=agent_id,
            metadata=metadata,
        )

    def emit_code_patch(
        self,
        title: str,
        patch_text: str,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Artifact:
        """Emit a git patch artifact."""
        return self.store.save_artifact(
            title=title,
            artifact_type=ArtifactType.CODE_PATCH,
            content=patch_text,
            session_id=session_id,
            task_id=task_id,
            agent_id=agent_id,
            metadata=metadata,
        )


# Default singleton instance
artifact_emitter = ArtifactEmitter()
