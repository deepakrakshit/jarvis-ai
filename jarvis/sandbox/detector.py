"""JARVIS Backend Capability Detector.

Discovers host virtualization and container isolation capabilities across
Windows, WSL2, Docker Desktop engines, and native Linux environments.
Evaluates SandboxProfile requirement satisfaction and enforces the fail-closed invariant.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from jarvis.core.logging import get_logger
from jarvis.sandbox.models import (
    BackendResolutionResult,
    CapabilityStatus,
    DockerBackendType,
    EvidenceClassification,
    EvidenceProvenance,
    IsolationTier,
    NetworkProfile,
    ProfileSatisfactionResult,
    SandboxProfile,
    SystemCapabilities,
)

logger = get_logger(__name__)


class BackendIdentityResolver:
    """Pure logic engine that interprets multi-source evidence to resolve Docker backend identity.

    Evaluates consistency between configuration and runtime evidence, detects contradictions,
    and enforces fail-closed behavior (resolving to UNKNOWN) whenever affirmative proof is lacking.

    Separates:
    - Container isolation technology (e.g. Isolation='hyperv' for Windows containers)
    - Docker Desktop VM/backend selection (WSL2 vs Hyper-V vs Docker VMM)
    """

    @staticmethod
    def resolve(
        evidence_list: list[EvidenceProvenance] | tuple[EvidenceProvenance, ...],
        host_os: str,
        daemon_available: bool,
    ) -> BackendResolutionResult:
        if not daemon_available:
            return BackendResolutionResult(
                active_backend=DockerBackendType.UNAVAILABLE,
                configured_backend=DockerBackendType.UNAVAILABLE,
                confidence="HIGH",
                contradictions=(),
                evidence=tuple(evidence_list),
            )

        evidence_by_source: dict[str, EvidenceProvenance] = {e.source: e for e in evidence_list}
        contradictions: list[str] = []

        # 1. Determine Configured Backend (from authoritative configuration files)
        configured_backend = DockerBackendType.UNKNOWN
        settings_ev = evidence_by_source.get("docker_settings")
        if settings_ev and settings_ev.availability == CapabilityStatus.SUPPORTED:
            if settings_ev.normalized_value == "WSL2":
                configured_backend = DockerBackendType.WSL2
            elif settings_ev.normalized_value == "NON_WSL":
                # Disabling WSL2 alone does NOT establish Hyper-V or Docker VMM without authoritative proof
                configured_backend = DockerBackendType.UNKNOWN

        # 2. Native Linux Host Evaluation
        if host_os == "linux":
            kernel_ev = evidence_by_source.get("docker_info_kernel")
            kernel_str = str(kernel_ev.observed_value).lower() if kernel_ev else ""
            if "microsoft" not in kernel_str and "wsl" not in kernel_str:
                return BackendResolutionResult(
                    active_backend=DockerBackendType.NATIVE_LINUX,
                    configured_backend=DockerBackendType.NATIVE_LINUX,
                    confidence="HIGH",
                    contradictions=(),
                    evidence=tuple(evidence_list),
                )

        # 3. Extract Runtime Signals
        kernel_ev = evidence_by_source.get("docker_info_kernel")
        wsl_runtime_ev = evidence_by_source.get("wsl_runtime")

        kernel_str = str(kernel_ev.observed_value).lower() if kernel_ev else ""

        # Affirmative WSL2 runtime markers
        has_wsl2_kernel = (
            "wsl2" in kernel_str
            or "-microsoft-standard-wsl2" in kernel_str
            or "microsoft-standard-wsl" in kernel_str
            or (kernel_str != "" and "wsl" in kernel_str)
        )
        has_wsl2_runtime_distro = (
            wsl_runtime_ev is not None
            and wsl_runtime_ev.availability == CapabilityStatus.SUPPORTED
            and wsl_runtime_ev.normalized_value == "WSL2_RUNNING"
        )

        has_affirmative_wsl2 = has_wsl2_kernel or has_wsl2_runtime_distro

        # Note on Hyper-V and Docker VMM:
        # docker_info_isolation (Isolation == "hyperv") describes Windows container isolation
        # technology, NOT Docker Desktop's Linux VM backend. It must NEVER resolve Hyper-V.
        # Likewise, neither Hyper-V nor Docker VMM currently has a documented, authoritative runtime
        # indicator in docker info for the Linux container VM. Without affirmative proof, the resolver
        # does not guess Hyper-V or Docker VMM.

        # 4. Check for Contradictions
        # Contradiction: Configured WSL2 (wslEngineEnabled=true) but active runtime daemon kernel is not WSL2
        if (
            configured_backend == DockerBackendType.WSL2
            and kernel_str
            and not has_wsl2_kernel
            and "microsoft" not in kernel_str
        ):
            contradictions.append(
                "Configured backend is WSL2 (wslEngineEnabled=true), but active Docker daemon kernel is not a WSL2 kernel."
            )

        # Contradiction: Configured non-WSL (wslEngineEnabled=false) but active runtime kernel is WSL2
        if settings_ev and settings_ev.normalized_value == "NON_WSL" and has_wsl2_kernel:
            contradictions.append(
                "Configured backend (wslEngineEnabled=false) contradicts active WSL2 kernel."
            )

        if contradictions:
            return BackendResolutionResult(
                active_backend=DockerBackendType.UNKNOWN,
                configured_backend=configured_backend,
                confidence="LOW",
                contradictions=tuple(contradictions),
                evidence=tuple(evidence_list),
            )

        # 5. Affirmative Resolution
        if has_affirmative_wsl2:
            confidence = (
                "HIGH"
                if (configured_backend == DockerBackendType.WSL2 or has_wsl2_runtime_distro)
                else "MEDIUM"
            )
            return BackendResolutionResult(
                active_backend=DockerBackendType.WSL2,
                configured_backend=configured_backend,
                confidence=confidence,
                contradictions=(),
                evidence=tuple(evidence_list),
            )

        # 6. Ambiguous / Insufficient Evidence -> Fail Closed to UNKNOWN without guessing
        return BackendResolutionResult(
            active_backend=DockerBackendType.UNKNOWN,
            configured_backend=configured_backend,
            confidence="NONE",
            contradictions=(),
            evidence=tuple(evidence_list),
        )


class CapabilityDetector:
    """Probes system environment and returns a structured SystemCapabilities report."""

    def __init__(self, command_timeout: float = 3.0) -> None:
        self.command_timeout = command_timeout

    def detect(self) -> SystemCapabilities:
        """Run all system checks and compile fine-grained capability profile."""
        host_os = platform.system().lower()
        host_arch = platform.machine().lower()
        diagnostics: list[str] = []
        evidence_list: list[EvidenceProvenance] = []

        # 1. WSL Platform & User Distro Discovery (Windows host)
        wsl_platform_status = CapabilityStatus.UNAVAILABLE
        user_wsl_distros_status = CapabilityStatus.UNAVAILABLE
        wsl_distros: list[str] = []
        if host_os == "windows":
            wsl_exe = shutil.which("wsl.exe") or shutil.which("wsl")
            if wsl_exe:
                wsl_platform_status = CapabilityStatus.SUPPORTED
                evidence_list.append(
                    EvidenceProvenance(
                        source="wsl_platform",
                        observed_value=str(wsl_exe),
                        normalized_value="SUPPORTED",
                        classification=EvidenceClassification.CORROBORATING_EVIDENCE,
                        confidence="HIGH",
                        availability=CapabilityStatus.SUPPORTED,
                    )
                )
                wsl_distros, wsl_evs = self._probe_wsl_runtime_evidence(wsl_exe, diagnostics)
                evidence_list.extend(wsl_evs)
                # Decouple user distros (e.g. Ubuntu, Debian) from Docker Desktop's internal environment
                user_distros = [
                    d for d in wsl_distros if not d.lower().startswith("docker-desktop")
                ]
                user_wsl_distros_status = (
                    CapabilityStatus.SUPPORTED
                    if len(user_distros) > 0
                    else CapabilityStatus.UNAVAILABLE
                )
            else:
                diagnostics.append("WSL executable not found on Windows host.")
                evidence_list.append(
                    EvidenceProvenance(
                        source="wsl_platform",
                        observed_value=None,
                        normalized_value="UNAVAILABLE",
                        classification=EvidenceClassification.CORROBORATING_EVIDENCE,
                        confidence="HIGH",
                        availability=CapabilityStatus.UNAVAILABLE,
                    )
                )
        else:
            wsl_platform_status = CapabilityStatus.NOT_APPLICABLE
            user_wsl_distros_status = CapabilityStatus.NOT_APPLICABLE

        # 2. Docker Desktop Installation & Version Discovery
        docker_desktop_status = self._probe_docker_desktop_installed(host_os, diagnostics)
        docker_desktop_ver, ver_evs = self._probe_docker_desktop_version(diagnostics)
        evidence_list.extend(ver_evs)

        # 3. Docker Settings Discovery (Configuration Evidence)
        _, settings_evs = self._probe_docker_settings(diagnostics)
        evidence_list.extend(settings_evs)

        # 4. Docker Desktop Engine Discovery (CLI Engine Evidence)
        engine_evs = self._probe_desktop_cli_engine(diagnostics)
        evidence_list.extend(engine_evs)

        # 5. Docker CLI Discovery
        docker_cli = shutil.which("docker")
        docker_cli_status = (
            CapabilityStatus.SUPPORTED if docker_cli else CapabilityStatus.UNAVAILABLE
        )
        if not docker_cli:
            diagnostics.append("Docker CLI executable not found in system PATH.")

        # 6. Docker Daemon & Runtime Discovery
        docker_daemon_status = CapabilityStatus.UNAVAILABLE
        docker_server_version: str | None = None
        docker_version: str | None = None
        oci_runtime: str | None = None

        rootless_status = CapabilityStatus.UNAVAILABLE
        user_ns_status = CapabilityStatus.UNAVAILABLE
        seccomp_status = CapabilityStatus.UNAVAILABLE
        cgroup_v2_status = CapabilityStatus.UNAVAILABLE
        cpu_limit_status = CapabilityStatus.UNAVAILABLE
        mem_limit_status = CapabilityStatus.UNAVAILABLE
        pids_limit_status = CapabilityStatus.UNAVAILABLE
        read_only_status = CapabilityStatus.UNAVAILABLE
        net_none_status = CapabilityStatus.UNAVAILABLE
        net_allowlist_status = CapabilityStatus.UNAVAILABLE
        no_new_priv_status = CapabilityStatus.UNAVAILABLE
        cap_drop_status = CapabilityStatus.UNAVAILABLE
        mount_iso_status = CapabilityStatus.UNAVAILABLE

        if docker_cli:
            docker_info = self._probe_docker_daemon(diagnostics)
            if docker_info:
                docker_daemon_status = CapabilityStatus.SUPPORTED
                docker_server_version = str(docker_info.get("ServerVersion", ""))
                oci_runtime = str(docker_info.get("DefaultRuntime", "runc"))

                kernel_ver = str(docker_info.get("KernelVersion", "")).strip()
                op_sys = str(docker_info.get("OperatingSystem", "")).strip()
                isolation = str(docker_info.get("Isolation", "")).strip()
                system_status = docker_info.get("SystemStatus")

                evidence_list.extend(
                    [
                        EvidenceProvenance(
                            source="docker_info_kernel",
                            observed_value=kernel_ver,
                            normalized_value=kernel_ver,
                            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
                            confidence="HIGH" if kernel_ver else "LOW",
                            availability=CapabilityStatus.SUPPORTED,
                        ),
                        EvidenceProvenance(
                            source="docker_info_os",
                            observed_value=op_sys,
                            normalized_value=op_sys,
                            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
                            confidence="HIGH" if op_sys else "LOW",
                            availability=CapabilityStatus.SUPPORTED,
                        ),
                        EvidenceProvenance(
                            source="docker_info_isolation",
                            observed_value=isolation,
                            normalized_value=isolation,
                            classification=EvidenceClassification.CONTAINER_ISOLATION_EVIDENCE,
                            confidence="HIGH" if isolation else "LOW",
                            availability=CapabilityStatus.SUPPORTED,
                        ),
                        EvidenceProvenance(
                            source="docker_info_system_status",
                            observed_value=system_status,
                            normalized_value=str(system_status) if system_status else "",
                            classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
                            confidence="MEDIUM" if system_status else "LOW",
                            availability=CapabilityStatus.SUPPORTED,
                        ),
                    ]
                )

                sec_opts = [str(opt).lower() for opt in (docker_info.get("SecurityOptions") or [])]
                rootless_status = (
                    CapabilityStatus.SUPPORTED
                    if any("rootless" in s for s in sec_opts)
                    else CapabilityStatus.UNSUPPORTED
                )
                user_ns_status = (
                    CapabilityStatus.SUPPORTED
                    if any("userns" in s for s in sec_opts)
                    else CapabilityStatus.UNSUPPORTED
                )
                seccomp_status = (
                    CapabilityStatus.SUPPORTED
                    if any("seccomp" in s for s in sec_opts)
                    else CapabilityStatus.UNSUPPORTED
                )

                cgroup_v2 = docker_info.get("CgroupVersion") == "2"
                cgroup_v2_status = (
                    CapabilityStatus.SUPPORTED if cgroup_v2 else CapabilityStatus.UNSUPPORTED
                )
                cgroup_driver = str(docker_info.get("CgroupDriver", "")).lower()

                # Inspect Docker warnings for missing cgroup controllers
                raw_warnings = docker_info.get("Warnings") or []
                warn_text = " ".join(str(w).lower() for w in raw_warnings)

                # Memory limit controller
                mem_supported = (
                    docker_info.get("MemoryLimit") is not False
                    and "no memory limit" not in warn_text
                )
                mem_limit_status = (
                    CapabilityStatus.SUPPORTED if mem_supported else CapabilityStatus.UNSUPPORTED
                )

                # CPU CFS quota controller
                cpu_supported = (
                    docker_info.get("CpuCfsQuota") is not False
                    and "no cpu cfs quota" not in warn_text
                )
                cpu_limit_status = (
                    CapabilityStatus.SUPPORTED if cpu_supported else CapabilityStatus.UNSUPPORTED
                )

                # PIDs limit controller
                pids_supported = (
                    docker_info.get("PidsLimit") is not False
                    and "no pids limit" not in warn_text
                    and (cgroup_v2 or host_os == "linux")
                )
                pids_limit_status = (
                    CapabilityStatus.SUPPORTED if pids_supported else CapabilityStatus.UNSUPPORTED
                )

                # Rootless mode caveat: cgroup limits require cgroup v2 + systemd driver
                if rootless_status == CapabilityStatus.SUPPORTED and not (
                    cgroup_v2 and cgroup_driver == "systemd"
                ):
                    diagnostics.append(
                        "Rootless Docker detected without cgroup v2 systemd delegation; cgroup limits are not enforceable."
                    )
                    cpu_limit_status = CapabilityStatus.UNSUPPORTED
                    mem_limit_status = CapabilityStatus.UNSUPPORTED
                    pids_limit_status = CapabilityStatus.UNSUPPORTED

                read_only_status = CapabilityStatus.SUPPORTED
                net_none_status = CapabilityStatus.SUPPORTED
                net_allowlist_status = CapabilityStatus.SUPPORTED
                no_new_priv_status = CapabilityStatus.SUPPORTED
                cap_drop_status = CapabilityStatus.SUPPORTED
                mount_iso_status = CapabilityStatus.SUPPORTED

        # 7. Pure Backend Identity Resolution
        resolution = BackendIdentityResolver.resolve(
            evidence_list=evidence_list,
            host_os=host_os,
            daemon_available=(docker_daemon_status == CapabilityStatus.SUPPORTED),
        )

        docker_backend = resolution.active_backend
        configured_backend = resolution.configured_backend
        backend_confidence = resolution.confidence
        backend_contradictions = resolution.contradictions

        backend_evidence: dict[str, Any] = {
            "active_backend": docker_backend.value,
            "configured_backend": configured_backend.value,
            "confidence": backend_confidence,
            "contradictions": list(backend_contradictions),
            "evidence_records": [e.model_dump(mode="json") for e in evidence_list],
        }

        # 8. gVisor (runsc) and Kata Containers Discovery
        gvisor_status = (
            CapabilityStatus.SUPPORTED if shutil.which("runsc") else CapabilityStatus.UNAVAILABLE
        )
        kata_status = (
            CapabilityStatus.SUPPORTED
            if shutil.which("kata-runtime")
            else CapabilityStatus.UNAVAILABLE
        )

        if gvisor_status != CapabilityStatus.SUPPORTED:
            diagnostics.append("gVisor (runsc) runtime not installed.")
        if kata_status != CapabilityStatus.SUPPORTED:
            diagnostics.append("Kata Containers (kata-runtime) not installed.")

        # 9. Supported Tiers Resolution
        supported_tiers: list[IsolationTier] = [IsolationTier.TIER_0_LOCAL]
        if docker_daemon_status == CapabilityStatus.SUPPORTED:
            supported_tiers.append(IsolationTier.TIER_1_CONTAINER)
        if gvisor_status == CapabilityStatus.SUPPORTED:
            supported_tiers.append(IsolationTier.TIER_2_APPLICATION_SANDBOX)
        if kata_status == CapabilityStatus.SUPPORTED:
            supported_tiers.append(IsolationTier.TIER_3_MICROVM)

        caps = SystemCapabilities(
            host_os=host_os,
            host_arch=host_arch,
            wsl_platform_available=wsl_platform_status,
            user_wsl_distros_present=user_wsl_distros_status,
            wsl_distributions=tuple(wsl_distros),
            wsl_available=wsl_platform_status,
            docker_desktop_installed=docker_desktop_status,
            docker_desktop_version=docker_desktop_ver,
            docker_cli_available=docker_cli_status,
            docker_daemon_available=docker_daemon_status,
            docker_version=docker_version,
            docker_server_version=docker_server_version,
            docker_backend=docker_backend,
            configured_backend=configured_backend,
            backend_confidence=backend_confidence,
            backend_contradictions=backend_contradictions,
            oci_runtime=oci_runtime,
            backend_detection_evidence=backend_evidence,
            rootless_supported=rootless_status,
            user_namespace_supported=user_ns_status,
            seccomp_supported=seccomp_status,
            cgroup_v2_supported=cgroup_v2_status,
            cpu_limit_supported=cpu_limit_status,
            memory_limit_supported=mem_limit_status,
            pids_limit_supported=pids_limit_status,
            read_only_rootfs_supported=read_only_status,
            network_none_supported=net_none_status,
            network_allowlist_supported=net_allowlist_status,
            no_new_privileges_supported=no_new_priv_status,
            capability_drop_supported=cap_drop_status,
            mount_isolation_supported=mount_iso_status,
            artifact_staging_supported=CapabilityStatus.SUPPORTED,
            gvisor_available=gvisor_status,
            kata_available=kata_status,
            supported_tiers=tuple(supported_tiers),
            diagnostics=tuple(diagnostics),
        )

        logger.debug(
            "system_capabilities_detected",
            host_os=host_os,
            wsl_platform=wsl_platform_status.value,
            user_wsl_distros=user_wsl_distros_status.value,
            docker_desktop=docker_desktop_status.value,
            docker_status=docker_daemon_status.value,
            docker_backend=docker_backend.value,
            supported_tiers=[t.value for t in supported_tiers],
        )
        return caps

    def _probe_wsl_distributions(self, wsl_path: str, diagnostics: list[str]) -> list[str]:
        """Inspect installed WSL2 Linux distributions."""
        distros, _ = self._probe_wsl_runtime_evidence(wsl_path, diagnostics)
        return distros

    def _probe_wsl_runtime_evidence(
        self, wsl_path: str, diagnostics: list[str]
    ) -> tuple[list[str], list[EvidenceProvenance]]:
        """Inspect installed WSL2 distributions and Docker-managed distribution running states."""
        try:
            res = subprocess.run(
                [wsl_path, "-l", "-v"],
                capture_output=True,
                timeout=self.command_timeout,
            )
            raw = res.stdout
            text = (
                raw.decode("utf-16le", errors="ignore")
                if b"\x00" in raw
                else raw.decode("utf-8", errors="ignore")
            )
            if "no installed distributions" in text.lower():
                diagnostics.append(
                    "WSL engine present, but 0 user Linux distributions are installed "
                    "(note: Docker Desktop WSL2 backend uses an internal Docker-managed environment "
                    "and does not require user distributions)."
                )
                ev = EvidenceProvenance(
                    source="wsl_runtime",
                    observed_value="no installed distributions",
                    normalized_value="ZERO_DISTROS",
                    classification=EvidenceClassification.CORROBORATING_EVIDENCE,
                    confidence="HIGH",
                    availability=CapabilityStatus.SUPPORTED,
                )
                return [], [ev]

            lines = [line.strip() for line in text.splitlines() if line.strip()]
            distros: list[str] = []
            header_found = False
            has_running_docker_desktop = False
            for line in lines:
                parts = line.split()
                if not parts:
                    continue
                if "NAME" in [p.upper() for p in parts] and "STATE" in [p.upper() for p in parts]:
                    header_found = True
                    continue
                if header_found:
                    name = parts[0].lstrip("*").strip()
                    if name:
                        distros.append(name)
                        # Check if this is a running Docker-managed distro under WSL2
                        name_lower = name.lower()
                        if name_lower.startswith("docker-desktop"):
                            state = parts[1].lower() if len(parts) > 1 else ""
                            ver = parts[2] if len(parts) > 2 else ""
                            if "running" in state and "2" in ver:
                                has_running_docker_desktop = True

            normalized_wsl = (
                "WSL2_RUNNING"
                if has_running_docker_desktop
                else (
                    "WSL2_PRESENT"
                    if any(d.lower().startswith("docker-desktop") for d in distros)
                    else "NO_DOCKER_WSL_DISTRO"
                )
            )
            ev = EvidenceProvenance(
                source="wsl_runtime",
                observed_value={
                    "distributions": distros,
                    "docker_running": has_running_docker_desktop,
                },
                normalized_value=normalized_wsl,
                classification=EvidenceClassification.CORROBORATING_EVIDENCE,
                confidence="HIGH",
                availability=CapabilityStatus.SUPPORTED,
            )
            return distros, [ev]
        except Exception as e:
            diagnostics.append(f"Failed probing WSL runtime evidence: {e}")
            ev = EvidenceProvenance(
                source="wsl_runtime",
                observed_value=str(e),
                normalized_value="ERROR",
                classification=EvidenceClassification.CORROBORATING_EVIDENCE,
                confidence="LOW",
                availability=CapabilityStatus.UNAVAILABLE,
            )
            return [], [ev]

    def _probe_docker_desktop_installed(
        self, host_os: str, diagnostics: list[str]
    ) -> CapabilityStatus:
        """Probe whether Docker Desktop is installed on the host platform.

        Evaluates multiple independent installation signals:
        1. All-users machine installation in %PROGRAMFILES% and %PROGRAMFILES(X86)%.
        2. Per-user installation in %LOCALAPPDATA%\\Programs\\DockerDesktop.
        3. Executable discoverability via PATH.

        Note: Installation presence does NOT imply runtime or daemon availability.
        """
        if host_os == "windows":
            prog_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
            prog_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
            local_appdata = os.environ.get("LOCALAPPDATA", "")

            candidates: list[Path] = []
            if prog_files:
                candidates.extend(
                    [
                        Path(prog_files) / "Docker" / "Docker" / "Docker Desktop.exe",
                        Path(prog_files) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
                    ]
                )
            if prog_files_x86:
                candidates.extend(
                    [
                        Path(prog_files_x86) / "Docker" / "Docker" / "Docker Desktop.exe",
                    ]
                )
            if local_appdata:
                candidates.extend(
                    [
                        Path(local_appdata) / "Programs" / "DockerDesktop" / "Docker Desktop.exe",
                        Path(local_appdata)
                        / "Programs"
                        / "DockerDesktop"
                        / "resources"
                        / "bin"
                        / "docker.exe",
                        Path(local_appdata) / "Programs" / "Docker Desktop" / "Docker Desktop.exe",
                        Path(local_appdata)
                        / "Programs"
                        / "Docker Desktop"
                        / "resources"
                        / "bin"
                        / "docker.exe",
                    ]
                )

            for candidate in candidates:
                if candidate.exists():
                    return CapabilityStatus.SUPPORTED

            if shutil.which("Docker Desktop.exe") or shutil.which("Docker Desktop"):
                return CapabilityStatus.SUPPORTED

            diagnostics.append(
                "Docker Desktop installation not detected in standard all-users or per-user system locations."
            )
            return CapabilityStatus.UNAVAILABLE
        elif host_os == "darwin":
            if Path("/Applications/Docker.app").exists():
                return CapabilityStatus.SUPPORTED
            return CapabilityStatus.UNAVAILABLE
        else:
            if shutil.which("docker-desktop"):
                return CapabilityStatus.SUPPORTED
            return CapabilityStatus.NOT_APPLICABLE

    def _probe_docker_desktop_version(
        self, diagnostics: list[str]
    ) -> tuple[str | None, list[EvidenceProvenance]]:
        """Inspect Docker Desktop version metadata if available."""
        host_os = platform.system().lower()
        if host_os == "windows":
            prog_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
            prog_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            version_candidates = [
                Path(prog_files) / "Docker" / "Docker" / "version.json",
                Path(prog_files_x86) / "Docker" / "Docker" / "version.json",
                Path(local_appdata) / "Programs" / "DockerDesktop" / "version.json",
            ]
            for candidate in version_candidates:
                if candidate.exists():
                    try:
                        with open(candidate, encoding="utf-8") as f:
                            v_data = json.load(f)
                        if isinstance(v_data, dict) and "version" in v_data:
                            ver_str = str(v_data["version"])
                            return ver_str, [
                                EvidenceProvenance(
                                    source="docker_desktop_version_file",
                                    observed_value=ver_str,
                                    normalized_value=ver_str,
                                    classification=EvidenceClassification.INSTALLATION_EVIDENCE,
                                    confidence="HIGH",
                                    availability=CapabilityStatus.SUPPORTED,
                                )
                            ]
                    except Exception:
                        pass
        return None, [
            EvidenceProvenance(
                source="docker_desktop_version_file",
                observed_value=None,
                normalized_value="UNAVAILABLE",
                classification=EvidenceClassification.INSTALLATION_EVIDENCE,
                confidence="LOW",
                availability=CapabilityStatus.UNAVAILABLE,
            )
        ]

    def _probe_docker_settings(
        self, diagnostics: list[str]
    ) -> tuple[dict[str, Any] | None, list[EvidenceProvenance]]:
        """Inspect Docker Desktop configuration files for configured engine settings."""
        host_os = platform.system().lower()
        candidates: list[Path] = []
        if host_os == "windows":
            appdata = os.environ.get("APPDATA", "")
            programdata = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
            if appdata:
                candidates.append(Path(appdata) / "Docker" / "settings.json")
                candidates.append(Path(appdata) / "Docker" / "settings-store.json")
            if programdata:
                candidates.append(Path(programdata) / "DockerDesktop" / "admin-settings.json")
            candidates.append(Path.home() / ".docker" / "desktop" / "settings.json")
            candidates.append(Path.home() / ".docker" / "desktop" / "settings-store.json")
        else:
            candidates.append(Path.home() / ".docker" / "desktop" / "settings.json")
            candidates.append(Path.home() / ".docker" / "desktop" / "settings-store.json")

        for candidate in candidates:
            if candidate.exists():
                try:
                    with open(candidate, encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        # The official Docker Desktop configuration key is wslEngineEnabled
                        wsl_enabled = data.get("wslEngineEnabled")
                        if wsl_enabled is None and isinstance(data.get("LinuxVM"), dict):
                            wsl_enabled = data["LinuxVM"].get("wslEngineEnabled")

                        normalized = "UNKNOWN"
                        if wsl_enabled is True:
                            normalized = "WSL2"
                        elif wsl_enabled is False:
                            normalized = "NON_WSL"

                        ev = EvidenceProvenance(
                            source="docker_settings",
                            observed_value={
                                "file": str(candidate),
                                "wslEngineEnabled": wsl_enabled,
                            },
                            normalized_value=normalized,
                            classification=EvidenceClassification.CONFIGURATION_EVIDENCE,
                            confidence="HIGH" if normalized != "UNKNOWN" else "MEDIUM",
                            availability=CapabilityStatus.SUPPORTED,
                        )
                        return data, [ev]
                except Exception as e:
                    diagnostics.append(
                        f"Failed parsing Docker Desktop settings from {candidate}: {e}"
                    )

        ev = EvidenceProvenance(
            source="docker_settings",
            observed_value=None,
            normalized_value="UNAVAILABLE",
            classification=EvidenceClassification.CONFIGURATION_EVIDENCE,
            confidence="LOW",
            availability=CapabilityStatus.UNAVAILABLE,
        )
        return None, [ev]

    def _probe_desktop_cli_engine(self, diagnostics: list[str]) -> list[EvidenceProvenance]:
        """Probe Docker Desktop CLI engine status via 'docker desktop engine ls'."""
        try:
            res = subprocess.run(
                ["docker", "desktop", "engine", "ls"],
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
            )
            if res.returncode == 0 and res.stdout.strip():
                stdout = res.stdout.strip()
                active_engine = "UNKNOWN"
                for line in stdout.splitlines():
                    if "*" in line:
                        active_engine = line.replace("*", "").strip().lower()
                return [
                    EvidenceProvenance(
                        source="docker_desktop_cli_engine",
                        observed_value=stdout,
                        normalized_value=active_engine,
                        classification=EvidenceClassification.CONTAINER_ISOLATION_EVIDENCE,
                        confidence="HIGH",
                        availability=CapabilityStatus.SUPPORTED,
                    )
                ]
        except Exception:
            pass  # Command not supported or timed out
        return [
            EvidenceProvenance(
                source="docker_desktop_cli_engine",
                observed_value=None,
                normalized_value="UNAVAILABLE",
                classification=EvidenceClassification.CONTAINER_ISOLATION_EVIDENCE,
                confidence="LOW",
                availability=CapabilityStatus.UNAVAILABLE,
            )
        ]

    def _probe_docker_daemon(self, diagnostics: list[str]) -> dict[str, Any] | None:
        """Query Docker daemon via docker info JSON."""
        try:
            res = subprocess.run(
                ["docker", "info", "--format", "{{json .}}"],
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
            )
            if res.returncode != 0:
                diagnostics.append(
                    f"Docker daemon is not responding: {res.stderr.strip() or 'Exit code ' + str(res.returncode)}"
                )
                return None
            return json.loads(res.stdout)  # type: ignore[no-any-return]
        except subprocess.TimeoutExpired:
            diagnostics.append(f"Docker info timed out after {self.command_timeout}s.")
            return None
        except Exception as e:
            diagnostics.append(f"Docker daemon probe error: {e}")
            return None

    def _resolve_docker_backend(
        self,
        info: dict[str, Any],
        host_os: str,
        additional_evidence: list[EvidenceProvenance]
        | tuple[EvidenceProvenance, ...]
        | None = None,
    ) -> tuple[DockerBackendType, dict[str, Any]]:
        """Determine whether Docker runs via WSL2, Hyper-V, Docker VMM, native Linux, or UNKNOWN.

        Evaluates multi-source evidence using the pure BackendIdentityResolver.
        """
        evidence_list: list[EvidenceProvenance] = list(additional_evidence or [])

        kernel_ver = str(info.get("KernelVersion", "")).strip()
        op_sys = str(info.get("OperatingSystem", "")).strip()
        isolation = str(info.get("Isolation", "")).strip()
        sec_opts = [str(opt) for opt in (info.get("SecurityOptions") or [])]
        system_status = info.get("SystemStatus")
        server_ver = str(info.get("ServerVersion", "")).strip()

        evidence_list.extend(
            [
                EvidenceProvenance(
                    source="docker_info_kernel",
                    observed_value=kernel_ver,
                    normalized_value=kernel_ver,
                    classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
                    confidence="HIGH" if kernel_ver else "LOW",
                    availability=CapabilityStatus.SUPPORTED,
                ),
                EvidenceProvenance(
                    source="docker_info_os",
                    observed_value=op_sys,
                    normalized_value=op_sys,
                    classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
                    confidence="HIGH" if op_sys else "LOW",
                    availability=CapabilityStatus.SUPPORTED,
                ),
                EvidenceProvenance(
                    source="docker_info_isolation",
                    observed_value=isolation,
                    normalized_value=isolation,
                    classification=EvidenceClassification.CONTAINER_ISOLATION_EVIDENCE,
                    confidence="HIGH" if isolation else "LOW",
                    availability=CapabilityStatus.SUPPORTED,
                ),
                EvidenceProvenance(
                    source="docker_info_system_status",
                    observed_value=system_status,
                    normalized_value=str(system_status) if system_status else "",
                    classification=EvidenceClassification.DAEMON_RUNTIME_EVIDENCE,
                    confidence="MEDIUM" if system_status else "LOW",
                    availability=CapabilityStatus.SUPPORTED,
                ),
            ]
        )

        resolution = BackendIdentityResolver.resolve(
            evidence_list=evidence_list,
            host_os=host_os,
            daemon_available=True,
        )

        evidence_dict: dict[str, Any] = {
            "OperatingSystem": op_sys,
            "KernelVersion": kernel_ver,
            "OSType": str(info.get("OSType", "")).strip(),
            "Isolation": isolation,
            "SecurityOptions": sec_opts,
            "SystemStatus": system_status,
            "ServerVersion": server_ver,
            "active_backend": resolution.active_backend.value,
            "configured_backend": resolution.configured_backend.value,
            "confidence": resolution.confidence,
            "contradictions": list(resolution.contradictions),
            "evidence_records": [e.model_dump(mode="json") for e in evidence_list],
        }
        return resolution.active_backend, evidence_dict


def evaluate_profile_satisfaction(
    profile: SandboxProfile,
    capabilities: SystemCapabilities,
) -> ProfileSatisfactionResult:
    """Evaluate whether a requested SandboxProfile is satisfiable by system capabilities.

    If any mandatory security requirement cannot be guaranteed by the active backend,
    returns unsatisfied with explicit reasons to trigger the fail-closed invariant.
    """
    unsatisfied: list[str] = []
    diagnostics: list[str] = list(capabilities.diagnostics)

    # 1. Isolation Tier Check
    if profile.isolation_tier not in capabilities.supported_tiers:
        unsatisfied.append(
            f"Isolation tier '{profile.isolation_tier.value}' is not supported by current host environment."
        )

    # 2. Container Engine Checks for Tier 1
    if profile.isolation_tier == IsolationTier.TIER_1_CONTAINER:
        if capabilities.docker_daemon_available != CapabilityStatus.SUPPORTED:
            unsatisfied.append(
                "Docker daemon is not operational; Tier-1 container isolation cannot be provided."
            )

        if capabilities.docker_backend == DockerBackendType.UNKNOWN:
            unsatisfied.append(
                "Docker backend identity is UNKNOWN; cannot verify containment boundary properties. Failing closed."
            )

        if (
            capabilities.docker_backend == DockerBackendType.UNAVAILABLE
            and "Docker daemon is not operational" not in " ".join(unsatisfied)
        ):
            unsatisfied.append(
                "Docker backend is unavailable; Tier-1 container isolation cannot be provided."
            )

        if (
            profile.read_only_rootfs
            and capabilities.read_only_rootfs_supported != CapabilityStatus.SUPPORTED
        ):
            unsatisfied.append(
                "Read-only root filesystem is not supported by the container backend."
            )

        if (
            profile.network_profile == NetworkProfile.NONE
            and capabilities.network_none_supported != CapabilityStatus.SUPPORTED
        ):
            unsatisfied.append("Network isolation (network: none) is not supported by backend.")

        if (
            profile.no_new_privileges
            and capabilities.no_new_privileges_supported != CapabilityStatus.SUPPORTED
        ):
            unsatisfied.append("no-new-privileges security option is not supported by backend.")

        if (
            profile.resource_limits.pids_limit > 0
            and capabilities.pids_limit_supported != CapabilityStatus.SUPPORTED
        ):
            unsatisfied.append(
                f"PIDs quota enforcement is {capabilities.pids_limit_supported.value}; cannot guarantee PID exhaustion defense."
            )

        if (
            profile.resource_limits.memory_limit_mb > 0
            and capabilities.memory_limit_supported != CapabilityStatus.SUPPORTED
        ):
            unsatisfied.append(
                f"Memory quota enforcement is {capabilities.memory_limit_supported.value}; cannot guarantee memory isolation."
            )

        if (
            profile.resource_limits.cpu_limit > 0
            and capabilities.cpu_limit_supported != CapabilityStatus.SUPPORTED
        ):
            unsatisfied.append(
                f"CPU quota enforcement is {capabilities.cpu_limit_supported.value}; cannot guarantee CPU containment."
            )

        if (
            profile.seccomp_profile != "unconfined"
            and capabilities.seccomp_supported != CapabilityStatus.SUPPORTED
        ):
            unsatisfied.append(
                f"Seccomp syscall filtering is {capabilities.seccomp_supported.value}; cannot guarantee syscall containment."
            )

        if (
            len(profile.drop_capabilities) > 0
            and capabilities.capability_drop_supported != CapabilityStatus.SUPPORTED
        ):
            unsatisfied.append(
                f"Linux capability dropping is {capabilities.capability_drop_supported.value}; cannot guarantee dropped capabilities."
            )

        if (
            profile.network_profile == NetworkProfile.ALLOWLIST
            and capabilities.network_allowlist_supported != CapabilityStatus.SUPPORTED
        ):
            unsatisfied.append(
                f"Network egress allowlisting is {capabilities.network_allowlist_supported.value}."
            )

    # 3. Application-Kernel Checks for Tier 2
    if (
        profile.isolation_tier == IsolationTier.TIER_2_APPLICATION_SANDBOX
        and capabilities.gvisor_available != CapabilityStatus.SUPPORTED
    ):
        unsatisfied.append("gVisor (runsc) runtime is not installed or available on this host.")

    # 4. MicroVM Checks for Tier 3
    if (
        profile.isolation_tier == IsolationTier.TIER_3_MICROVM
        and capabilities.kata_available != CapabilityStatus.SUPPORTED
    ):
        unsatisfied.append(
            "Kata Containers (kata-runtime) is not installed or available on this host."
        )

    return ProfileSatisfactionResult(
        is_satisfied=len(unsatisfied) == 0,
        isolation_tier=profile.isolation_tier,
        unsatisfied_reasons=tuple(unsatisfied),
        diagnostics=tuple(diagnostics),
    )


_GLOBAL_DETECTOR: CapabilityDetector | None = None
_CACHED_CAPS: SystemCapabilities | None = None


def get_system_capabilities(force_refresh: bool = False) -> SystemCapabilities:
    """Retrieve cached system capability report or detect fresh."""
    global _GLOBAL_DETECTOR, _CACHED_CAPS
    if _GLOBAL_DETECTOR is None:
        _GLOBAL_DETECTOR = CapabilityDetector()
    if _CACHED_CAPS is None or force_refresh:
        _CACHED_CAPS = _GLOBAL_DETECTOR.detect()
    return _CACHED_CAPS
