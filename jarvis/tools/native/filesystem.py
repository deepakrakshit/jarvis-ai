"""JARVIS Native Filesystem Tools.

Provides safe file reading, writing, and directory listing confined to the workspace root.
"""

from pathlib import Path
from typing import Any


def resolve_confined_path(file_path: str, workspace_root: Path | str | None = None) -> Path:
    """Resolve and enforce that file_path resides within the workspace boundary."""
    root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
    target = (
        (root / file_path).resolve()
        if not Path(file_path).is_absolute()
        else Path(file_path).resolve()
    )

    try:
        target.relative_to(root)
    except ValueError:
        raise PermissionError(
            f"Access to '{file_path}' outside workspace root '{root}' is forbidden."
        ) from None

    return target


def read_file(file_path: str, workspace_root: Path | str | None = None) -> dict[str, Any]:
    """Read the complete content of a file within the workspace boundary."""
    target = resolve_confined_path(file_path, workspace_root)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if not target.is_file():
        raise IsADirectoryError(f"Target is a directory, not a file: {file_path}")

    disk_bytes = target.stat().st_size
    content = target.read_text(encoding="utf-8", errors="replace")
    content_bytes = len(content.encode("utf-8"))
    return {
        "file_path": str(target),
        "content": content,
        "size_bytes": disk_bytes,
        "disk_bytes": disk_bytes,
        "content_bytes": content_bytes,
    }


def write_file(
    file_path: str, content: str, workspace_root: Path | str | None = None
) -> dict[str, Any]:
    """Write text content to a file within the workspace boundary."""
    target = resolve_confined_path(file_path, workspace_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    disk_bytes = target.stat().st_size
    content_bytes = len(content.encode("utf-8"))
    return {
        "file_path": str(target),
        "bytes_written": disk_bytes,
        "disk_bytes": disk_bytes,
        "content_bytes": content_bytes,
        "status": "success",
    }


def list_dir(dir_path: str = ".", workspace_root: Path | str | None = None) -> dict[str, Any]:
    """List directory contents within the workspace boundary."""
    target = resolve_confined_path(dir_path, workspace_root)
    if not target.exists():
        raise FileNotFoundError(f"Directory not found: {dir_path}")
    if not target.is_dir():
        raise NotADirectoryError(f"Target is not a directory: {dir_path}")

    entries = []
    for item in sorted(target.iterdir()):
        entries.append(
            {
                "name": item.name,
                "is_dir": item.is_dir(),
                "size_bytes": item.stat().st_size if item.is_file() else None,
            }
        )
    return {
        "dir_path": str(target),
        "entries": entries,
        "count": len(entries),
    }


def delete_file(file_path: str, workspace_root: Path | str | None = None) -> dict[str, Any]:
    """Delete a single file within the workspace boundary."""
    target = resolve_confined_path(file_path, workspace_root)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if not target.is_file():
        raise IsADirectoryError(f"Target is a directory, not a file: {file_path}")

    target.unlink()
    return {
        "file_path": str(target),
        "status": "deleted",
    }
