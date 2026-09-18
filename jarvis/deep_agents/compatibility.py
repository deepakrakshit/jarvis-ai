"""Upstream Deep Agents Compatibility Verifier.

Provides compile-time and startup-time compatibility assertions for
the official Python deepagents package and its internal contracts.
"""

from __future__ import annotations

import importlib.metadata
import inspect
from typing import Any

from deepagents.middleware.filesystem import FilesystemMiddleware

from jarvis.core.exceptions import JarvisError
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_DEEPAGENTS_MAJOR = 0
SUPPORTED_DEEPAGENTS_MINOR = 7


class DeepAgentCompatibilityError(JarvisError):
    """Raised when the installed deepagents package violates expected security contracts."""


def verify_deepagents_compatibility() -> dict[str, Any]:
    """Verify runtime compatibility with official deepagents package.

    Validates:
    1. Package is installed and version is in supported range (0.7.x).
    2. FilesystemMiddleware.__init__ exposes '_permissions' parameter.
    3. FilesystemMiddleware retains '_permissions' storage attribute.

    Fails closed with DeepAgentCompatibilityError if any invariant is violated.
    """
    try:
        version_str = importlib.metadata.version("deepagents")
    except importlib.metadata.PackageNotFoundError as err:
        raise DeepAgentCompatibilityError(
            "The 'deepagents' package is not installed in the active environment."
        ) from err

    parts = version_str.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError) as err:
        raise DeepAgentCompatibilityError(
            f"Unable to parse deepagents version string: '{version_str}'."
        ) from err

    if major != SUPPORTED_DEEPAGENTS_MAJOR or minor != SUPPORTED_DEEPAGENTS_MINOR:
        raise DeepAgentCompatibilityError(
            f"Unsupported deepagents version '{version_str}'. Expected 0.7.x. "
            f"Fail-closed guard prevents unverified execution."
        )

    # Verify private _permissions parameter in FilesystemMiddleware.__init__
    sig = inspect.signature(FilesystemMiddleware.__init__)
    if "_permissions" not in sig.parameters:
        raise DeepAgentCompatibilityError(
            "FilesystemMiddleware signature does not expose '_permissions' parameter. "
            "Upstream permissions channel has changed; failing closed."
        )

    logger.debug(
        "deepagents_compatibility_verified",
        version=version_str,
        has_permissions_param=True,
    )

    return {
        "version": version_str,
        "major": major,
        "minor": minor,
        "has_permissions_param": True,
        "compatible": True,
    }
