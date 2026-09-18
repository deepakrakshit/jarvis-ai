"""JARVIS Sandbox Provider Abstraction and Concrete Drivers.

Defines the provider-agnostic interface for execution containment,
the production Docker container driver, and contract test doubles.
Enforces the fail-closed invariant and explicit artifact staging.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from jarvis.core.exceptions import (
    SandboxBackendUnavailableError,
    SandboxExecutionError,
    SandboxSecurityViolationError,
    SandboxTimeoutError,
)
from jarvis.core.logging import get_logger
from jarvis.sandbox.detector import get_system_capabilities
from jarvis.sandbox.models import (
    ArtifactDirection,
    ArtifactTransferRecord,
    AttestationStatus,
    BackendType,
    CapabilityStatus,
    DockerBackendType,
    EffectiveSecurityConfiguration,
    IsolationTier,
    NetworkProfile,
    ProviderCategory,
    ProviderHealthReport,
    SandboxAttestation,
    SandboxExecutionResult,
    SandboxIdentity,
    SandboxProfile,
    SandboxState,
)

logger = get_logger(__name__)


class SandboxProvider(ABC):
    """Abstract interface for all sandbox execution backends."""

    @property
    @abstractmethod
    def provider_category(self) -> ProviderCategory:
        """Indicates whether this provider enforces real isolation or is a test double."""
        pass

    @property
    @abstractmethod
    def backend_type(self) -> BackendType:
        """Underlying execution engine."""
        pass

    @property
    @abstractmethod
    def supported_tier(self) -> IsolationTier:
        """The containment tier provided by this backend."""
        pass

    @abstractmethod
    async def create(
        self,
        task_id: UUID,
        profile: SandboxProfile,
        request_id: UUID | None = None,
        session_id: UUID | None = None,
        agent_id: str = "coding",
    ) -> SandboxIdentity:
        """Initialize an ephemeral sandbox context for a task."""
        pass

    @abstractmethod
    async def start(self, sandbox_id: UUID) -> SandboxState:
        """Start the sandbox container or environment."""
        pass

    @abstractmethod
    async def health_check(self, sandbox_id: UUID) -> bool:
        """Verify the sandbox environment is healthy and responsive."""
        pass

    @abstractmethod
    async def execute(
        self,
        sandbox_id: UUID,
        command: list[str],
        task_id: UUID,
        capability_id: str,
        logical_effect_id: str | None = None,
        attempt_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> SandboxExecutionResult:
        """Execute a command within the isolated sandbox."""
        pass

    @abstractmethod
    async def upload_artifacts(
        self,
        sandbox_id: UUID,
        task_id: UUID,
        artifacts: list[tuple[Path, str]],
    ) -> list[ArtifactTransferRecord]:
        """Transfer files from host into the sandbox workspace with SHA-256 validation."""
        pass

    @abstractmethod
    async def download_artifacts(
        self,
        sandbox_id: UUID,
        task_id: UUID,
        artifacts: list[tuple[str, Path]],
    ) -> list[ArtifactTransferRecord]:
        """Transfer files from sandbox workspace back to host with SHA-256 validation."""
        pass

    @abstractmethod
    async def inspect(self, sandbox_id: UUID) -> SandboxState:
        """Inspect the current lifecycle state of the sandbox."""
        pass

    @abstractmethod
    async def terminate(self, sandbox_id: UUID, reason: str = "normal_completion") -> SandboxState:
        """Quiesce and terminate the sandbox environment."""
        pass

    @abstractmethod
    async def cleanup(self, sandbox_id: UUID) -> None:
        """Destroy container resources, temporary workspaces, and finalizer hooks."""
        pass

    @abstractmethod
    async def get_health_report(self, run_smoke_test: bool = False) -> ProviderHealthReport:
        """Evaluate and report structured operational readiness and health of this backend."""
        pass

    @abstractmethod
    async def attest(self, sandbox_id: UUID) -> SandboxAttestation:
        """Inspect and attest the empirical security properties of an active sandbox."""
        pass


class DockerSandboxProvider(SandboxProvider):
    """Production Tier-1 OCI/Docker container sandbox provider.

    Translates SandboxProfile declarations into hardened container arguments:
    - Kernel namespaces & non-root user
    - Dropped capabilities (`--cap-drop ALL`)
    - Read-only root filesystem (`--read-only`)
    - Scoped writable staging workspace
    - Cgroup limits (`--pids-limit`, `--memory`, `--cpus`)
    - Egress containment (`--network none` by default)
    - Zero credential or Docker socket exposure
    """

    def __init__(self) -> None:
        self._sandboxes: dict[UUID, dict[str, Any]] = {}

    @property
    def provider_category(self) -> ProviderCategory:
        return ProviderCategory.REAL_ISOLATION_PROVIDER

    @property
    def backend_type(self) -> BackendType:
        return BackendType.DOCKER

    @property
    def supported_tier(self) -> IsolationTier:
        return IsolationTier.TIER_1_CONTAINER

    def _verify_engine_available(self) -> None:
        """Enforce the absolute fail-closed invariant if Docker is offline."""
        caps = get_system_capabilities()
        if caps.docker_daemon_available != CapabilityStatus.SUPPORTED:
            reasons = (
                "; ".join(caps.diagnostics) if caps.diagnostics else "Docker daemon is inactive"
            )
            raise SandboxBackendUnavailableError(
                f"Fail-closed invariant triggered: Tier-1 container isolation is unavailable on this host ({reasons}). "
                "Automatic unisolated host subprocess fallback is strictly prohibited."
            )

    async def create(
        self,
        task_id: UUID,
        profile: SandboxProfile,
        request_id: UUID | None = None,
        session_id: UUID | None = None,
        agent_id: str = "coding",
    ) -> SandboxIdentity:
        self._verify_engine_available()

        if profile.allow_docker_socket:
            raise SandboxSecurityViolationError(
                "Docker socket exposure prohibited in sandbox profile."
            )
        if profile.allow_credential_injection:
            raise SandboxSecurityViolationError(
                "Credential injection prohibited in sandbox profile."
            )

        sandbox_id = uuid4()
        staging_dir = Path(tempfile.mkdtemp(prefix=f"jarvis_sbx_{sandbox_id.hex[:8]}_"))
        container_name = f"jarvis-sbx-{sandbox_id.hex[:12]}"

        now = datetime.now(UTC)
        identity = SandboxIdentity(
            sandbox_id=sandbox_id,
            task_id=task_id,
            session_id=session_id,
            agent_id=agent_id,
            owner="core",
            profile_id=profile.profile_id,
            backend_type=self.backend_type,
            isolation_tier=self.supported_tier,
            provider_category=self.provider_category,
            created_at=now,
            expires_at=datetime.fromtimestamp(
                now.timestamp() + profile.resource_limits.timeout_seconds * 2, tz=UTC
            ),
        )

        self._sandboxes[sandbox_id] = {
            "identity": identity,
            "profile": profile,
            "state": SandboxState.CREATED,
            "staging_dir": staging_dir,
            "container_name": container_name,
            "started_at": None,
            "created_at": now,
        }

        logger.info("sandbox_created", sandbox_id=str(sandbox_id), profile_id=profile.profile_id)
        return identity

    def _build_docker_run_args(self, sandbox_id: UUID) -> list[str]:
        """Construct the security-hardened Docker command-line arguments."""
        record = self._sandboxes[sandbox_id]
        profile: SandboxProfile = record["profile"]
        container_name: str = record["container_name"]
        staging_dir: Path = record["staging_dir"]

        args = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--user",
            profile.run_as_user,
            "--security-opt",
            "no-new-privileges=true",
        ]

        if profile.seccomp_profile:
            args.extend(["--security-opt", f"seccomp={profile.seccomp_profile}"])

        for cap in profile.drop_capabilities:
            args.extend(["--cap-drop", cap])
        for cap in profile.add_capabilities:
            args.extend(["--cap-add", cap])

        if profile.read_only_rootfs:
            args.append("--read-only")
            args.extend(["--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"])

        res = profile.resource_limits
        args.extend(["--pids-limit", str(res.pids_limit)])
        args.extend(["--memory", f"{res.memory_limit_mb}m"])
        args.extend(["--cpus", str(res.cpu_limit)])

        if profile.network_profile == NetworkProfile.NONE:
            args.extend(["--network", "none"])
        elif profile.network_profile == NetworkProfile.ALLOWLIST:
            args.extend(["--network", "bridge"])

        # Mount strictly the dedicated staging workspace
        args.extend(["-v", f"{staging_dir.resolve()}:/workspace:rw"])
        args.extend(["-w", "/workspace"])

        # Labels for audit and correlation
        args.extend(
            [
                "--label",
                f"jarvis.task_id={record['identity'].task_id}",
                "--label",
                f"jarvis.sandbox_id={sandbox_id}",
                "--label",
                f"jarvis.profile_id={profile.profile_id}",
            ]
        )

        # Base image and keepalive loop
        args.append(profile.base_image)
        args.extend(["sleep", "infinity"])
        return args

    async def start(self, sandbox_id: UUID) -> SandboxState:
        self._verify_engine_available()
        record = self._sandboxes.get(sandbox_id)
        if not record:
            raise SandboxExecutionError(f"Sandbox {sandbox_id} not found.")

        record["state"] = SandboxState.STARTING
        args = self._build_docker_run_args(sandbox_id)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                record["state"] = SandboxState.FAILED
                err_msg = stderr.decode(errors="replace").strip()
                raise SandboxExecutionError(f"Failed starting Docker container: {err_msg}")

            record["state"] = SandboxState.READY
            record["started_at"] = datetime.now(UTC)
            logger.info(
                "sandbox_started", sandbox_id=str(sandbox_id), container=record["container_name"]
            )
            return SandboxState.READY
        except Exception as exc:
            record["state"] = SandboxState.FAILED
            if isinstance(exc, SandboxExecutionError):
                raise
            raise SandboxExecutionError(f"Docker startup error: {exc}") from exc

    async def health_check(self, sandbox_id: UUID) -> bool:
        record = self._sandboxes.get(sandbox_id)
        if not record or record["state"] != SandboxState.READY:
            return False
        container_name = record["container_name"]
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}}",
                container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return proc.returncode == 0 and stdout.decode().strip().lower() == "true"
        except Exception:
            return False

    async def execute(
        self,
        sandbox_id: UUID,
        command: list[str],
        task_id: UUID,
        capability_id: str,
        logical_effect_id: str | None = None,
        attempt_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> SandboxExecutionResult:
        self._verify_engine_available()
        record = self._sandboxes.get(sandbox_id)
        if not record:
            raise SandboxExecutionError(f"Sandbox {sandbox_id} not found.")
        if record["state"] not in (SandboxState.READY, SandboxState.EXECUTING):
            raise SandboxExecutionError(
                f"Cannot execute in sandbox {sandbox_id} in state {record['state']}."
            )

        profile: SandboxProfile = record["profile"]
        container_name: str = record["container_name"]
        timeout = timeout_seconds or profile.resource_limits.timeout_seconds
        started_at = datetime.now(UTC)
        record["state"] = SandboxState.EXECUTING

        exec_args = ["docker", "exec", container_name, *command]
        try:
            proc = await asyncio.create_subprocess_exec(
                *exec_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                duration = (datetime.now(UTC) - started_at).total_seconds()
                record["state"] = SandboxState.READY

                max_bytes = profile.resource_limits.max_output_bytes
                stdout_str = stdout_bytes.decode(errors="replace")[:max_bytes]
                stderr_str = stderr_bytes.decode(errors="replace")[:max_bytes]

                return SandboxExecutionResult(
                    sandbox_id=sandbox_id,
                    task_id=task_id,
                    exit_code=proc.returncode if proc.returncode is not None else 0,
                    stdout=stdout_str,
                    stderr=stderr_str,
                    duration_seconds=duration,
                    timed_out=False,
                    state=SandboxState.READY,
                    backend_type=self.backend_type,
                    isolation_tier=self.supported_tier,
                    provider_category=self.provider_category,
                    network_profile=profile.network_profile,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                )
            except TimeoutError as err:
                record["state"] = SandboxState.TIMED_OUT
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                raise SandboxTimeoutError(
                    f"Execution timed out after {timeout} seconds in sandbox {sandbox_id}."
                ) from err
        except Exception as exc:
            if isinstance(exc, (SandboxTimeoutError, SandboxExecutionError)):
                raise
            raise SandboxExecutionError(f"Execution failed in sandbox {sandbox_id}: {exc}") from exc

    async def upload_artifacts(
        self,
        sandbox_id: UUID,
        task_id: UUID,
        artifacts: list[tuple[Path, str]],
    ) -> list[ArtifactTransferRecord]:
        record = self._sandboxes.get(sandbox_id)
        if not record:
            raise SandboxExecutionError(f"Sandbox {sandbox_id} not found.")

        staging_dir: Path = record["staging_dir"]
        transfers: list[ArtifactTransferRecord] = []

        for host_path, rel_sandbox_path in artifacts:
            resolved_host = host_path.resolve()
            if not resolved_host.is_file():
                raise SandboxExecutionError(f"Host artifact '{host_path}' does not exist on disk.")

            # Validate relative sandbox path against directory traversal
            clean_rel = rel_sandbox_path.replace("\\", "/").strip("/")
            if ".." in clean_rel.split("/"):
                raise SandboxSecurityViolationError(
                    f"Path traversal detected in sandbox destination: {rel_sandbox_path}"
                )

            target_sandbox_path = (staging_dir / clean_rel).resolve()
            if not target_sandbox_path.is_relative_to(staging_dir):
                raise SandboxSecurityViolationError(
                    f"Artifact destination escapes sandbox workspace: {rel_sandbox_path}"
                )

            target_sandbox_path.parent.mkdir(parents=True, exist_ok=True)
            content = resolved_host.read_bytes()
            target_sandbox_path.write_bytes(content)

            sha256_hash = hashlib.sha256(content).hexdigest()
            transfers.append(
                ArtifactTransferRecord(
                    task_id=task_id,
                    sandbox_id=sandbox_id,
                    direction=ArtifactDirection.UPLOAD,
                    host_path=str(resolved_host),
                    sandbox_path=clean_rel,
                    file_size_bytes=len(content),
                    sha256_digest=sha256_hash,
                )
            )

        return transfers

    async def download_artifacts(
        self,
        sandbox_id: UUID,
        task_id: UUID,
        artifacts: list[tuple[str, Path]],
    ) -> list[ArtifactTransferRecord]:
        record = self._sandboxes.get(sandbox_id)
        if not record:
            raise SandboxExecutionError(f"Sandbox {sandbox_id} not found.")

        staging_dir: Path = record["staging_dir"]
        transfers: list[ArtifactTransferRecord] = []

        for rel_sandbox_path, host_destination in artifacts:
            clean_rel = rel_sandbox_path.replace("\\", "/").strip("/")
            if ".." in clean_rel.split("/"):
                raise SandboxSecurityViolationError(
                    f"Path traversal detected in sandbox source: {rel_sandbox_path}"
                )

            source_file = (staging_dir / clean_rel).resolve()
            if not source_file.is_file() or not source_file.is_relative_to(staging_dir):
                raise SandboxExecutionError(
                    f"Sandbox artifact '{rel_sandbox_path}' does not exist."
                )

            resolved_dest = host_destination.resolve()
            resolved_dest.parent.mkdir(parents=True, exist_ok=True)
            content = source_file.read_bytes()
            resolved_dest.write_bytes(content)

            sha256_hash = hashlib.sha256(content).hexdigest()
            transfers.append(
                ArtifactTransferRecord(
                    task_id=task_id,
                    sandbox_id=sandbox_id,
                    direction=ArtifactDirection.DOWNLOAD,
                    host_path=str(resolved_dest),
                    sandbox_path=clean_rel,
                    file_size_bytes=len(content),
                    sha256_digest=sha256_hash,
                )
            )

        return transfers

    async def inspect(self, sandbox_id: UUID) -> SandboxState:
        record = self._sandboxes.get(sandbox_id)
        if not record:
            return SandboxState.TERMINATED
        return record["state"]  # type: ignore[no-any-return]

    async def terminate(self, sandbox_id: UUID, reason: str = "normal_completion") -> SandboxState:
        record = self._sandboxes.get(sandbox_id)
        if not record:
            return SandboxState.TERMINATED

        record["state"] = SandboxState.QUIESCING
        container_name = record["container_name"]
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception as e:
            logger.warning(
                "docker_terminate_container_failed", container=container_name, error=str(e)
            )

        # Absence verification: Re-query engine to confirm container absence
        try:
            insp_proc = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await asyncio.wait_for(insp_proc.communicate(), timeout=5.0)
            stderr_str = stderr.decode(errors="replace").lower()
            if insp_proc.returncode != 0 and (
                "no such" in stderr_str
                or "error response from daemon: no such container" in stderr_str
                or "error: no such object" in stderr_str
            ):
                record["state"] = SandboxState.TERMINATED
                logger.info("sandbox_terminated", sandbox_id=str(sandbox_id), reason=reason)
                return SandboxState.TERMINATED
            else:
                record["state"] = SandboxState.UNKNOWN
                logger.warning(
                    "docker_terminate_absence_unverified",
                    container=container_name,
                    returncode=insp_proc.returncode,
                    stderr=stderr_str,
                )
                return SandboxState.UNKNOWN
        except Exception as e:
            record["state"] = SandboxState.UNKNOWN
            logger.warning(
                "docker_terminate_absence_check_failed",
                container=container_name,
                error=str(e),
            )
            return SandboxState.UNKNOWN

    async def cleanup(self, sandbox_id: UUID) -> None:
        record = self._sandboxes.pop(sandbox_id, None)
        if not record:
            return
        staging_dir: Path | None = record.get("staging_dir")
        if staging_dir and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        logger.debug("sandbox_cleanup_complete", sandbox_id=str(sandbox_id))

    async def get_health_report(self, run_smoke_test: bool = False) -> ProviderHealthReport:
        caps = get_system_capabilities()
        is_cli = caps.docker_cli_available == CapabilityStatus.SUPPORTED
        is_daemon = caps.docker_daemon_available == CapabilityStatus.SUPPORTED
        smoke_passed: bool | None = None
        is_healthy = is_daemon

        evidence: dict[str, Any] = {
            "docker_cli": is_cli,
            "docker_daemon": is_daemon,
            "docker_backend": caps.docker_backend.value,
            "server_version": caps.docker_server_version,
            "oci_runtime": caps.oci_runtime,
            "diagnostics": caps.diagnostics,
        }

        if is_daemon and run_smoke_test:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
                smoke_passed = proc.returncode == 0
                evidence["smoke_test_stdout"] = stdout.decode(errors="replace")[:1000]
                if not smoke_passed:
                    is_healthy = False
                    evidence["smoke_test_stderr"] = stderr.decode(errors="replace")[:1000]
            except Exception as e:
                logger.warning("docker_health_smoke_test_failed", error=str(e))
                smoke_passed = False
                is_healthy = False
                evidence["smoke_test_error"] = str(e)
        elif run_smoke_test and not is_daemon:
            smoke_passed = False

        features = {
            "cgroup_v2": caps.cgroup_v2_supported,
            "cpu_limit": caps.cpu_limit_supported,
            "memory_limit": caps.memory_limit_supported,
            "pids_limit": caps.pids_limit_supported,
            "seccomp": caps.seccomp_supported,
            "no_new_privileges": caps.no_new_privileges_supported,
            "drop_capabilities": caps.capability_drop_supported,
            "read_only_rootfs": caps.read_only_rootfs_supported,
            "network_none": caps.network_none_supported,
            "network_allowlist": caps.network_allowlist_supported,
            "rootless": caps.rootless_supported,
            "user_namespaces": caps.user_namespace_supported,
        }

        readiness = "READY" if is_healthy else ("UNAVAILABLE" if not is_daemon else "DEGRADED")
        return ProviderHealthReport(
            is_available=is_daemon,
            is_healthy=is_healthy,
            docker_cli_available=is_cli,
            backend_type=self.backend_type,
            docker_backend=caps.docker_backend,
            backend_version=caps.docker_server_version or caps.docker_version,
            server_version=caps.docker_server_version,
            runtime=caps.oci_runtime,
            supported_tiers=(self.supported_tier,) if is_daemon else (),
            supported_features=features,
            capacity_readiness=readiness,
            smoke_test_passed=smoke_passed,
            diagnostics=caps.diagnostics,
            evidence=evidence,
        )

    async def attest(self, sandbox_id: UUID) -> SandboxAttestation:
        """Inspect and attest the empirical security properties of an active container sandbox."""
        self._verify_engine_available()
        record = self._sandboxes.get(sandbox_id)
        if not record:
            raise SandboxExecutionError(f"Sandbox {sandbox_id} not found.")

        profile: SandboxProfile = record["profile"]
        container_name: str = record["container_name"]
        caps = get_system_capabilities()

        proc = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()
            return SandboxAttestation(
                sandbox_id=sandbox_id,
                profile_id=profile.profile_id,
                backend_type=self.backend_type,
                docker_backend=caps.docker_backend,
                backend_version=caps.docker_server_version or "",
                isolation_tier=self.supported_tier,
                provider_category=self.provider_category,
                status=AttestationStatus.FAILED,
                overall_state=AttestationStatus.FAILED,
                failures=(f"Docker inspect failed: {err_msg}",),
                unsatisfied_constraints=(f"Docker inspect failed: {err_msg}",),
                evidence={"inspect_error": err_msg},
            )

        try:
            inspect_data = json.loads(stdout.decode("utf-8"))
            if not inspect_data or not isinstance(inspect_data, list):
                raise ValueError("Unexpected inspect JSON format")
            info = inspect_data[0]
        except Exception as e:
            return SandboxAttestation(
                sandbox_id=sandbox_id,
                profile_id=profile.profile_id,
                backend_type=self.backend_type,
                docker_backend=caps.docker_backend,
                backend_version=caps.docker_server_version or "",
                isolation_tier=self.supported_tier,
                provider_category=self.provider_category,
                status=AttestationStatus.FAILED,
                overall_state=AttestationStatus.FAILED,
                failures=(f"Failed parsing inspect JSON: {e}",),
                unsatisfied_constraints=(f"Failed parsing inspect JSON: {e}",),
                evidence={"error": str(e)},
            )

        config = info.get("Config", {})
        host_config = info.get("HostConfig", {})
        mounts = info.get("Mounts", [])

        unsatisfied: list[str] = []
        evidence: dict[str, Any] = {
            "Config.User": config.get("User"),
            "HostConfig.ReadonlyRootfs": host_config.get("ReadonlyRootfs"),
            "HostConfig.SecurityOpt": host_config.get("SecurityOpt"),
            "HostConfig.CapDrop": host_config.get("CapDrop"),
            "HostConfig.NetworkMode": host_config.get("NetworkMode"),
            "HostConfig.PidsLimit": host_config.get("PidsLimit"),
            "HostConfig.Memory": host_config.get("Memory"),
            "HostConfig.NanoCpus": host_config.get("NanoCpus"),
            "Mounts": mounts,
        }

        # 1. Privilege Isolation
        sec_opts = [str(s).lower() for s in (host_config.get("SecurityOpt") or [])]
        user = str(config.get("User", ""))
        cap_drop = [str(c).upper() for c in (host_config.get("CapDrop") or [])]

        is_non_root = user != "" and user != "0" and user != "root" and user != "0:0"
        has_no_new_priv = any(
            "no-new-privileges:true" in s or "no-new-privileges" in s for s in sec_opts
        )
        has_cap_drop = "ALL" in cap_drop

        priv_status = CapabilityStatus.SUPPORTED
        if not is_non_root:
            unsatisfied.append(f"Workload configured as root user ('{user}')")
            priv_status = CapabilityStatus.UNSUPPORTED
        if profile.no_new_privileges and not has_no_new_priv:
            unsatisfied.append("no-new-privileges security option is not active")
            priv_status = CapabilityStatus.UNSUPPORTED
        if "ALL" in profile.drop_capabilities and not has_cap_drop:
            unsatisfied.append("Capability drop ALL is not active")
            priv_status = CapabilityStatus.UNSUPPORTED

        # 2. Filesystem Isolation
        readonly_active = bool(host_config.get("ReadonlyRootfs"))
        fs_status = CapabilityStatus.SUPPORTED
        if profile.read_only_rootfs and not readonly_active:
            unsatisfied.append("Root filesystem is not mounted read-only")
            fs_status = CapabilityStatus.UNSUPPORTED

        # Check mount boundaries
        for mount in mounts:
            src = str(mount.get("Source", "")).lower()
            dst = str(mount.get("Destination", "")).lower()
            if "docker.sock" in src or "docker.sock" in dst:
                unsatisfied.append(f"Docker socket mounted into container: {src}")
                fs_status = CapabilityStatus.UNSUPPORTED
            if src in ("/", "c:\\", "c:/", "/var/run", "/run"):
                unsatisfied.append(f"Dangerous host path mounted into container: {src}")
                fs_status = CapabilityStatus.UNSUPPORTED

        # 3. Network Isolation
        net_mode = str(host_config.get("NetworkMode", "")).lower()
        net_status = CapabilityStatus.SUPPORTED
        if profile.network_profile == NetworkProfile.NONE and net_mode != "none":
            unsatisfied.append(f"Network mode '{net_mode}' does not match requested NONE")
            net_status = CapabilityStatus.UNSUPPORTED

        # 4. Process Isolation
        pid_mode = str(host_config.get("PidMode", "")).lower()
        proc_status = CapabilityStatus.SUPPORTED
        if pid_mode == "host":
            unsatisfied.append("Host PID namespace exposed to container (PidMode=host)")
            proc_status = CapabilityStatus.UNSUPPORTED

        # 5. Resource Controls
        res = profile.resource_limits
        actual_pids = host_config.get("PidsLimit")
        actual_mem = host_config.get("Memory")
        res_status = CapabilityStatus.SUPPORTED
        if actual_pids is not None and actual_pids != res.pids_limit and actual_pids > 0:
            unsatisfied.append(f"PidsLimit {actual_pids} does not match requested {res.pids_limit}")
            res_status = CapabilityStatus.UNSUPPORTED
        expected_mem_bytes = res.memory_limit_mb * 1024 * 1024
        if actual_mem is not None and actual_mem != expected_mem_bytes and actual_mem > 0:
            unsatisfied.append(
                f"Memory limit {actual_mem} does not match requested {expected_mem_bytes}"
            )
            res_status = CapabilityStatus.UNSUPPORTED

        # 6. Docker Socket Access
        docker_sock_status = CapabilityStatus.SUPPORTED
        for mount in mounts:
            src = str(mount.get("Source", "")).lower()
            if "docker.sock" in src:
                docker_sock_status = CapabilityStatus.UNSUPPORTED

        # 7. Credential Exposure
        env_vars = config.get("Env") or []
        cred_status = CapabilityStatus.SUPPORTED
        secret_prefixes = (
            "GEMINI_",
            "OPENAI_",
            "ANTHROPIC_",
            "AWS_",
            "GITHUB_",
            "SSH_",
            "JARVIS_SECRET",
        )
        for ev in env_vars:
            key = ev.split("=")[0].upper()
            if any(key.startswith(p) for p in secret_prefixes):
                unsatisfied.append(f"Credential exposed in container environment: {key}")
                cred_status = CapabilityStatus.UNSUPPORTED

        if not unsatisfied:
            overall_status = AttestationStatus.VERIFIED
        elif any(
            "root" in u.lower() or "docker.sock" in u.lower() or "credential" in u.lower()
            for u in unsatisfied
        ):
            overall_status = AttestationStatus.FAILED
        else:
            overall_status = AttestationStatus.PARTIALLY_VERIFIED

        effective_cfg = EffectiveSecurityConfiguration(
            image=str(config.get("Image", "")),
            runtime=str(host_config.get("Runtime", "")),
            network_mode=net_mode,
            mounts=tuple(mounts),
            readonly_rootfs=readonly_active,
            drop_capabilities=tuple(cap_drop),
            add_capabilities=tuple(str(c).upper() for c in (host_config.get("CapAdd") or [])),
            security_options=tuple(sec_opts),
            user=user,
            pids_limit=host_config.get("PidsLimit"),
            memory_limit_bytes=host_config.get("Memory"),
            nano_cpus=host_config.get("NanoCpus"),
            env_keys=tuple(str(ev).split("=")[0] for ev in env_vars),
            workspace_mapping=str(record.get("staging_dir", "")),
        )

        cap_restriction_status = (
            CapabilityStatus.SUPPORTED if has_cap_drop else CapabilityStatus.UNSUPPORTED
        )
        ro_rootfs_status = (
            CapabilityStatus.SUPPORTED if readonly_active else CapabilityStatus.UNSUPPORTED
        )

        return SandboxAttestation(
            sandbox_id=sandbox_id,
            profile_id=profile.profile_id,
            backend_type=self.backend_type,
            docker_backend=caps.docker_backend,
            backend_version=caps.docker_server_version or "",
            isolation_tier=self.supported_tier,
            provider_category=self.provider_category,
            host_filesystem_boundary=fs_status,
            workspace_boundary=CapabilityStatus.SUPPORTED,
            docker_socket_boundary=docker_sock_status,
            credential_boundary=cred_status,
            process_namespace_boundary=proc_status,
            child_process_containment=CapabilityStatus.SUPPORTED,
            network_egress_policy=net_status,
            privilege_boundary=priv_status,
            capability_restriction=cap_restriction_status,
            read_only_rootfs=ro_rootfs_status,
            resource_limits=res_status,
            lifecycle_cleanup=CapabilityStatus.SUPPORTED,
            filesystem_isolation=fs_status,
            network_isolation=net_status,
            privilege_isolation=priv_status,
            process_isolation=proc_status,
            resource_controls=res_status,
            credential_exposure=cred_status,
            docker_socket_access=docker_sock_status,
            status=overall_status,
            overall_state=overall_status,
            effective_configuration=effective_cfg,
            failures=tuple(unsatisfied),
            unsatisfied_constraints=tuple(unsatisfied),
            evidence=evidence,
        )


class TestDoubleSandboxProvider(SandboxProvider):
    """Test double provider for lifecycle, contract, policy-selection, and artifact staging testing.

    IMPORTANT ARCHITECTURAL DISTINCTION:
    This class is classified as `ProviderCategory.TEST_DOUBLE`.
    It does NOT execute untrusted code in a host isolation boundary.
    It does NOT claim to prove host filesystem, process, network, or privilege isolation.
    """

    __test__ = False

    def __init__(
        self,
        simulate_timeout: bool = False,
        simulate_failure: bool = False,
        simulate_uncertain_termination: bool = False,
        simulate_unavailable: bool = False,
        supported_tier: IsolationTier = IsolationTier.TIER_1_CONTAINER,
    ) -> None:
        self._sandboxes: dict[UUID, dict[str, Any]] = {}
        self.simulate_timeout = simulate_timeout
        self.simulate_failure = simulate_failure
        self.simulate_uncertain_termination = simulate_uncertain_termination
        self.simulate_unavailable = simulate_unavailable
        self._supported_tier = supported_tier

    @property
    def provider_category(self) -> ProviderCategory:
        return ProviderCategory.TEST_DOUBLE

    @property
    def backend_type(self) -> BackendType:
        return BackendType.TEST_DOUBLE

    @property
    def supported_tier(self) -> IsolationTier:
        return self._supported_tier

    async def get_health_report(self, run_smoke_test: bool = False) -> ProviderHealthReport:
        is_avail = not self.simulate_unavailable
        is_healthy = is_avail and not self.simulate_failure
        smoke_passed: bool | None = None
        if run_smoke_test:
            smoke_passed = is_healthy
        readiness = "READY" if is_healthy else ("UNAVAILABLE" if not is_avail else "DEGRADED")
        return ProviderHealthReport(
            is_available=is_avail,
            is_healthy=is_healthy,
            docker_cli_available=True,
            backend_type=self.backend_type,
            docker_backend=DockerBackendType.UNAVAILABLE,
            backend_version="test-double-1.0",
            server_version="test-double-1.0",
            runtime="test-double-runtime",
            supported_tiers=(self.supported_tier,) if is_avail else (),
            supported_features={
                "cgroup_v2": CapabilityStatus.SUPPORTED,
                "cpu_limit": CapabilityStatus.SUPPORTED,
                "memory_limit": CapabilityStatus.SUPPORTED,
                "pids_limit": CapabilityStatus.SUPPORTED,
                "seccomp": CapabilityStatus.SUPPORTED,
                "no_new_privileges": CapabilityStatus.SUPPORTED,
                "drop_capabilities": CapabilityStatus.SUPPORTED,
                "read_only_rootfs": CapabilityStatus.SUPPORTED,
                "network_none": CapabilityStatus.SUPPORTED,
            },
            capacity_readiness=readiness,
            smoke_test_passed=smoke_passed,
            evidence={"is_test_double": True},
        )

    async def attest(self, sandbox_id: UUID) -> SandboxAttestation:
        """Attest test double sandbox, strictly reporting NOT_VERIFIED for real isolation."""
        record = self._sandboxes.get(sandbox_id)
        if not record:
            raise SandboxExecutionError(f"Sandbox {sandbox_id} not found.")

        profile: SandboxProfile = record["profile"]
        return SandboxAttestation(
            sandbox_id=sandbox_id,
            profile_id=profile.profile_id,
            backend_type=self.backend_type,
            docker_backend=DockerBackendType.UNAVAILABLE,
            backend_version="test-double-1.0",
            isolation_tier=self.supported_tier,
            provider_category=self.provider_category,
            host_filesystem_boundary=CapabilityStatus.NOT_APPLICABLE,
            workspace_boundary=CapabilityStatus.NOT_APPLICABLE,
            docker_socket_boundary=CapabilityStatus.NOT_APPLICABLE,
            credential_boundary=CapabilityStatus.NOT_APPLICABLE,
            process_namespace_boundary=CapabilityStatus.NOT_APPLICABLE,
            child_process_containment=CapabilityStatus.NOT_APPLICABLE,
            network_egress_policy=CapabilityStatus.NOT_APPLICABLE,
            privilege_boundary=CapabilityStatus.NOT_APPLICABLE,
            capability_restriction=CapabilityStatus.NOT_APPLICABLE,
            read_only_rootfs=CapabilityStatus.NOT_APPLICABLE,
            resource_limits=CapabilityStatus.NOT_APPLICABLE,
            lifecycle_cleanup=CapabilityStatus.NOT_APPLICABLE,
            filesystem_isolation=CapabilityStatus.NOT_APPLICABLE,
            network_isolation=CapabilityStatus.NOT_APPLICABLE,
            privilege_isolation=CapabilityStatus.NOT_APPLICABLE,
            process_isolation=CapabilityStatus.NOT_APPLICABLE,
            resource_controls=CapabilityStatus.NOT_APPLICABLE,
            credential_exposure=CapabilityStatus.NOT_APPLICABLE,
            docker_socket_access=CapabilityStatus.NOT_APPLICABLE,
            status=AttestationStatus.NOT_VERIFIED,
            overall_state=AttestationStatus.NOT_VERIFIED,
            unsatisfied_constraints=(
                "Test double provider does not execute workloads on a real kernel isolation boundary.",
            ),
            evidence={
                "is_test_double": True,
                "note": "Attestation status is strictly NOT_VERIFIED for test doubles.",
            },
        )

    async def create(
        self,
        task_id: UUID,
        profile: SandboxProfile,
        request_id: UUID | None = None,
        session_id: UUID | None = None,
        agent_id: str = "coding",
    ) -> SandboxIdentity:
        if self.simulate_unavailable:
            raise SandboxBackendUnavailableError("Test double backend is offline/unavailable.")
        if profile.allow_docker_socket:
            raise SandboxSecurityViolationError("Docker socket exposure prohibited.")
        if profile.allow_credential_injection:
            raise SandboxSecurityViolationError("Credential injection prohibited.")

        sandbox_id = uuid4()
        staging_dir = Path(tempfile.mkdtemp(prefix=f"test_double_sbx_{sandbox_id.hex[:8]}_"))
        now = datetime.now(UTC)

        identity = SandboxIdentity(
            sandbox_id=sandbox_id,
            task_id=task_id,
            session_id=session_id,
            agent_id=agent_id,
            owner="test_double",
            profile_id=profile.profile_id,
            backend_type=self.backend_type,
            isolation_tier=self.supported_tier,
            provider_category=self.provider_category,
            created_at=now,
            expires_at=datetime.fromtimestamp(now.timestamp() + 300, tz=UTC),
        )

        self._sandboxes[sandbox_id] = {
            "identity": identity,
            "profile": profile,
            "state": SandboxState.CREATED,
            "staging_dir": staging_dir,
            "simulated_files": {},
        }
        return identity

    async def start(self, sandbox_id: UUID) -> SandboxState:
        if self.simulate_unavailable:
            raise SandboxBackendUnavailableError("Test double backend is offline/unavailable.")
        record = self._sandboxes.get(sandbox_id)
        if not record:
            raise SandboxExecutionError(f"Sandbox {sandbox_id} not found.")
        if self.simulate_failure:
            record["state"] = SandboxState.FAILED
            raise SandboxExecutionError("Simulated backend startup failure.")
        record["state"] = SandboxState.READY
        return SandboxState.READY

    async def health_check(self, sandbox_id: UUID) -> bool:
        record = self._sandboxes.get(sandbox_id)
        return bool(record and record["state"] == SandboxState.READY and not self.simulate_failure)

    async def execute(
        self,
        sandbox_id: UUID,
        command: list[str],
        task_id: UUID,
        capability_id: str,
        logical_effect_id: str | None = None,
        attempt_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> SandboxExecutionResult:
        record = self._sandboxes.get(sandbox_id)
        if not record:
            raise SandboxExecutionError(f"Sandbox {sandbox_id} not found.")

        if self.simulate_timeout:
            record["state"] = SandboxState.TIMED_OUT
            raise SandboxTimeoutError("Simulated execution timeout.")

        if self.simulate_failure:
            record["state"] = SandboxState.FAILED
            raise SandboxExecutionError("Simulated execution failure.")

        started_at = datetime.now(UTC)
        record["state"] = SandboxState.EXECUTING
        await asyncio.sleep(0.01)  # Context switch
        record["state"] = SandboxState.READY

        # Deterministic simulation output reflecting the invocation
        cmd_str = " ".join(command)
        stdout_sim = f"[TEST_DOUBLE_SIMULATED_OUTPUT]: Executed '{cmd_str}' in test double context."
        profile: SandboxProfile = record["profile"]

        return SandboxExecutionResult(
            sandbox_id=sandbox_id,
            task_id=task_id,
            exit_code=0,
            stdout=stdout_sim,
            stderr="",
            duration_seconds=0.01,
            timed_out=False,
            state=SandboxState.READY,
            backend_type=self.backend_type,
            isolation_tier=self.supported_tier,
            provider_category=self.provider_category,
            network_profile=profile.network_profile,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            resource_usage={"simulated_pids": 1, "simulated_memory_mb": 12},
        )

    async def upload_artifacts(
        self,
        sandbox_id: UUID,
        task_id: UUID,
        artifacts: list[tuple[Path, str]],
    ) -> list[ArtifactTransferRecord]:
        record = self._sandboxes.get(sandbox_id)
        if not record:
            raise SandboxExecutionError(f"Sandbox {sandbox_id} not found.")

        staging_dir: Path = record["staging_dir"]
        transfers: list[ArtifactTransferRecord] = []

        for host_path, rel_sandbox_path in artifacts:
            resolved_host = host_path.resolve()
            if not resolved_host.is_file():
                raise SandboxExecutionError(f"Host artifact '{host_path}' does not exist.")

            clean_rel = rel_sandbox_path.replace("\\", "/").strip("/")
            if ".." in clean_rel.split("/"):
                raise SandboxSecurityViolationError(f"Path traversal detected: {rel_sandbox_path}")

            target = (staging_dir / clean_rel).resolve()
            if not target.is_relative_to(staging_dir):
                raise SandboxSecurityViolationError(f"Artifact escapes staging: {rel_sandbox_path}")

            target.parent.mkdir(parents=True, exist_ok=True)
            content = resolved_host.read_bytes()
            target.write_bytes(content)

            sha256_hash = hashlib.sha256(content).hexdigest()
            transfers.append(
                ArtifactTransferRecord(
                    task_id=task_id,
                    sandbox_id=sandbox_id,
                    direction=ArtifactDirection.UPLOAD,
                    host_path=str(resolved_host),
                    sandbox_path=clean_rel,
                    file_size_bytes=len(content),
                    sha256_digest=sha256_hash,
                )
            )

        return transfers

    async def download_artifacts(
        self,
        sandbox_id: UUID,
        task_id: UUID,
        artifacts: list[tuple[str, Path]],
    ) -> list[ArtifactTransferRecord]:
        record = self._sandboxes.get(sandbox_id)
        if not record:
            raise SandboxExecutionError(f"Sandbox {sandbox_id} not found.")

        staging_dir: Path = record["staging_dir"]
        transfers: list[ArtifactTransferRecord] = []

        for rel_sandbox_path, host_destination in artifacts:
            clean_rel = rel_sandbox_path.replace("\\", "/").strip("/")
            if ".." in clean_rel.split("/"):
                raise SandboxSecurityViolationError(f"Path traversal detected: {rel_sandbox_path}")

            source = (staging_dir / clean_rel).resolve()
            if not source.is_file() or not source.is_relative_to(staging_dir):
                raise SandboxExecutionError(
                    f"Sandbox artifact '{rel_sandbox_path}' does not exist."
                )

            dest = host_destination.resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = source.read_bytes()
            dest.write_bytes(content)

            sha256_hash = hashlib.sha256(content).hexdigest()
            transfers.append(
                ArtifactTransferRecord(
                    task_id=task_id,
                    sandbox_id=sandbox_id,
                    direction=ArtifactDirection.DOWNLOAD,
                    host_path=str(dest),
                    sandbox_path=clean_rel,
                    file_size_bytes=len(content),
                    sha256_digest=sha256_hash,
                )
            )

        return transfers

    async def inspect(self, sandbox_id: UUID) -> SandboxState:
        if self.simulate_unavailable:
            raise SandboxBackendUnavailableError("Test double backend is offline/unavailable.")
        if self.simulate_uncertain_termination:
            return SandboxState.UNKNOWN
        record = self._sandboxes.get(sandbox_id)
        if not record:
            return SandboxState.TERMINATED
        return record["state"]  # type: ignore[no-any-return]

    async def terminate(self, sandbox_id: UUID, reason: str = "normal_completion") -> SandboxState:
        if self.simulate_unavailable:
            raise SandboxBackendUnavailableError("Test double backend is offline/unavailable.")
        record = self._sandboxes.get(sandbox_id)
        if not record:
            return SandboxState.TERMINATED
        if self.simulate_uncertain_termination:
            record["state"] = SandboxState.UNKNOWN
            return SandboxState.UNKNOWN
        record["state"] = SandboxState.TERMINATED
        return SandboxState.TERMINATED

    async def cleanup(self, sandbox_id: UUID) -> None:
        if self.simulate_unavailable:
            raise SandboxBackendUnavailableError("Test double backend is offline/unavailable.")
        record = self._sandboxes.pop(sandbox_id, None)
        if not record:
            return
        staging_dir: Path | None = record.get("staging_dir")
        if staging_dir and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
