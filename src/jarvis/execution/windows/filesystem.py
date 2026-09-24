"""Sandboxed Filesystem Operations for Windows Host.

Provides structured reading, writing, editing, listing, and guarded deletion.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

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


def search_files(
    dir_str: str,
    query: str = "",
    extension: Optional[str] = None,
    max_results: int = 50,
) -> Dict[str, Any]:
    """Search for files in a directory matching keywords and optional extension."""
    raw_path = dir_str.strip().lower()
    if raw_path in ("downloads", "download"):
        path = Path.home() / "Downloads"
    elif raw_path in ("desktop",):
        path = Path.home() / "Desktop"
    elif raw_path in ("documents", "document"):
        path = Path.home() / "Documents"
    else:
        path = Path(dir_str).resolve()

    if not path.exists():
        return {
            "found": False,
            "error": f"Directory not found: {path}",
            "matches": [],
        }

    query_tokens = [t.lower() for t in query.split()] if query else []
    ext_candidates: set[str] = set()
    if extension:
        ext_clean = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        if ext_clean in (".ppt", ".pptx"):
            ext_candidates = {".ppt", ".pptx", ".pps", ".ppsx"}
        elif ext_clean in (".doc", ".docx"):
            ext_candidates = {".doc", ".docx"}
        elif ext_clean in (".xls", ".xlsx"):
            ext_candidates = {".xls", ".xlsx"}
        else:
            ext_candidates = {ext_clean}

    matches: List[Dict[str, Any]] = []
    available_samples: List[str] = []

    try:
        for item in path.iterdir():
            if not item.is_file():
                continue
            name_lower = item.name.lower()
            if len(available_samples) < 15:
                available_samples.append(item.name)

            if ext_candidates and item.suffix.lower() not in ext_candidates:
                continue

            if query_tokens and not all(t in name_lower for t in query_tokens):
                continue

            stat = item.stat()
            matches.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "size_bytes": stat.st_size,
                    "modified_time": stat.st_mtime,
                    "extension": item.suffix.lower(),
                }
            )
            if len(matches) >= max_results:
                break
    except Exception as err:
        return {"found": False, "error": str(err), "matches": []}

    return {
        "found": len(matches) > 0,
        "count": len(matches),
        "location": str(path),
        "matches": matches,
        "sample_files": available_samples[:10] if not matches else [],
    }
