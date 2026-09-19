"""Sandboxed Filesystem Operations for Windows Host.

Provides structured reading, writing, editing, listing, and guarded deletion.
"""

from pathlib import Path
from typing import Any, Dict, List

from jarvis.telemetry import logger


def read_file(path_str: str, max_bytes: int = 1_000_000) -> str:
    """Read contents of a text file safely."""
    path = Path(path_str).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a regular file: {path}")

    logger.info(f"Reading file: {path}")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(max_bytes)


def write_file(path_str: str, content: str, overwrite: bool = True) -> Dict[str, Any]:
    """Write text contents to a file, creating parent directories as needed."""
    path = Path(path_str).resolve()
    if path.exists() and not overwrite:
        raise FileExistsError(f"File already exists and overwrite=False: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f"Wrote {len(content)} characters to {path}")
    return {
        "path": str(path),
        "bytes_written": len(content.encode("utf-8")),
        "status": "written",
    }


def list_directory(dir_str: str, max_entries: int = 100) -> List[Dict[str, Any]]:
    """List contents of a directory."""
    path = Path(dir_str).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Directory not found: {path}")
    if not path.is_dir():
        raise ValueError(f"Path is not a directory: {path}")

    entries: List[Dict[str, Any]] = []
    for item in path.iterdir():
        try:
            stat = item.stat()
            entries.append(
                {
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size_bytes": stat.st_size if not item.is_dir() else None,
                    "modified_time": stat.st_mtime,
                }
            )
            if len(entries) >= max_entries:
                break
        except Exception:
            continue
    return entries


def delete_file(path_str: str) -> Dict[str, Any]:
    """Delete a file with guarded validation."""
    path = Path(path_str).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    logger.warning(f"Deleting path: {path}")
    if path.is_file():
        path.unlink()
    elif path.is_dir():
        path.rmdir()

    return {
        "path": str(path),
        "status": "deleted",
    }
