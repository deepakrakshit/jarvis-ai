"""JARVIS Deep Agents Sandbox Backend Adapter.

Implements BaseSandbox wrapping JARVIS SandboxProvider and SandboxLifecycleManager.
Enforces zero-trust policy evaluation, least-privilege scoping, container absence
verification, and fail-closed host isolation invariants.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import re
import tempfile
import urllib.parse
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from jarvis.core.capabilities.manifest import (
    CapabilityManifest,
    RiskClass,
    SideEffectClass,
    ToolType,
)
from jarvis.core.exceptions import (
    SandboxBackendUnavailableError,
    SandboxExecutionError,
    SandboxSecurityViolationError,
    SandboxTimeoutError,
)
from jarvis.core.logging import get_logger
from jarvis.core.policy.decision import AutonomyLevel, PolicyDecisionType
from jarvis.deep_agents.models import (
    HumanApprovalRequiredError,
    UnauthorizedToolInvocationError,
)
from jarvis.sandbox.detector import get_system_capabilities
from jarvis.sandbox.models import (
    BackendType,
    CapabilityStatus,
    ProviderCategory,
    SandboxProfile,
    SandboxState,
)
from jarvis.sandbox.provider import DockerSandboxProvider, SandboxProvider

if TYPE_CHECKING:
    from jarvis.core.broker.broker import ActionBroker
    from jarvis.core.policy.engine import PolicyEngine
    from jarvis.sandbox.manager import SandboxLifecycleManager

logger = get_logger(__name__)


def normalize_and_validate_sandbox_path(path_str: str) -> str | None:
    """Normalize and validate that a sandbox path stays confined within /workspace.

    Defends against:
    - Traversal attacks ('..')
    - Encoded traversal ('%2e%2e', '%2f')
    - Null byte injection ('\x00')
    - Empty, root, or whitespace paths
    - Repeated separators and Windows backslashes

    Returns the normalized relative POSIX path (e.g. 'foo/bar.txt') if safe,
    or None if the path is invalid or attempts escape.
    """
    if not isinstance(path_str, str):
        return None
    unquoted = urllib.parse.unquote(path_str).strip()
    if not unquoted or "\x00" in unquoted:
        return None

    clean = re.sub(r"/+", "/", unquoted.replace("\\", "/")).strip()
    if clean in ("", "/", "."):
        return None

    posix = PurePosixPath(clean)
    parts = posix.parts

    if ".." in parts or "~" in parts:
        return None

    if parts and parts[0] == "/":
        parts = parts[1:]
    if parts and parts[0] == "workspace":
        parts = parts[1:]

    if not parts or ".." in parts:
        return None

    return "/".join(parts)


def _is_safe_sandbox_relative_path(path_str: str) -> bool:
    """Verify that a path does not traverse outside the sandbox root or use relative parent escapes."""
    return normalize_and_validate_sandbox_path(path_str) is not None


class JarvisSandboxBackend(BaseSandbox):
    """Governed Deep Agents sandbox adapter bridging to JARVIS containment backends.

    Zero-Trust Invariants:
    1. Sole Authority: JARVIS Policy Engine & Action Broker govern every action.
    2. Containment: BaseSandbox is an adapter layer, not a security boundary;
       the underlying SandboxProvider provides the actual isolation boundary.
    3. Fail Closed: Unisolated host execution (LocalShellBackend) is strictly prohibited.
       If the required sandbox backend is unavailable, execution fails closed.
    4. Attestation Invariant: Test double providers strictly yield real_isolation_verified = False.
    """

    def __init__(
        self,
        provider: SandboxProvider,
        sandbox_id: UUID | None = None,
        task_id: UUID | None = None,
        lifecycle_manager: SandboxLifecycleManager | None = None,
        policy_engine: PolicyEngine | None = None,
        action_broker: ActionBroker | None = None,
        autonomy_level: AutonomyLevel = AutonomyLevel.AUTO_BOUNDED_MUTATION,
        task_scopes: frozenset[str] = frozenset(
            {"sandbox:execute", "sandbox:read", "sandbox:write"}
        ),
        allow_test_doubles: bool = False,
        profile: SandboxProfile | None = None,
        max_output_bytes: int = 50_000,
    ) -> None:
        super().__init__()
        self._provider = provider
        self._sandbox_id = sandbox_id or uuid4()
        self._task_id = task_id or uuid4()
        self._lifecycle_manager = lifecycle_manager
        self._policy_engine = policy_engine
        self._action_broker = action_broker
        self._autonomy_level = autonomy_level
        self._task_scopes = task_scopes
        self._allow_test_doubles = allow_test_doubles
        self._profile = profile or SandboxProfile(profile_id="jarvis_deepagent_profile")
        self._max_output_bytes = max_output_bytes

        # Validate provider category invariants immediately upon construction
        if (
            self._provider.provider_category == ProviderCategory.TEST_DOUBLE
            and not self._allow_test_doubles
        ):
            raise SandboxBackendUnavailableError(
                "Cannot bind test double provider when allow_test_doubles is False."
            )

    @property
    def id(self) -> str:
        """Return the unique string identity of the governed sandbox."""
        return str(self._sandbox_id)

    @property
    def sandbox_id(self) -> UUID:
        """Return the UUID identity of the governed sandbox."""
        return self._sandbox_id

    @property
    def task_id(self) -> UUID:
        """Return the task UUID associated with this backend."""
        return self._task_id

    @property
    def provider(self) -> SandboxProvider:
        """Access the underlying containment provider."""
        return self._provider

    @property
    def provider_category(self) -> ProviderCategory:
        """Return whether provider is a real isolation provider or test double."""
        return self._provider.provider_category

    @property
    def backend_type(self) -> BackendType:
        """Return the execution engine type."""
        return self._provider.backend_type

    @property
    def real_isolation_verified(self) -> bool:
        """Indicate whether genuine host containment is empirically verified."""
        if self._provider.provider_category == ProviderCategory.TEST_DOUBLE:
            return False

        if isinstance(self._provider, DockerSandboxProvider):
            caps = get_system_capabilities()
            return caps.docker_daemon_available == CapabilityStatus.SUPPORTED

        return False

    async def _ensure_sandbox_ready(self) -> None:
        """Ensure that the sandbox container or test double is created and ready."""
        # 1. Check if existing sandbox is recognized by the provider
        try:
            inspect_state = await self._provider.inspect(self._sandbox_id)
            if inspect_state in (SandboxState.READY, SandboxState.EXECUTING):
                return
            if inspect_state == SandboxState.CREATED:
                await self._provider.start(self._sandbox_id)
                return
        except Exception:
            pass

        # 2. If sandbox is managed by a lifecycle manager, look it up or create
        if self._lifecycle_manager is not None:
            try:
                state = self._lifecycle_manager.get_sandbox_state(self._sandbox_id)
                if state in (SandboxState.READY, SandboxState.EXECUTING):
                    return
                if state == SandboxState.CREATED:
                    await self._lifecycle_manager.start_sandbox(self._sandbox_id)
                    return
            except Exception:
                pass
            identity = await self._lifecycle_manager.create_sandbox(
                task_id=self._task_id,
                profile=self._profile,
                allow_test_doubles=self._allow_test_doubles,
            )
            self._sandbox_id = identity.sandbox_id
            await self._lifecycle_manager.start_sandbox(self._sandbox_id)
            return

        # 3. Otherwise provision directly on provider
        identity = await self._provider.create(
            task_id=self._task_id,
            profile=self._profile,
        )
        self._sandbox_id = identity.sandbox_id
        await self._provider.start(self._sandbox_id)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute a command synchronously within the isolated sandbox under JARVIS governance."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lambda: asyncio.run(self.aexecute(command, timeout=timeout)))
                return future.result()
        return asyncio.run(self.aexecute(command, timeout=timeout))

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute a command asynchronously within the isolated sandbox under JARVIS governance.

        Zero-Trust Evaluation Flow:
        1. Fail-closed backend availability check.
        2. Scope verification (sandbox:execute required).
        3. Centralized Policy Engine evaluation.
        4. Human-In-The-Loop pause enforcement when required.
        5. Action Broker logical effect derivation.
        6. Command dispatch to underlying SandboxProvider.
        7. Output capture and length truncation defenses.
        """
        # 1. Fail-closed verification
        if (
            self._provider.provider_category == ProviderCategory.TEST_DOUBLE
            and not self._allow_test_doubles
        ):
            raise SandboxBackendUnavailableError(
                "Execution rejected: Test double provider is prohibited for real isolation tasks."
            )

        if isinstance(self._provider, DockerSandboxProvider):
            caps = get_system_capabilities()
            if caps.docker_daemon_available != CapabilityStatus.SUPPORTED:
                raise SandboxBackendUnavailableError(
                    "Execution rejected: Docker container daemon is offline or unverified on host."
                )

        # 2. Scope check: sandbox:execute required
        if "sandbox:execute" not in self._task_scopes:
            raise UnauthorizedToolInvocationError(
                "Execution denied: 'sandbox:execute' scope is not granted to this task."
            )

        # 3. Policy Engine evaluation
        if self._policy_engine is not None:
            manifest = CapabilityManifest(
                capability_id="sandbox:execute",
                description="Executes a shell command inside an isolated container boundary.",
                risk_class=RiskClass.BOUNDED_MUTATION,
                side_effect_class=SideEffectClass.NON_IDEMPOTENT,
                tool_type=ToolType.SANDBOX,
                required_scopes=["sandbox:execute"],
            )
            decision = self._policy_engine.evaluate_invocation(
                task_id=self._task_id,
                manifest=manifest,
                arguments={"command": command},
                autonomy_level=self._autonomy_level,
            )

            if decision.decision == PolicyDecisionType.REQUIRE_HITL:
                raise HumanApprovalRequiredError(
                    f"Execution paused: Command '{command[:60]}' requires human-in-the-loop approval ({decision.reason})."
                )

            if decision.decision == PolicyDecisionType.DENY:
                return ExecuteResponse(
                    output=f"Execution blocked by Policy Engine: {decision.reason}",
                    exit_code=126,
                    truncated=False,
                )

        # 4. Action Broker tracking
        logical_effect_id: str | None = None
        if self._action_broker is not None:
            logical_effect_id = self._action_broker.compute_logical_effect_id(
                task_id=self._task_id,
                tool_id="sandbox:execute",
                arguments={"command": command},
            )

        # 5. Format command for container execution
        cmd_args = ["/bin/sh", "-c", command]

        # 6. Dispatch to provider
        effective_timeout = (
            float(timeout) if timeout is not None else self._profile.resource_limits.timeout_seconds
        )

        await self._ensure_sandbox_ready()

        try:
            if self._lifecycle_manager is not None:
                sbx_state = self._lifecycle_manager.get_sandbox_state(self._sandbox_id)
                if sbx_state not in (SandboxState.READY, SandboxState.EXECUTING):
                    raise SandboxExecutionError(
                        f"Cannot execute: Sandbox {self._sandbox_id} is in state {sbx_state.value}."
                    )
                result = await self._lifecycle_manager.execute_command(
                    sandbox_id=self._sandbox_id,
                    command=cmd_args,
                    task_id=self._task_id,
                    capability_id="sandbox:execute",
                    logical_effect_id=logical_effect_id,
                    timeout_seconds=effective_timeout,
                )
            else:
                result = await self._provider.execute(
                    sandbox_id=self._sandbox_id,
                    command=cmd_args,
                    task_id=self._task_id,
                    capability_id="sandbox:execute",
                    logical_effect_id=logical_effect_id,
                    timeout_seconds=effective_timeout,
                )
        except SandboxTimeoutError:
            return ExecuteResponse(
                output=f"Execution timed out after {effective_timeout:.1f} seconds.",
                exit_code=124,
                truncated=False,
            )
        except (SandboxBackendUnavailableError, SandboxSecurityViolationError):
            raise
        except Exception as exc:
            logger.error(
                "deepagent_sandbox_exec_failed",
                sandbox_id=str(self._sandbox_id),
                command=command[:100],
                error=str(exc),
            )
            return ExecuteResponse(
                output=f"Execution error: {exc}",
                exit_code=1,
                truncated=False,
            )

        # 7. Merge stdout and stderr and defend against buffer overflow
        combined_output = result.stdout
        if result.stderr:
            if combined_output:
                combined_output = f"{combined_output}\n{result.stderr}"
            else:
                combined_output = result.stderr

        is_truncated = len(combined_output) > self._max_output_bytes
        if is_truncated:
            combined_output = (
                combined_output[: self._max_output_bytes]
                + "\n...[OUTPUT TRUNCATED DUE TO SIZE LIMIT]"
            )

        return ExecuteResponse(
            output=combined_output,
            exit_code=result.exit_code,
            truncated=is_truncated,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Synchronously upload files to the sandbox workspace."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lambda: asyncio.run(self.aupload_files(files)))
                return future.result()
        return asyncio.run(self.aupload_files(files))

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Asynchronously upload files to the sandbox workspace with traversal protection."""
        if not files:
            return []

        # Scope verification: sandbox:write or fs:write required
        has_write_scope = bool(self._task_scopes.intersection({"sandbox:write", "fs:write"}))
        if not has_write_scope:
            return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

        await self._ensure_sandbox_ready()

        responses: list[FileUploadResponse] = []
        with tempfile.TemporaryDirectory(prefix="jarvis_da_upload_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            staging_pairs: list[tuple[Path, str]] = []

            for path_str, content_bytes in files:
                clean_rel = normalize_and_validate_sandbox_path(path_str)
                if clean_rel is None:
                    responses.append(FileUploadResponse(path=path_str, error="permission_denied"))
                    continue

                local_file = tmp_dir / f"file_{hashlib.sha256(clean_rel.encode()).hexdigest()[:12]}"
                local_file.write_bytes(content_bytes)
                staging_pairs.append((local_file, clean_rel))

            if staging_pairs:
                try:
                    await self._provider.upload_artifacts(
                        sandbox_id=self._sandbox_id,
                        task_id=self._task_id,
                        artifacts=staging_pairs,
                    )
                    for _, clean_rel in staging_pairs:
                        responses.append(FileUploadResponse(path=clean_rel, error=None))
                except Exception as exc:
                    logger.warning("deepagent_upload_failed", error=str(exc))
                    for _, clean_rel in staging_pairs:
                        responses.append(
                            FileUploadResponse(path=clean_rel, error=f"upload_error: {exc}")
                        )

        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Synchronously download files from the sandbox workspace."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lambda: asyncio.run(self.adownload_files(paths)))
                return future.result()
        return asyncio.run(self.adownload_files(paths))

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Asynchronously download files from the sandbox workspace with traversal protection."""
        if not paths:
            return []

        # Scope verification: sandbox:read or fs:read required
        has_read_scope = bool(self._task_scopes.intersection({"sandbox:read", "fs:read"}))
        if not has_read_scope:
            return [
                FileDownloadResponse(path=path, content=None, error="permission_denied")
                for path in paths
            ]

        await self._ensure_sandbox_ready()

        responses: list[FileDownloadResponse] = []
        with tempfile.TemporaryDirectory(prefix="jarvis_da_download_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            fetch_pairs: list[tuple[str, Path]] = []

            for path_str in paths:
                clean_rel = normalize_and_validate_sandbox_path(path_str)
                if clean_rel is None:
                    responses.append(
                        FileDownloadResponse(path=path_str, content=None, error="permission_denied")
                    )
                    continue

                dest_file = tmp_dir / f"dl_{hashlib.sha256(clean_rel.encode()).hexdigest()[:12]}"
                fetch_pairs.append((clean_rel, dest_file))

            if fetch_pairs:
                try:
                    await self._provider.download_artifacts(
                        sandbox_id=self._sandbox_id,
                        task_id=self._task_id,
                        artifacts=fetch_pairs,
                    )
                    for clean_rel, dest_file in fetch_pairs:
                        if dest_file.is_file():
                            content = dest_file.read_bytes()
                            responses.append(
                                FileDownloadResponse(path=clean_rel, content=content, error=None)
                            )
                        else:
                            responses.append(
                                FileDownloadResponse(
                                    path=clean_rel, content=None, error="file_not_found"
                                )
                            )
                except Exception as exc:
                    logger.warning("deepagent_download_failed", error=str(exc))
                    for clean_rel, _ in fetch_pairs:
                        responses.append(
                            FileDownloadResponse(
                                path=clean_rel, content=None, error=f"download_error: {exc}"
                            )
                        )

        return responses
