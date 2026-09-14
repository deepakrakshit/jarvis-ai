"""JARVIS Sandbox Execution Fabric Abstraction.

Provides a confined process execution environment with environment variable scrubbing and timeout enforcement.
"""

import asyncio
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, Field

from jarvis.core.exceptions import SandboxExecutionError, SandboxTimeoutError
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class SandboxResult(BaseModel):
    """Result of a sandboxed command execution."""

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float = Field(ge=0.0)
    timed_out: bool = False


class SandboxRunner(ABC):
    """Abstract interface for sandboxed command execution."""

    @abstractmethod
    async def execute(
        self,
        command: list[str],
        workdir: Path,
        env: dict[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> SandboxResult:
        """Execute a command within sandbox constraints."""
        pass


class LocalProcessSandbox(SandboxRunner):
    """Local process sandbox with directory boundary confinement and environment scrubbing."""

    # Environment variables strictly forbidden from leaking into sandbox processes
    FORBIDDEN_ENV_PREFIXES = (
        "GEMINI_",
        "GROQ_",
        "OPENROUTER_",
        "VAULT_",
        "API_KEY",
        "SECRET",
        "AWS_",
        "AZURE_",
        "GCP_",
    )

    def __init__(self, allowed_root: Path | None = None) -> None:
        self.allowed_root = allowed_root.resolve() if allowed_root else None

    def _sanitize_environment(self, custom_env: dict[str, str] | None) -> dict[str, str]:
        """Produce a clean environment scrubbed of sensitive host credentials."""
        clean_env: dict[str, str] = {}

        # Safely import standard runtime system variables
        safe_keys = {
            "PATH",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "HOMEPATH",
            "HOMEDRIVE",
            "COMSPEC",
        }
        for k, v in os.environ.items():
            if k.upper() in safe_keys or not any(
                k.upper().startswith(p) for p in self.FORBIDDEN_ENV_PREFIXES
            ):
                clean_env[k] = v

        if custom_env:
            for k, v in custom_env.items():
                if any(k.upper().startswith(p) for p in self.FORBIDDEN_ENV_PREFIXES):
                    raise SandboxExecutionError(
                        f"Attempted to inject sensitive credential key '{k}' into sandbox."
                    )
                clean_env[k] = v

        return clean_env

    async def execute(
        self,
        command: list[str],
        workdir: Path,
        env: dict[str, str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> SandboxResult:
        """Execute command via subprocess with argument list (shell=False) and confinement check."""
        if not command:
            raise SandboxExecutionError("Command list cannot be empty.")

        resolved_workdir = workdir.resolve()
        if self.allowed_root and not resolved_workdir.is_relative_to(self.allowed_root):
            raise SandboxExecutionError(
                f"Confinement breach: Workdir '{resolved_workdir}' is outside allowed root '{self.allowed_root}'."
            )

        resolved_workdir.mkdir(parents=True, exist_ok=True)
        sanitized_env = self._sanitize_environment(env)

        start_time = time.monotonic()
        logger.debug("executing_sandboxed_command", cmd=command[0], workdir=str(resolved_workdir))

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(resolved_workdir),
                env=sanitized_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=timeout_seconds
                )
                duration = time.monotonic() - start_time
                return SandboxResult(
                    exit_code=process.returncode or 0,
                    stdout=stdout_bytes.decode(errors="replace"),
                    stderr=stderr_bytes.decode(errors="replace"),
                    duration_seconds=duration,
                    timed_out=False,
                )
            except TimeoutError as err:
                process.kill()
                await process.wait()
                duration = time.monotonic() - start_time
                logger.warning(
                    "sandboxed_command_timed_out", cmd=command[0], timeout=timeout_seconds
                )
                raise SandboxTimeoutError(
                    f"Command '{command[0]}' timed out after {timeout_seconds} seconds."
                ) from err

        except FileNotFoundError as e:
            raise SandboxExecutionError(f"Executable not found: {command[0]}") from e
        except Exception as e:
            if isinstance(e, (SandboxExecutionError, SandboxTimeoutError)):
                raise
            raise SandboxExecutionError(f"Process execution error: {e}") from e
