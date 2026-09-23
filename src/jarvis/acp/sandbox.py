"""External Agent Sandbox Manager for ACP Coding Agents.

Implements Sections 30 and 143 of ARCHITECTURE.md:
- Repository workspace boundary confinement and path traversal prevention
- Environment variable sanitization (preventing secret exfiltration)
- Branch isolation to contain speculative agent modifications
- Sandboxed test execution and filesystem inspection
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from jarvis.telemetry import logger

# Host environment variable names that must NEVER be passed to external agents
SENSITIVE_ENV_PREFIXES = (
    "GEMINI",
    "GOOGLE_API",
    "GROQ",
    "OPENAI",
    "ANTHROPIC",
    "AWS_",
    "AZURE",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "SECRET",
    "PASSWORD",
    "PRIVATE",
    "KEY",
)

SAFE_PASSTHROUGH_VARS = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOME",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
}


class AcpSandboxError(Exception):
    """Raised when an operation violates sandbox boundaries or security policies."""

    pass


class AcpSandboxManager:
    """Manages workspace isolation, environment sanitization, and execution constraints."""

    def __init__(self, repo_path: Path, read_only: bool = False) -> None:
        self.repo_path = repo_path.resolve()
        self.read_only = read_only
        if not self.repo_path.exists():
            raise AcpSandboxError(f"Repository path does not exist: {self.repo_path}")
        if not self.repo_path.is_dir():
            raise AcpSandboxError(f"Repository path is not a directory: {self.repo_path}")

    def resolve_safe_path(self, relative_or_absolute: str | Path) -> Path:
        """Resolve a path and verify it remains strictly within the repository sandbox.

        Raises AcpSandboxError if path traversal is detected.
        """
        raw_path = Path(relative_or_absolute)
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
        else:
            resolved = (self.repo_path / raw_path).resolve()

        try:
            # relative_to will raise ValueError if resolved is not inside repo_path
            resolved.relative_to(self.repo_path)
        except ValueError as err:
            raise AcpSandboxError(
                f"Path traversal violation: '{relative_or_absolute}' resolves to "
                f"'{resolved}', which is outside sandbox root '{self.repo_path}'."
            ) from err

        return resolved

    def sanitize_environment(
        self,
        extra_env: Optional[Dict[str, str]] = None,
        allowed_keys: Optional[Set[str]] = None,
    ) -> Dict[str, str]:
        """Construct a sanitized environment dictionary stripped of sensitive host secrets.

        Parameters
        ----------
        extra_env : Optional[Dict[str, str]]
            Explicit environment variables intended for the worker.
        allowed_keys : Optional[Set[str]]
            Keys explicitly authorized to bypass prefix filters.
        """
        sanitized: Dict[str, str] = {}
        allowed = allowed_keys or set()

        for key, value in os.environ.items():
            upper_key = key.upper()
            if upper_key in allowed:
                sanitized[key] = value
                continue
            if upper_key in SAFE_PASSTHROUGH_VARS:
                sanitized[key] = value
                continue
            if any(upper_key.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES):
                continue
            # Non-sensitive standard environment variables can pass
            if not any(token in upper_key for token in ("SECRET", "TOKEN", "KEY", "PASS")):
                sanitized[key] = value

        if extra_env:
            for k, v in extra_env.items():
                sanitized[k] = v

        return sanitized

    def ensure_isolated_branch(self, branch_name: str) -> bool:
        """Ensure git working tree is switched to an isolated branch.

        Returns True if branch was successfully selected or created, False if git is unavailable.
        """
        git_dir = self.repo_path / ".git"
        if not git_dir.exists():
            logger.info(
                f"Directory {self.repo_path} is not a git repository; skipping branch creation."
            )
            return False

        try:
            # Check if branch exists
            result = subprocess.run(
                ["git", "rev-parse", "--verify", branch_name],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            if result.returncode == 0:
                # Branch exists; checkout
                subprocess.run(
                    ["git", "checkout", branch_name],
                    cwd=str(self.repo_path),
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10.0,
                )
            else:
                # Create and checkout
                subprocess.run(
                    ["git", "checkout", "-b", branch_name],
                    cwd=str(self.repo_path),
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10.0,
                )
            logger.info(f"Checked out isolated branch '{branch_name}' in {self.repo_path}")
            return True
        except Exception as err:
            logger.warning(f"Failed to switch to git branch '{branch_name}': {err}")
            return False

    def inspect_git_changes(self) -> List[str]:
        """Inspect repository working tree for modified, added, or untracked files."""
        git_dir = self.repo_path / ".git"
        if not git_dir.exists():
            return []

        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=15.0,
            )
            if result.returncode != 0:
                return []

            changed: List[str] = []
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Line format is 'XY path' or 'XY "path"'
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    file_path = parts[1].strip('"')
                    # Handle renames e.g. "orig -> new"
                    if " -> " in file_path:
                        file_path = file_path.split(" -> ")[1].strip('"')
                    changed.append(file_path)
            return sorted(set(changed))
        except Exception as err:
            logger.warning(f"Failed to inspect git status: {err}")
            return []

    def run_sandboxed_tests(
        self,
        test_command: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ) -> Tuple[bool, str]:
        """Execute test suite within the sandbox and report pass/fail status and output."""
        cmd = test_command or f"{sys.executable} -m pytest -q"
        env = self.sanitize_environment()

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
            )
            passed = result.returncode == 0
            output = result.stdout + ("\n" + result.stderr if result.stderr else "")
            return passed, output.strip()
        except subprocess.TimeoutExpired:
            return False, f"Test execution timed out after {timeout_seconds} seconds."
        except Exception as err:
            return False, f"Test runner execution failed: {err}"
