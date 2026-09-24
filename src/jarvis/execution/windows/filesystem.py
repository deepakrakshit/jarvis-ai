"""Sandboxed Filesystem Operations for Windows Host.

Provides structured reading, writing, editing, listing, search, delivery, and guarded deletion.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from jarvis.config import settings
from jarvis.telemetry import logger

# Protected sensitive patterns that cannot be delivered or exposed
FORBIDDEN_FILE_PATTERNS: Set[str] = {
    ".env",
    "sessions.json",
    "conversations.json",
    "id_rsa",
    "id_ed25519",
}

FORBIDDEN_EXTENSIONS: Set[str] = {
    ".pem",
    ".key",
    ".sqlite",
    ".sqlite3",
    ".db",
}


def resolve_path(path_str: str) -> Path:
    """Normalize and resolve user paths, mapping aliases to standard user folders."""
    raw = path_str.strip().strip("\"'").replace("\\", "/")
    raw_lower = raw.lower()

    # Direct alias mapping
    folder_aliases = {
        "downloads": Path.home() / "Downloads",
        "download": Path.home() / "Downloads",
        "desktop": Path.home() / "Desktop",
        "desk": Path.home() / "Desktop",
        "documents": Path.home() / "Documents",
        "document": Path.home() / "Documents",
        "docs": Path.home() / "Documents",
        "pictures": Path.home() / "Pictures",
        "videos": Path.home() / "Videos",
        "music": Path.home() / "Music",
    }

    if raw_lower in folder_aliases:
        return folder_aliases[raw_lower]

    for alias, base_dir in folder_aliases.items():
        if raw_lower.startswith(f"{alias}/"):
            subpath = raw[len(alias) + 1 :]
            return (base_dir / subpath).resolve()

    if raw.startswith("~"):
        return Path(raw).expanduser().resolve()

    # Try resolving directly
    p = Path(path_str).resolve()
    if p.exists():
        return p

    # Fallback search across standard directories if user passed a bare filename
    for base_dir in (
        Path.home() / "Downloads",
        Path.home() / "Documents",
        Path.home() / "Desktop",
    ):
        candidate = (base_dir / path_str).resolve()
        if candidate.exists():
            return candidate

    return p


def is_safe_artifact_path(path: Path) -> Tuple[bool, Optional[str]]:
    """Verify that a path is safe for artifact export and not a private credential."""
    name_lower = path.name.lower()

    for pattern in FORBIDDEN_FILE_PATTERNS:
        if pattern in name_lower:
            return False, f"Access to sensitive file '{path.name}' is strictly prohibited."

    if path.suffix.lower() in FORBIDDEN_EXTENSIONS:
        return (
            False,
            f"Files with extension '{path.suffix}' contain private secrets and cannot be delivered.",
        )

    # Check maximum file size for Telegram delivery (default: 50 MB)
    max_bytes = getattr(settings, "TELEGRAM_MAX_UPLOAD_BYTES", 52428800)
    try:
        size = path.stat().st_size
        if size > max_bytes:
            size_mb = size / (1024 * 1024)
            max_mb = max_bytes / (1024 * 1024)
            return (
                False,
                f"File '{path.name}' is {size_mb:.1f} MB, exceeding the {max_mb:.0f} MB upload limit.",
            )
    except Exception as stat_err:
        return False, f"Could not inspect file attributes: {stat_err}"

    return True, None


def read_file(path_str: str, max_bytes: int = 1_000_000) -> str:
    """Read contents of a text file safely."""
    path = resolve_path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a regular file: {path}")

    logger.info(f"Reading file: {path}")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(max_bytes)


def write_file(path_str: str, content: str, overwrite: bool = True) -> Dict[str, Any]:
    """Write text contents to a file, creating parent directories as needed."""
    path = resolve_path(path_str)
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
    path = resolve_path(dir_str)
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
    path = resolve_path(path_str)
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
    path = resolve_path(dir_str)
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


def deliver_file(path_str: str, caption: Optional[str] = None) -> Dict[str, Any]:
    """Validate a host file and package it as an authorized deliverable artifact."""
    path = resolve_path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"File not found on host: {path_str}")
    if not path.is_file():
        raise ValueError(f"Path is a directory, not a deliverable file: {path_str}")

    safe, reason = is_safe_artifact_path(path)
    if not safe:
        raise PermissionError(reason or "File access prohibited by security policy.")

    stat = path.stat()
    mime, _ = mimetypes.guess_type(path.name)
    mime_type = mime or "application/octet-stream"
    ext = path.suffix.lower()
    kind = "photo" if ext in (".png", ".jpg", ".jpeg", ".webp") else "document"

    size_display = (
        f"{stat.st_size / (1024 * 1024):.1f} MB"
        if stat.st_size >= 1024 * 1024
        else f"{stat.st_size / 1024:.1f} KB"
    )

    logger.info(f"Authorized deliverable artifact created: {path} ({size_display})")
    return {
        "artifact_id": f"art-{uuid4().hex[:8]}",
        "kind": kind,
        "name": path.name,
        "path": str(path),
        "size_bytes": stat.st_size,
        "size_display": size_display,
        "mime_type": mime_type,
        "extension": ext,
        "caption": caption or path.name,
        "authorized": True,
        "delivered": True,
    }
