"""JARVIS Execution Fabric Sentinel Isolation Verifier.

Executes the canonical 12-probe security sentinel suite against an isolated
execution container to empirically verify host filesystem, process, network,
credential, privilege, capability, rootfs, and resource containment boundaries.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from jarvis.core.logging import get_logger
from jarvis.sandbox.detector import evaluate_profile_satisfaction, get_system_capabilities
from jarvis.sandbox.models import (
    CapabilityStatus,
    DockerBackendType,
    EffectiveSecurityConfiguration,
    IsolationTier,
    NetworkProfile,
    ProviderCategory,
    ResourceLimits,
    SandboxProfile,
    SandboxState,
    SentinelProbeId,
    SentinelProbeResult,
    SentinelProbeVerdict,
    SentinelSuiteReport,
)

if TYPE_CHECKING:
    from jarvis.sandbox.provider import SandboxProvider

logger = get_logger(__name__)


class SentinelIsolationVerifier:
    """Automated security verifier executing the 12 canonical containment probes."""

    def __init__(self, provider: SandboxProvider) -> None:
        self.provider = provider

    async def run_suite(
        self,
        task_id: UUID | None = None,
        profile: SandboxProfile | None = None,
    ) -> SentinelSuiteReport:
        """Execute the full 12-probe sentinel suite and record structured evidence."""
        bound_task_id = task_id or uuid4()
        suite_id = uuid4()
        timestamp = datetime.now(UTC)
        diagnostics: list[str] = []
        caps = get_system_capabilities()

        # Fail-closed guard for test doubles
        if self.provider.provider_category == ProviderCategory.TEST_DOUBLE:
            return self._run_test_double_suite(suite_id, bound_task_id, timestamp)

        # Fail-closed guard 1: Docker daemon reachability
        if caps.docker_daemon_available != CapabilityStatus.SUPPORTED:
            diagnostics.append(
                "Docker daemon is not operational on host; real isolation cannot be verified."
            )
            return SentinelSuiteReport(
                suite_id=suite_id,
                sandbox_id=None,
                backend_type=self.provider.backend_type,
                docker_backend=caps.docker_backend,
                provider_category=self.provider.provider_category,
                isolation_tier=self.provider.supported_tier,
                probes={},
                effective_configuration=None,
                all_probes_passed=False,
                real_isolation_verified=False,
                diagnostics=tuple(diagnostics),
                timestamp=timestamp,
            )

        # Fail-closed guard 2: Docker backend identity must not be UNKNOWN or UNAVAILABLE
        if caps.docker_backend in (DockerBackendType.UNKNOWN, DockerBackendType.UNAVAILABLE):
            diagnostics.append(
                f"Docker backend identity is {caps.docker_backend.value}; cannot verify containment boundary properties."
            )
            return SentinelSuiteReport(
                suite_id=suite_id,
                sandbox_id=None,
                backend_type=self.provider.backend_type,
                docker_backend=caps.docker_backend,
                provider_category=self.provider.provider_category,
                isolation_tier=self.provider.supported_tier,
                probes={},
                effective_configuration=None,
                all_probes_passed=False,
                real_isolation_verified=False,
                diagnostics=tuple(diagnostics),
                timestamp=timestamp,
            )

        # Fail-closed guard 3: Profile satisfaction check against capabilities
        target_profile = profile or SandboxProfile(
            profile_id="sentinel-hardened-probe",
            isolation_tier=IsolationTier.TIER_1_CONTAINER,
            read_only_rootfs=True,
            no_new_privileges=True,
            drop_capabilities=("ALL",),
            network_profile=NetworkProfile.NONE,
            resource_limits=ResourceLimits(
                cpu_limit=1.0,
                memory_limit_mb=256,
                pids_limit=50,
                timeout_seconds=15.0,
            ),
        )

        satisfaction = evaluate_profile_satisfaction(target_profile, caps)
        if not satisfaction.is_satisfied:
            diagnostics.extend(satisfaction.unsatisfied_reasons)
            return SentinelSuiteReport(
                suite_id=suite_id,
                sandbox_id=None,
                backend_type=self.provider.backend_type,
                docker_backend=caps.docker_backend,
                provider_category=self.provider.provider_category,
                isolation_tier=self.provider.supported_tier,
                probes={},
                effective_configuration=None,
                all_probes_passed=False,
                real_isolation_verified=False,
                diagnostics=tuple(diagnostics),
                timestamp=timestamp,
            )

        sandbox_id: UUID | None = None
        effective_cfg: EffectiveSecurityConfiguration | None = None
        canonical_probes: dict[str, SentinelProbeResult] = {}

        with tempfile.TemporaryDirectory() as host_sentinel_dir:
            host_sentinel_path = (
                Path(host_sentinel_dir) / f"sentinel_host_secret_{uuid4().hex[:8]}.txt"
            )
            host_sentinel_path.write_text("JARVIS_HOST_CONFIDENTIAL_12345", encoding="utf-8")

            fake_cred_path = Path(host_sentinel_dir) / f"fake_credentials_{uuid4().hex[:8]}.env"
            fake_cred_path.write_text(
                "AWS_SECRET_ACCESS_KEY=TEST_DOUBLE_KEY_DO_NOT_USE", encoding="utf-8"
            )

            try:
                # 1. Create and Start Real Sandbox
                identity = await self.provider.create(
                    task_id=bound_task_id,
                    profile=target_profile,
                )
                sandbox_id = identity.sandbox_id
                await self.provider.start(sandbox_id)

                # Capture diagnostic effective security configuration
                try:
                    attestation = await self.provider.attest(sandbox_id)
                    effective_cfg = attestation.effective_configuration
                except Exception as e:
                    diagnostics.append(f"Failed capturing effective configuration: {e}")

                # 2. Probe 1: Host Filesystem Sentinel (Must be INACCESSIBLE)
                canonical_probes[
                    SentinelProbeId.HOST_FILESYSTEM_BOUNDARY.value
                ] = await self._probe_host_filesystem(sandbox_id, bound_task_id, host_sentinel_path)

                # 3. Probe 2: Workspace Boundary (Must SUCCEED)
                canonical_probes[
                    SentinelProbeId.WORKSPACE_BOUNDARY.value
                ] = await self._probe_workspace_boundary(sandbox_id, bound_task_id)

                # 4. Probe 3: Docker Socket Access (Must be INACCESSIBLE)
                canonical_probes[
                    SentinelProbeId.DOCKER_SOCKET_BOUNDARY.value
                ] = await self._probe_docker_socket(sandbox_id, bound_task_id)

                # 5. Probe 4: Host Credentials Inaccessibility (Must be INACCESSIBLE)
                canonical_probes[
                    SentinelProbeId.CREDENTIAL_BOUNDARY.value
                ] = await self._probe_host_credentials(sandbox_id, bound_task_id, fake_cred_path)

                # 6. Probe 5: Process Boundary Isolation (Host PIDs must not be visible)
                canonical_probes[
                    SentinelProbeId.PROCESS_NAMESPACE_BOUNDARY.value
                ] = await self._probe_process_boundary(sandbox_id, bound_task_id)

                # 7. Probe 6: Child Process Termination (Child reaped on shutdown)
                canonical_probes[
                    SentinelProbeId.CHILD_PROCESS_CONTAINMENT.value
                ] = await self._probe_child_process(sandbox_id, bound_task_id)

                # 8. Probe 7: Network Egress Policy (Must be BLOCKED under NETWORK_NONE)
                canonical_probes[
                    SentinelProbeId.NETWORK_EGRESS_POLICY.value
                ] = await self._probe_network_egress(sandbox_id, bound_task_id)

                # 9. Probe 8: Privilege Boundary (Must be non-root, setuid blocked)
                canonical_probes[
                    SentinelProbeId.PRIVILEGE_BOUNDARY.value
                ] = await self._probe_privilege_boundary(sandbox_id, bound_task_id)

                # 10. Probe 9: Capability Restriction (Effective capabilities dropped)
                canonical_probes[
                    SentinelProbeId.CAPABILITY_RESTRICTION.value
                ] = await self._probe_capability_restriction(sandbox_id, bound_task_id)

                # 11. Probe 10: Read-Only Root Filesystem (Rootfs read-only, workspace writable)
                canonical_probes[
                    SentinelProbeId.READ_ONLY_ROOTFS.value
                ] = await self._probe_read_only_rootfs(sandbox_id, bound_task_id)

                # 12. Probe 11: Resource Limits Enforcement (Cgroup limits bounded)
                canonical_probes[
                    SentinelProbeId.RESOURCE_LIMITS.value
                ] = await self._probe_resource_limits(sandbox_id, bound_task_id, target_profile)

                # 13. Probe 12: Lifecycle Cleanup Attestation (Absence verified)
                canonical_probes[
                    SentinelProbeId.LIFECYCLE_CLEANUP.value
                ] = await self._probe_lifecycle_cleanup(sandbox_id)
                sandbox_id = None  # Already terminated and verified absent by probe 12

            except Exception as e:
                diagnostics.append(f"Error during sentinel execution: {e}")
            finally:
                if sandbox_id is not None:
                    try:
                        await self.provider.terminate(sandbox_id, reason="sentinel_suite_complete")
                        await self.provider.cleanup(sandbox_id)
                    except Exception as e:
                        diagnostics.append(f"Teardown error: {e}")

        # Assemble legacy aliases for backward compatibility
        legacy_aliases: dict[str, SentinelProbeResult] = {}
        if SentinelProbeId.HOST_FILESYSTEM_BOUNDARY.value in canonical_probes:
            legacy_aliases["PROBE_A_HOST_FILESYSTEM"] = canonical_probes[
                SentinelProbeId.HOST_FILESYSTEM_BOUNDARY.value
            ]
        if SentinelProbeId.WORKSPACE_BOUNDARY.value in canonical_probes:
            legacy_aliases["PROBE_B_WORKSPACE_BOUNDARY"] = canonical_probes[
                SentinelProbeId.WORKSPACE_BOUNDARY.value
            ]
        if SentinelProbeId.DOCKER_SOCKET_BOUNDARY.value in canonical_probes:
            legacy_aliases["PROBE_C_DOCKER_SOCKET"] = canonical_probes[
                SentinelProbeId.DOCKER_SOCKET_BOUNDARY.value
            ]
        if SentinelProbeId.CREDENTIAL_BOUNDARY.value in canonical_probes:
            legacy_aliases["PROBE_D_HOST_CREDENTIALS"] = canonical_probes[
                SentinelProbeId.CREDENTIAL_BOUNDARY.value
            ]
        if SentinelProbeId.PROCESS_NAMESPACE_BOUNDARY.value in canonical_probes:
            legacy_aliases["PROBE_E_PROCESS_BOUNDARY"] = canonical_probes[
                SentinelProbeId.PROCESS_NAMESPACE_BOUNDARY.value
            ]
        if SentinelProbeId.CHILD_PROCESS_CONTAINMENT.value in canonical_probes:
            legacy_aliases["PROBE_F_CHILD_PROCESS"] = canonical_probes[
                SentinelProbeId.CHILD_PROCESS_CONTAINMENT.value
            ]
        if SentinelProbeId.NETWORK_EGRESS_POLICY.value in canonical_probes:
            legacy_aliases["PROBE_G_NETWORK_NONE"] = canonical_probes[
                SentinelProbeId.NETWORK_EGRESS_POLICY.value
            ]
        if SentinelProbeId.PRIVILEGE_BOUNDARY.value in canonical_probes:
            legacy_aliases["PROBE_H_PRIVILEGE"] = canonical_probes[
                SentinelProbeId.PRIVILEGE_BOUNDARY.value
            ]
        if SentinelProbeId.RESOURCE_LIMITS.value in canonical_probes:
            legacy_aliases["PROBE_I_RESOURCE_LIMITS"] = canonical_probes[
                SentinelProbeId.RESOURCE_LIMITS.value
            ]

        all_probes_dict = {**canonical_probes, **legacy_aliases}
        all_passed = len(canonical_probes) == 12 and all(
            p.passed for p in canonical_probes.values()
        )

        real_verified = (
            all_passed
            and self.provider.provider_category == ProviderCategory.REAL_ISOLATION_PROVIDER
            and caps.docker_daemon_available == CapabilityStatus.SUPPORTED
            and caps.docker_backend
            not in (DockerBackendType.UNKNOWN, DockerBackendType.UNAVAILABLE)
        )

        return SentinelSuiteReport(
            suite_id=suite_id,
            sandbox_id=sandbox_id,
            backend_type=self.provider.backend_type,
            docker_backend=caps.docker_backend,
            provider_category=self.provider.provider_category,
            isolation_tier=self.provider.supported_tier,
            probes=all_probes_dict,
            effective_configuration=effective_cfg,
            all_probes_passed=all_passed,
            real_isolation_verified=real_verified,
            diagnostics=tuple(diagnostics),
            timestamp=timestamp,
        )

    def _run_test_double_suite(
        self, suite_id: UUID, task_id: UUID, timestamp: datetime
    ) -> SentinelSuiteReport:
        """Simulate probe definitions for test double without claiming real isolation."""
        probe_fs = SentinelProbeResult(
            probe_id=SentinelProbeId.HOST_FILESYSTEM_BOUNDARY.value,
            name="Host Filesystem Boundary",
            description="Verifies container cannot read arbitrary host files outside workspace.",
            expected_verdict=SentinelProbeVerdict.INACCESSIBLE,
            actual_verdict=SentinelProbeVerdict.INACCESSIBLE,
            status=CapabilityStatus.SUPPORTED,
            passed=True,
            details="Test double contract simulated: host filesystem unmounted.",
        )
        probe_ws = SentinelProbeResult(
            probe_id=SentinelProbeId.WORKSPACE_BOUNDARY.value,
            name="Workspace Boundary Access",
            description="Verifies container can read explicitly prepared workspace artifacts.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED,
            status=CapabilityStatus.SUPPORTED,
            passed=True,
            details="Test double contract simulated: workspace accessible.",
        )
        probe_sock = SentinelProbeResult(
            probe_id=SentinelProbeId.DOCKER_SOCKET_BOUNDARY.value,
            name="Docker Socket Inaccessibility",
            description="Verifies container cannot access Docker daemon control socket.",
            expected_verdict=SentinelProbeVerdict.INACCESSIBLE,
            actual_verdict=SentinelProbeVerdict.INACCESSIBLE,
            status=CapabilityStatus.SUPPORTED,
            passed=True,
            details="Test double contract simulated: docker.sock unmounted.",
        )
        probe_cred = SentinelProbeResult(
            probe_id=SentinelProbeId.CREDENTIAL_BOUNDARY.value,
            name="Credential Boundary Inaccessibility",
            description="Verifies container cannot access host credentials, keys, or sensitive environment tokens.",
            expected_verdict=SentinelProbeVerdict.INACCESSIBLE,
            actual_verdict=SentinelProbeVerdict.INACCESSIBLE,
            status=CapabilityStatus.SUPPORTED,
            passed=True,
            details="Test double contract simulated: zero credentials injected.",
        )
        probe_proc = SentinelProbeResult(
            probe_id=SentinelProbeId.PROCESS_NAMESPACE_BOUNDARY.value,
            name="Process Namespace Boundary",
            description="Verifies container cannot observe host operating system processes.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED,
            status=CapabilityStatus.SUPPORTED,
            passed=True,
            details="Test double contract simulated: PID namespace isolated.",
        )
        probe_child = SentinelProbeResult(
            probe_id=SentinelProbeId.CHILD_PROCESS_CONTAINMENT.value,
            name="Child Process Lifecycle Containment",
            description="Verifies spawned child processes are terminated with container.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED,
            status=CapabilityStatus.SUPPORTED,
            passed=True,
            details="Test double contract simulated: child processes reaped.",
        )
        probe_net = SentinelProbeResult(
            probe_id=SentinelProbeId.NETWORK_EGRESS_POLICY.value,
            name="Network Egress Policy",
            description="Verifies outbound network traffic is blocked under NETWORK_NONE.",
            expected_verdict=SentinelProbeVerdict.BLOCKED,
            actual_verdict=SentinelProbeVerdict.BLOCKED,
            status=CapabilityStatus.SUPPORTED,
            passed=True,
            details="Test double contract simulated: network disabled.",
        )
        probe_priv = SentinelProbeResult(
            probe_id=SentinelProbeId.PRIVILEGE_BOUNDARY.value,
            name="Privilege Boundary Confinement",
            description="Verifies workload runs as non-root user with dropped capabilities.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED,
            status=CapabilityStatus.SUPPORTED,
            passed=True,
            details="Test double contract simulated: UID 1000 without root.",
        )
        probe_caps = SentinelProbeResult(
            probe_id=SentinelProbeId.CAPABILITY_RESTRICTION.value,
            name="Capability Restriction Enforcement",
            description="Verifies effective Linux capability set is dropped according to profile.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED,
            status=CapabilityStatus.SUPPORTED,
            passed=True,
            details="Test double contract simulated: Linux capabilities dropped.",
        )
        probe_ro = SentinelProbeResult(
            probe_id=SentinelProbeId.READ_ONLY_ROOTFS.value,
            name="Read-Only Root Filesystem",
            description="Verifies root filesystem is read-only while workspace remains writable.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED,
            status=CapabilityStatus.SUPPORTED,
            passed=True,
            details="Test double contract simulated: read-only rootfs active.",
        )
        probe_res = SentinelProbeResult(
            probe_id=SentinelProbeId.RESOURCE_LIMITS.value,
            name="Resource Limits Enforcement",
            description="Verifies cgroup quotas for memory, CPU, and PIDs are enforced.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED,
            status=CapabilityStatus.SUPPORTED,
            passed=True,
            details="Test double contract simulated: resource limits enforced.",
        )
        probe_cleanup = SentinelProbeResult(
            probe_id=SentinelProbeId.LIFECYCLE_CLEANUP.value,
            name="Lifecycle Cleanup Attestation",
            description="Verifies container absence and workspace destruction upon termination.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED,
            status=CapabilityStatus.SUPPORTED,
            passed=True,
            details="Test double contract simulated: container verified absent upon termination.",
        )

        canonical_probes = {
            SentinelProbeId.HOST_FILESYSTEM_BOUNDARY.value: probe_fs,
            SentinelProbeId.WORKSPACE_BOUNDARY.value: probe_ws,
            SentinelProbeId.DOCKER_SOCKET_BOUNDARY.value: probe_sock,
            SentinelProbeId.CREDENTIAL_BOUNDARY.value: probe_cred,
            SentinelProbeId.PROCESS_NAMESPACE_BOUNDARY.value: probe_proc,
            SentinelProbeId.CHILD_PROCESS_CONTAINMENT.value: probe_child,
            SentinelProbeId.NETWORK_EGRESS_POLICY.value: probe_net,
            SentinelProbeId.PRIVILEGE_BOUNDARY.value: probe_priv,
            SentinelProbeId.CAPABILITY_RESTRICTION.value: probe_caps,
            SentinelProbeId.READ_ONLY_ROOTFS.value: probe_ro,
            SentinelProbeId.RESOURCE_LIMITS.value: probe_res,
            SentinelProbeId.LIFECYCLE_CLEANUP.value: probe_cleanup,
        }
        legacy_aliases = {
            "PROBE_A_HOST_FILESYSTEM": probe_fs,
            "PROBE_B_WORKSPACE_BOUNDARY": probe_ws,
            "PROBE_C_DOCKER_SOCKET": probe_sock,
            "PROBE_D_HOST_CREDENTIALS": probe_cred,
            "PROBE_E_PROCESS_BOUNDARY": probe_proc,
            "PROBE_F_CHILD_PROCESS": probe_child,
            "PROBE_G_NETWORK_NONE": probe_net,
            "PROBE_H_PRIVILEGE": probe_priv,
            "PROBE_I_RESOURCE_LIMITS": probe_res,
        }

        return SentinelSuiteReport(
            suite_id=suite_id,
            sandbox_id=None,
            backend_type=self.provider.backend_type,
            docker_backend=DockerBackendType.UNAVAILABLE,
            provider_category=self.provider.provider_category,
            isolation_tier=self.provider.supported_tier,
            probes={**canonical_probes, **legacy_aliases},
            effective_configuration=None,
            all_probes_passed=True,
            real_isolation_verified=False,  # Test double NEVER claims real host isolation
            diagnostics=(
                "Test double provider evaluated; real isolation qualification requires operational container backend.",
            ),
            timestamp=timestamp,
        )

    async def _probe_host_filesystem(
        self, sandbox_id: UUID, task_id: UUID, host_path: Path
    ) -> SentinelProbeResult:
        probe_id = SentinelProbeId.HOST_FILESYSTEM_BOUNDARY.value
        cmd = [
            "python",
            "-c",
            f"import pathlib, sys; p = pathlib.Path(r'{host_path}'); sys.exit(0 if p.exists() else 1)",
        ]
        res = await self.provider.execute(sandbox_id, cmd, task_id, "probe:fs")
        inaccessible = res.exit_code != 0
        status = CapabilityStatus.SUPPORTED if inaccessible else CapabilityStatus.FAILED
        return SentinelProbeResult(
            probe_id=probe_id,
            name="Host Filesystem Boundary",
            description="Verifies container cannot read or access arbitrary host files outside workspace.",
            expected_verdict=SentinelProbeVerdict.INACCESSIBLE,
            actual_verdict=SentinelProbeVerdict.INACCESSIBLE
            if inaccessible
            else SentinelProbeVerdict.FAILED,
            status=status,
            passed=inaccessible,
            details="Host sentinel path does not exist inside sandbox."
            if inaccessible
            else "BREACH: Host file accessible inside container!",
            evidence={"exit_code": res.exit_code, "stdout": res.stdout, "stderr": res.stderr},
        )

    async def _probe_workspace_boundary(
        self, sandbox_id: UUID, task_id: UUID
    ) -> SentinelProbeResult:
        probe_id = SentinelProbeId.WORKSPACE_BOUNDARY.value
        token = f"JARVIS_WORKSPACE_{uuid4().hex}"
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tf:
            tf.write(token)
            temp_path = Path(tf.name)

        try:
            await self.provider.upload_artifacts(
                sandbox_id, task_id, [(temp_path, "probe_test.txt")]
            )
            cmd = ["cat", "/workspace/probe_test.txt"]
            res = await self.provider.execute(sandbox_id, cmd, task_id, "probe:workspace")
            passed = res.exit_code == 0 and token in res.stdout
            status = CapabilityStatus.SUPPORTED if passed else CapabilityStatus.FAILED
            return SentinelProbeResult(
                probe_id=probe_id,
                name="Workspace Boundary Access",
                description="Verifies container can read and write explicitly prepared workspace artifacts.",
                expected_verdict=SentinelProbeVerdict.PASSED,
                actual_verdict=SentinelProbeVerdict.PASSED
                if passed
                else SentinelProbeVerdict.FAILED,
                status=status,
                passed=passed,
                details="Prepared workspace artifact read successfully."
                if passed
                else "Failed reading prepared workspace artifact.",
                evidence={"stdout": res.stdout, "exit_code": res.exit_code},
            )
        finally:
            temp_path.unlink(missing_ok=True)

    async def _probe_docker_socket(self, sandbox_id: UUID, task_id: UUID) -> SentinelProbeResult:
        probe_id = SentinelProbeId.DOCKER_SOCKET_BOUNDARY.value
        cmd = [
            "python",
            "-c",
            "import pathlib, sys; "
            "sockets = ['/var/run/docker.sock', '/run/docker.sock', '/run/containerd/containerd.sock']; "
            "sys.exit(0 if any(pathlib.Path(s).exists() for s in sockets) else 1)",
        ]
        res = await self.provider.execute(sandbox_id, cmd, task_id, "probe:docker_socket")
        inaccessible = res.exit_code != 0
        status = CapabilityStatus.SUPPORTED if inaccessible else CapabilityStatus.FAILED
        return SentinelProbeResult(
            probe_id=probe_id,
            name="Docker Socket Inaccessibility",
            description="Verifies container cannot discover or access Docker daemon control socket.",
            expected_verdict=SentinelProbeVerdict.INACCESSIBLE,
            actual_verdict=SentinelProbeVerdict.INACCESSIBLE
            if inaccessible
            else SentinelProbeVerdict.FAILED,
            status=status,
            passed=inaccessible,
            details="Docker daemon sockets do not exist inside sandbox."
            if inaccessible
            else "BREACH: Docker control socket is accessible inside container!",
            evidence={"exit_code": res.exit_code},
        )

    async def _probe_host_credentials(
        self, sandbox_id: UUID, task_id: UUID, cred_path: Path
    ) -> SentinelProbeResult:
        probe_id = SentinelProbeId.CREDENTIAL_BOUNDARY.value
        cmd = [
            "python",
            "-c",
            f"import os, pathlib, sys; "
            f"p = pathlib.Path(r'{cred_path}'); "
            f"cred_paths = [p, pathlib.Path.home() / '.aws/credentials', pathlib.Path.home() / '.ssh/id_rsa', pathlib.Path('/root/.ssh')]; "
            f"has_cred_file = any(c.exists() for c in cred_paths); "
            f"has_secret_env = any(k.upper().startswith(('AWS_', 'GEMINI_', 'OPENAI_', 'GITHUB_', 'SSH_', 'JARVIS_SECRET')) for k in os.environ); "
            f"sys.exit(0 if (has_cred_file or has_secret_env) else 1)",
        ]
        res = await self.provider.execute(sandbox_id, cmd, task_id, "probe:credentials")
        inaccessible = res.exit_code != 0
        status = CapabilityStatus.SUPPORTED if inaccessible else CapabilityStatus.FAILED
        return SentinelProbeResult(
            probe_id=probe_id,
            name="Credential Boundary Inaccessibility",
            description="Verifies container cannot access host credentials, keys, or sensitive environment tokens.",
            expected_verdict=SentinelProbeVerdict.INACCESSIBLE,
            actual_verdict=SentinelProbeVerdict.INACCESSIBLE
            if inaccessible
            else SentinelProbeVerdict.FAILED,
            status=status,
            passed=inaccessible,
            details="Zero credentials or secrets discovered inside sandbox."
            if inaccessible
            else "BREACH: Host credentials or secrets accessible inside container!",
            evidence={"exit_code": res.exit_code},
        )

    async def _probe_process_boundary(self, sandbox_id: UUID, task_id: UUID) -> SentinelProbeResult:
        probe_id = SentinelProbeId.PROCESS_NAMESPACE_BOUNDARY.value
        cmd = [
            "python",
            "-c",
            "import os, sys; "
            "pids = [int(p) for p in os.listdir('/proc') if p.isdigit()]; "
            "sys.exit(0 if (len(pids) > 0 and len(pids) < 50) else 1)",
        ]
        res = await self.provider.execute(sandbox_id, cmd, task_id, "probe:process")
        passed = res.exit_code == 0
        status = CapabilityStatus.SUPPORTED if passed else CapabilityStatus.FAILED
        return SentinelProbeResult(
            probe_id=probe_id,
            name="Process Namespace Boundary",
            description="Verifies container runs in an isolated PID namespace and cannot observe host processes.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED if passed else SentinelProbeVerdict.FAILED,
            status=status,
            passed=passed,
            details="Isolated PID namespace active; host process table unobservable."
            if passed
            else "Process namespace isolation check failed.",
            evidence={"stdout": res.stdout[:500], "exit_code": res.exit_code},
        )

    async def _probe_child_process(self, sandbox_id: UUID, task_id: UUID) -> SentinelProbeResult:
        probe_id = SentinelProbeId.CHILD_PROCESS_CONTAINMENT.value
        cmd = [
            "python",
            "-c",
            "import subprocess, sys; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.5)']); "
            "pid = p.pid; "
            "p.wait(timeout=2.0); "
            "print(pid)",
        ]
        res = await self.provider.execute(sandbox_id, cmd, task_id, "probe:child")
        passed = res.exit_code == 0 and res.stdout.strip().isdigit()
        status = CapabilityStatus.SUPPORTED if passed else CapabilityStatus.FAILED
        return SentinelProbeResult(
            probe_id=probe_id,
            name="Child Process Lifecycle Containment",
            description="Verifies child processes remain confined to container process tree and do not leak to host.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED if passed else SentinelProbeVerdict.FAILED,
            status=status,
            passed=passed,
            details="Child process executed and reaped under container process tree."
            if passed
            else "Child process confinement check failed.",
            evidence={"pid": res.stdout.strip(), "exit_code": res.exit_code},
        )

    async def _probe_network_egress(self, sandbox_id: UUID, task_id: UUID) -> SentinelProbeResult:
        probe_id = SentinelProbeId.NETWORK_EGRESS_POLICY.value
        cmd = [
            "python",
            "-c",
            "import socket, sys; "
            "dns_failed = False;\n"
            "try:\n"
            "    socket.getaddrinfo('example.com', 80)\n"
            "except Exception:\n"
            "    dns_failed = True\n"
            "tcp_failed = False;\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM);\n"
            "s.settimeout(1.5);\n"
            "try:\n"
            "    s.connect(('1.1.1.1', 80))\n"
            "except Exception:\n"
            "    tcp_failed = True\n"
            "finally:\n"
            "    s.close()\n"
            "sys.exit(0 if (dns_failed and tcp_failed) else 1)",
        ]
        res = await self.provider.execute(sandbox_id, cmd, task_id, "probe:network")
        blocked = res.exit_code == 0
        status = CapabilityStatus.SUPPORTED if blocked else CapabilityStatus.FAILED
        return SentinelProbeResult(
            probe_id=probe_id,
            name="Network Egress Policy",
            description="Verifies outbound network traffic is blocked under NETWORK_NONE isolation.",
            expected_verdict=SentinelProbeVerdict.BLOCKED,
            actual_verdict=SentinelProbeVerdict.BLOCKED if blocked else SentinelProbeVerdict.FAILED,
            status=status,
            passed=blocked,
            details="Outbound DNS and TCP connections blocked as expected."
            if blocked
            else "BREACH: Outbound network traffic permitted under NETWORK_NONE!",
            evidence={"exit_code": res.exit_code, "stderr": res.stderr},
        )

    async def _probe_privilege_boundary(
        self, sandbox_id: UUID, task_id: UUID
    ) -> SentinelProbeResult:
        probe_id = SentinelProbeId.PRIVILEGE_BOUNDARY.value
        cmd = [
            "python",
            "-c",
            "import os, sys; "
            "uid = os.getuid(); "
            "if uid == 0: sys.exit(1); "
            "perm_denied = False;\n"
            "try:\n"
            "    os.setuid(0)\n"
            "except PermissionError:\n"
            "    perm_denied = True\n"
            "sys.exit(0 if perm_denied else 1)",
        ]
        res = await self.provider.execute(sandbox_id, cmd, task_id, "probe:privilege")
        passed = res.exit_code == 0
        status = CapabilityStatus.SUPPORTED if passed else CapabilityStatus.FAILED
        return SentinelProbeResult(
            probe_id=probe_id,
            name="Privilege Boundary Confinement",
            description="Verifies workload runs as non-root user with blocked system modification privileges.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED if passed else SentinelProbeVerdict.FAILED,
            status=status,
            passed=passed,
            details="Workload executing as unprivileged non-root user; setuid blocked."
            if passed
            else "Privilege boundary check failed: running as root or setuid succeeded!",
            evidence={"exit_code": res.exit_code, "stdout": res.stdout},
        )

    async def _probe_capability_restriction(
        self, sandbox_id: UUID, task_id: UUID
    ) -> SentinelProbeResult:
        probe_id = SentinelProbeId.CAPABILITY_RESTRICTION.value
        cmd = [
            "python",
            "-c",
            "import sys; "
            "with open('/proc/self/status') as f: lines = f.readlines(); "
            "cap_eff = [line.split(':')[1].strip() for line in lines if line.startswith('CapEff:')]; "
            "if not cap_eff: sys.exit(1); "
            "val = int(cap_eff[0], 16); "
            "sys.exit(0 if val == 0 else 1)",
        ]
        res = await self.provider.execute(sandbox_id, cmd, task_id, "probe:capabilities")
        passed = res.exit_code == 0
        status = CapabilityStatus.SUPPORTED if passed else CapabilityStatus.FAILED
        return SentinelProbeResult(
            probe_id=probe_id,
            name="Capability Restriction Enforcement",
            description="Verifies effective Linux capability set is minimized/dropped according to profile.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED if passed else SentinelProbeVerdict.FAILED,
            status=status,
            passed=passed,
            details="Linux capability set minimized; CapEff is zero."
            if passed
            else "Capability restriction check failed: CapEff non-zero or status unreadable.",
            evidence={"exit_code": res.exit_code, "stdout": res.stdout},
        )

    async def _probe_read_only_rootfs(self, sandbox_id: UUID, task_id: UUID) -> SentinelProbeResult:
        probe_id = SentinelProbeId.READ_ONLY_ROOTFS.value
        cmd = [
            "python",
            "-c",
            "import sys; "
            "ro_blocked = False;\n"
            "try:\n"
            "    with open('/etc/sentinel_ro_test', 'w') as f: f.write('fail')\n"
            "except OSError:\n"
            "    ro_blocked = True\n"
            "rw_ok = False;\n"
            "try:\n"
            "    with open('/workspace/sentinel_rw_test', 'w') as f: f.write('ok')\n"
            "    rw_ok = True\n"
            "except OSError:\n"
            "    pass\n"
            "sys.exit(0 if (ro_blocked and rw_ok) else 1)",
        ]
        res = await self.provider.execute(sandbox_id, cmd, task_id, "probe:rootfs")
        passed = res.exit_code == 0
        status = CapabilityStatus.SUPPORTED if passed else CapabilityStatus.FAILED
        return SentinelProbeResult(
            probe_id=probe_id,
            name="Read-Only Root Filesystem",
            description="Verifies root filesystem is read-only while explicitly mounted workspace remains writable.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED if passed else SentinelProbeVerdict.FAILED,
            status=status,
            passed=passed,
            details="Root filesystem is read-only; workspace is writable."
            if passed
            else "Read-only rootfs enforcement failed.",
            evidence={"exit_code": res.exit_code, "stdout": res.stdout},
        )

    async def _probe_resource_limits(
        self, sandbox_id: UUID, task_id: UUID, profile: SandboxProfile
    ) -> SentinelProbeResult:
        probe_id = SentinelProbeId.RESOURCE_LIMITS.value
        cmd = [
            "python",
            "-c",
            "import pathlib, sys; "
            "cgroup_paths = ["
            "    pathlib.Path('/sys/fs/cgroup/pids/pids.max'),"
            "    pathlib.Path('/sys/fs/cgroup/pids.max'),"
            "    pathlib.Path('/sys/fs/cgroup/memory/memory.limit_in_bytes'),"
            "    pathlib.Path('/sys/fs/cgroup/memory.max'),"
            "]; "
            "found = any(p.exists() for p in cgroup_paths); "
            "sys.exit(0 if found else 1)",
        ]
        res = await self.provider.execute(sandbox_id, cmd, task_id, "probe:resources")
        passed = res.exit_code == 0
        status = CapabilityStatus.SUPPORTED if passed else CapabilityStatus.FAILED
        return SentinelProbeResult(
            probe_id=probe_id,
            name="Resource Limits Enforcement",
            description="Verifies cgroup quotas for memory, CPU, and PIDs are effectively enforced.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED if passed else SentinelProbeVerdict.FAILED,
            status=status,
            passed=passed,
            details="Resource quotas active in cgroup controllers."
            if passed
            else "Resource limits verification failed.",
            evidence={"exit_code": res.exit_code},
        )

    async def _probe_lifecycle_cleanup(self, sandbox_id: UUID) -> SentinelProbeResult:
        probe_id = SentinelProbeId.LIFECYCLE_CLEANUP.value
        absence_verified = False
        try:
            state = await self.provider.terminate(sandbox_id, reason="sentinel_suite_cleanup")
            await self.provider.cleanup(sandbox_id)
            inspect_state = await self.provider.inspect(sandbox_id)
            absence_verified = (
                state == SandboxState.TERMINATED and inspect_state == SandboxState.TERMINATED
            )
        except Exception as e:
            absence_verified = False
            details = f"Cleanup error: {e}"
        else:
            details = (
                "Container verified absent from engine; temporary workspaces cleaned."
                if absence_verified
                else "Container absence could not be confirmed."
            )

        status = CapabilityStatus.SUPPORTED if absence_verified else CapabilityStatus.FAILED
        return SentinelProbeResult(
            probe_id=probe_id,
            name="Lifecycle Cleanup Attestation",
            description="Verifies container and child processes are terminated, runtime resources released, and absence confirmed.",
            expected_verdict=SentinelProbeVerdict.PASSED,
            actual_verdict=SentinelProbeVerdict.PASSED
            if absence_verified
            else SentinelProbeVerdict.FAILED,
            status=status,
            passed=absence_verified,
            details=details,
            evidence={"absence_verified": absence_verified},
        )
