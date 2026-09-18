"""JARVIS Sandbox Security Guard and Path Confinement.

Provides defensive validation of container mounts, host path boundaries,
workspace containment, symlink/junction traversal, Windows reparse points,
and sensitive host credential locations.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis.core.exceptions import SandboxSecurityViolationError
from jarvis.core.logging import get_logger

if TYPE_CHECKING:
    from jarvis.sandbox.models import SandboxProfile

logger = get_logger(__name__)

# Sensitive host system paths that must NEVER be mounted into a container
BLOCKED_HOST_PATHS: tuple[str, ...] = (
    "/etc",
    "/private/etc",
    "/proc",
    "/sys",
    "/dev",
    "/root",
    "/boot",
    "/run",
    "/var/run",
    "/private/var/run",
    "/var/run/docker.sock",
    "/private/var/run/docker.sock",
    "/run/docker.sock",
    "//./pipe/docker_engine",
    "\\\\.\\pipe\\docker_engine",
    "/system32",
    "/windows",
)

# User home subdirectories containing sensitive secrets/credentials
BLOCKED_HOME_SUBPATHS: tuple[str, ...] = (
    ".aws",
    ".azure",
    ".config",
    ".docker",
    ".gnupg",
    ".netrc",
    ".npm",
    ".cargo",
    ".ssh",
    ".env",
)

BLOCKED_SECCOMP_PROFILES: frozenset[str] = frozenset({"unconfined"})
BLOCKED_APPARMOR_PROFILES: frozenset[str] = frozenset({"unconfined"})
RESERVED_CONTAINER_TARGET_PATHS: frozenset[str] = frozenset({"/workspace"})


def is_unc_path(path_str: str) -> bool:
    """Detect Windows UNC paths (e.g. \\\\server\\share or //server/share)."""
    clean = path_str.strip()
    return clean.startswith(("\\\\", "//"))


def is_reparse_point_or_symlink(p: Path) -> bool:
    """Detect if a path is a symlink, NTFS junction, or reparse point."""
    if p.is_symlink():
        return True
    if sys.platform == "win32" and p.exists():
        try:
            stat_res = os.lstat(p)
            file_attributes = getattr(stat_res, "st_file_attributes", 0)
            # FILE_ATTRIBUTE_REPARSE_POINT is 0x400
            if file_attributes & 0x400:
                return True
        except (OSError, ValueError):
            pass
    return False


def resolve_via_existing_ancestor(target: Path) -> Path:
    """Resolve symlinks and junctions through existing ancestors for nonexistent leaf targets."""
    curr = target
    missing_parts: list[str] = []

    while not curr.exists() and curr.parent != curr:
        missing_parts.append(curr.name)
        curr = curr.parent

    try:
        resolved_ancestor = curr.resolve()
    except (OSError, RuntimeError):
        resolved_ancestor = curr

    for part in reversed(missing_parts):
        resolved_ancestor = resolved_ancestor / part

    return resolved_ancestor


def normalize_sandbox_relative_path(path_str: str) -> str:
    """Normalize and validate a workspace-relative path inside a sandbox.

    Rejects:
    - Path traversal sequences ('..')
    - URL-encoded traversal sequences ('%2e%2e', '%2f', '%5c')
    - Null bytes ('\\x00')
    - Absolute paths
    - Windows drive letter references (e.g. 'C:', 'D:')
    - UNC paths

    Returns normalized POSIX-style relative path.
    """
    if not isinstance(path_str, str):
        raise SandboxSecurityViolationError("Sandbox path must be a string.")

    unquoted = urllib.parse.unquote(path_str).strip()
    if not unquoted:
        raise SandboxSecurityViolationError("Sandbox path cannot be empty.")

    if "\x00" in unquoted:
        raise SandboxSecurityViolationError("Null byte injection detected in path.")

    if is_unc_path(unquoted):
        raise SandboxSecurityViolationError(f"UNC paths are forbidden in sandbox: '{path_str}'")

    # Check for drive letter escape (e.g. 'C:\', 'D:foo')
    if re.match(r"^[a-zA-Z]:", unquoted):
        raise SandboxSecurityViolationError(
            f"Drive letter references are forbidden in sandbox path: '{path_str}'"
        )

    # Normalize backslashes to forward slashes
    clean = re.sub(r"/+", "/", unquoted.replace("\\", "/")).strip("/")

    # Check for path traversal elements
    raw_parts = clean.split("/")
    parts = [p for p in raw_parts if p != "."]
    if any(part in ("..", "~") for part in parts):
        raise SandboxSecurityViolationError(
            f"Path traversal ('..') detected in sandbox path: '{path_str}'"
        )

    # Strip optional leading 'workspace/'
    if parts and parts[0] == "workspace":
        parts = parts[1:]

    if not parts:
        raise SandboxSecurityViolationError("Sandbox path resolved to empty workspace root.")

    normalized = "/".join(parts)
    if ".." in normalized.split("/"):
        raise SandboxSecurityViolationError(
            f"Traversal detected after normalization: '{normalized}'"
        )

    return normalized


def validate_host_path_for_staging(
    host_path: Path | str,
    allowed_root: Path | None = None,
) -> Path:
    """Validate that a host path is safe for artifact staging into or out of a sandbox.

    Ensures:
    1. Path does not target blocked system paths (e.g. /etc, System32, docker.sock).
    2. Path does not target blocked credential paths (e.g. .ssh, .aws, .env).
    3. Path does not cover the entire host filesystem root.
    4. Path is strictly contained within allowed_root if specified.
    """
    path_str = str(host_path).strip()
    if is_unc_path(path_str):
        raise SandboxSecurityViolationError(f"UNC path forbidden for host staging: '{path_str}'")

    p = Path(host_path)
    resolved = resolve_via_existing_ancestor(p)
    resolved_str = str(resolved).replace("\\", "/").lower()

    # Reject mounting entire drive or root
    if resolved_str in ("/", "c:/", "c:", "d:/", "d:"):
        raise SandboxSecurityViolationError(
            "Host filesystem root cannot be mounted or transferred."
        )

    # Check against blocked host system paths
    drive_stripped = re.sub(r"^[a-zA-Z]:", "", resolved_str)
    for blocked in BLOCKED_HOST_PATHS:
        blocked_clean = blocked.lower()
        if (
            resolved_str == blocked_clean
            or resolved_str.startswith(f"{blocked_clean}/")
            or drive_stripped == blocked_clean
            or drive_stripped.startswith(f"{blocked_clean}/")
        ):
            raise SandboxSecurityViolationError(
                f"Host staging targets prohibited system path: '{blocked}'"
            )

    # Check against blocked home subpaths
    home_dir = Path.home()
    try:
        resolved_home = home_dir.resolve()
        for subpath in BLOCKED_HOME_SUBPATHS:
            blocked_target = (resolved_home / subpath).resolve()
            blocked_str = str(blocked_target).replace("\\", "/").lower()
            if resolved_str == blocked_str or resolved_str.startswith(f"{blocked_str}/"):
                raise SandboxSecurityViolationError(
                    f"Host staging targets prohibited credential path: '{subpath}' in user home"
                )
    except SandboxSecurityViolationError:
        raise
    except Exception as e:
        logger.debug("home_dir_resolution_skipped", error=str(e))

    # Boundary confinement check if allowed_root is configured
    if allowed_root is not None:
        try:
            resolved_root = allowed_root.resolve()
            if not resolved.is_relative_to(resolved_root):
                raise SandboxSecurityViolationError(
                    f"Confinement breach: Host path '{resolved}' escapes allowed root '{resolved_root}'."
                )
        except ValueError as err:
            raise SandboxSecurityViolationError(
                f"Confinement breach: Host path '{resolved}' outside root '{allowed_root}'."
            ) from err

    return resolved


def validate_sandbox_profile_security(profile: SandboxProfile) -> list[str]:
    """Inspect SandboxProfile declarations and return any security violations or unsupported flags."""
    violations: list[str] = []

    if profile.allow_docker_socket:
        violations.append("Docker socket exposure is strictly prohibited.")

    if profile.allow_credential_injection:
        violations.append("Credential injection into sandbox is strictly prohibited.")

    if profile.seccomp_profile.lower() in BLOCKED_SECCOMP_PROFILES:
        violations.append(
            f"Seccomp profile '{profile.seccomp_profile}' is prohibited (unconfined)."
        )

    if not profile.read_only_rootfs:
        violations.append("Read-only root filesystem must be enabled for hardened sandboxes.")

    if not profile.no_new_privileges:
        violations.append("no_new_privileges must be True for hardened sandboxes.")

    user_str = str(profile.run_as_user).strip()
    if user_str in ("0", "root", "0:0"):
        violations.append(f"Workload configured to run as root user ('{user_str}').")

    return violations


__all__ = [
    "BLOCKED_APPARMOR_PROFILES",
    "BLOCKED_HOME_SUBPATHS",
    "BLOCKED_HOST_PATHS",
    "BLOCKED_SECCOMP_PROFILES",
    "RESERVED_CONTAINER_TARGET_PATHS",
    "is_reparse_point_or_symlink",
    "is_unc_path",
    "normalize_sandbox_relative_path",
    "resolve_via_existing_ancestor",
    "validate_host_path_for_staging",
    "validate_sandbox_profile_security",
]
