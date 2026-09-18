"""JARVIS Native Artifact Tools.

Provides deterministic inspection and windowed pagination over offloaded Data-Plane artifacts.
"""

from pathlib import Path
from typing import Any

from jarvis.core.config import get_settings
from jarvis.core.context.offloader import DynamicArtifactOffloader
from jarvis.core.context.pagination import ArtifactPaginator


def read_artifact_slice(
    task_id: str,
    artifact_id: str,
    offset: int = 0,
    limit: int = 100,
    artifacts_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Read a deterministic window of lines from an offloaded Data-Plane artifact."""
    base_dir = Path(artifacts_dir).resolve() if artifacts_dir else get_settings().ARTIFACTS_DIR
    offloader = DynamicArtifactOffloader(artifacts_dir=base_dir)
    paginator = ArtifactPaginator(offloader=offloader)

    slice_result = paginator.paginate_artifact(
        task_id=task_id,
        artifact_id=artifact_id,
        offset=offset,
        limit=limit,
    )

    return {
        "artifact_id": artifact_id,
        "task_id": task_id,
        "offset": slice_result.metadata.offset,
        "limit": slice_result.metadata.limit,
        "total_lines": slice_result.metadata.total_lines,
        "has_more": slice_result.metadata.has_more,
        "next_page_token": slice_result.metadata.next_page_token,
        "content": slice_result.content,
        "lines_returned": len(slice_result.lines),
    }


def get_artifact_metadata(
    task_id: str,
    artifact_id: str,
    artifacts_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Retrieve metadata and physical characteristics for an offloaded artifact."""
    base_dir = Path(artifacts_dir).resolve() if artifacts_dir else get_settings().ARTIFACTS_DIR
    safe_task_id = "".join(c for c in task_id if c.isalnum() or c in "-_")
    safe_art_id = "".join(c for c in artifact_id if c.isalnum() or c in "-_")

    target_dir = (base_dir / safe_task_id).resolve()
    if not str(target_dir).startswith(str(base_dir.resolve())):
        raise PermissionError(f"Access outside artifacts directory is forbidden: {task_id}")

    candidates = list(target_dir.glob(f"{safe_art_id}.*"))
    if not candidates:
        raise FileNotFoundError(f"Artifact {artifact_id} not found in task {task_id}")

    file_path = candidates[0].resolve()
    stat = file_path.stat()

    return {
        "artifact_id": artifact_id,
        "task_id": task_id,
        "file_name": file_path.name,
        "file_path": str(file_path),
        "size_bytes": stat.st_size,
        "suffix": file_path.suffix,
        "created_timestamp": stat.st_ctime,
        "modified_timestamp": stat.st_mtime,
    }
