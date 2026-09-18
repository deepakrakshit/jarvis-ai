"""JARVIS Deterministic Artifact Pagination Subsystem.

Provides controlled windowing over offloaded Data-Plane artifacts to allow
fine-grained inspection without active context window saturation.
"""

from jarvis.core.context.offloader import DynamicArtifactOffloader
from jarvis.core.context.schemas import PaginatedSlice, PaginationMetadata


class ArtifactPaginator:
    """Manages deterministic line-based pagination over artifact content."""

    def __init__(self, offloader: DynamicArtifactOffloader | None = None) -> None:
        self.offloader = offloader or DynamicArtifactOffloader()

    def paginate_text(
        self,
        text: str,
        artifact_id: str,
        offset: int = 0,
        limit: int = 100,
    ) -> PaginatedSlice:
        """Paginate a text string by line numbers."""
        if offset < 0:
            raise ValueError(f"Offset must be non-negative, got {offset}")
        if limit < 1:
            raise ValueError(f"Limit must be at least 1, got {limit}")

        lines = text.splitlines()
        total_lines = len(lines)

        end_idx = min(offset + limit, total_lines)
        slice_lines = lines[offset:end_idx]
        has_more = end_idx < total_lines
        next_token = str(end_idx) if has_more else None

        meta = PaginationMetadata(
            artifact_id=artifact_id,
            offset=offset,
            limit=limit,
            total_lines=total_lines,
            has_more=has_more,
            next_page_token=next_token,
        )

        return PaginatedSlice(
            metadata=meta,
            lines=slice_lines,
            content="\n".join(slice_lines),
        )

    def paginate_artifact(
        self,
        task_id: str,
        artifact_id: str,
        offset: int = 0,
        limit: int = 100,
    ) -> PaginatedSlice:
        """Read and paginate an offloaded artifact by ID."""
        content = self.offloader.read_artifact(task_id, artifact_id)
        return self.paginate_text(
            text=content,
            artifact_id=artifact_id,
            offset=offset,
            limit=limit,
        )
