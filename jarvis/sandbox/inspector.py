"""JARVIS Sandbox Security Inspector and Backend Capability Reporter.

Provides deep diagnostic inspection of sandbox configurations, effective security controls,
backend virtualization evidence, and machine-readable capability reports.
Never discloses sensitive tokens, keys, or credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from jarvis.sandbox.detector import get_system_capabilities
from jarvis.sandbox.models import (
    CapabilityStatus,
    IsolationTier,
    NetworkProfile,
    ResourceLimits,
    SandboxProfile,
)

if TYPE_CHECKING:
    from jarvis.sandbox.manager import SandboxLifecycleManager


def get_backend_capability_report() -> dict[str, Any]:
    """Generate an authoritative, structured, machine-readable backend capability report."""
    caps = get_system_capabilities()
    now_iso = datetime.now(UTC).isoformat()

    def _entry(
        status: CapabilityStatus,
        evidence: Any,
        source: str,
        limitations: str = "",
    ) -> dict[str, Any]:
        return {
            "status": status.value,
            "evidence": str(evidence) if evidence is not None else "none",
            "source": source,
            "timestamp": now_iso,
            "limitations": limitations,
        }

    daemon_active = caps.docker_daemon_available == CapabilityStatus.SUPPORTED

    return {
        "host_os": caps.host_os,
        "host_arch": caps.host_arch,
        "wsl_platform": _entry(
            caps.wsl_platform_available,
            "wsl.exe detected in Windows System32"
            if caps.wsl_platform_available == CapabilityStatus.SUPPORTED
            else "wsl.exe not found",
            "system_binary_probe",
            limitations="Platform utility presence does not imply installed Linux distributions.",
        ),
        "wsl_distros": _entry(
            caps.user_wsl_distros_present,
            list(caps.wsl_distributions) if caps.wsl_distributions else "0 user distros",
            "wsl_list_probe",
            limitations="Docker Desktop manages its own distributions independently.",
        ),
        "docker_desktop_installed": _entry(
            caps.docker_desktop_installed,
            caps.docker_desktop_version or "not detected",
            "filesystem_package_discovery",
            limitations="Installation on disk does not establish that the daemon service is running.",
        ),
        "docker_cli": _entry(
            caps.docker_cli_available,
            "docker executable in PATH"
            if caps.docker_cli_available == CapabilityStatus.SUPPORTED
            else "not found in PATH",
            "path_lookup",
            limitations="CLI availability does not guarantee daemon reachability.",
        ),
        "docker_daemon": _entry(
            caps.docker_daemon_available,
            "responding" if daemon_active else "inactive/unreachable",
            "docker_version_probe",
            limitations="When inactive, all container isolation fails closed to prevent unisolated execution.",
        ),
        "docker_server_version": _entry(
            CapabilityStatus.SUPPORTED
            if caps.docker_server_version
            else CapabilityStatus.UNAVAILABLE,
            caps.docker_server_version,
            "daemon_info",
        ),
        "docker_backend": _entry(
            CapabilityStatus.SUPPORTED if daemon_active else CapabilityStatus.UNAVAILABLE,
            caps.docker_backend.value,
            "backend_identity_resolver",
            limitations=f"Confidence: {caps.backend_confidence}; Contradictions: {len(caps.backend_contradictions)}",
        ),
        "docker_backend_confidence": _entry(
            CapabilityStatus.SUPPORTED,
            caps.backend_confidence,
            "evidence_provenance_matrix",
        ),
        "rootless": _entry(
            caps.rootless_supported,
            caps.rootless_supported.value,
            "security_options_probe",
        ),
        "cgroup_v2": _entry(
            caps.cgroup_v2_supported,
            caps.cgroup_v2_supported.value,
            "cgroup_controller_probe",
            limitations="Cgroups enforce CPU, memory, and PID limits in Linux kernel.",
        ),
        "cpu_limits": _entry(
            caps.cpu_limit_supported,
            caps.cpu_limit_supported.value,
            "cgroup_cpu_controller",
        ),
        "memory_limits": _entry(
            caps.memory_limit_supported,
            caps.memory_limit_supported.value,
            "cgroup_memory_controller",
        ),
        "pids_limits": _entry(
            caps.pids_limit_supported,
            caps.pids_limit_supported.value,
            "cgroup_pids_controller",
        ),
        "seccomp": _entry(
            caps.seccomp_supported,
            caps.seccomp_supported.value,
            "security_options_seccomp",
            limitations="Filters dangerous syscalls (clone, ptrace, bpf, mount).",
        ),
        "gvisor": _entry(
            caps.gvisor_available,
            caps.gvisor_available.value,
            "runtime_detection_runsc",
            limitations="gVisor (runsc) provides user-space Sentry application-kernel isolation.",
        ),
        "kata": _entry(
            caps.kata_available,
            caps.kata_available.value,
            "runtime_detection_kata",
            limitations="Kata Containers provides hardware-virtualized microVM guest-kernel isolation.",
        ),
        "oci_runtime": _entry(
            CapabilityStatus.SUPPORTED if caps.oci_runtime else CapabilityStatus.UNAVAILABLE,
            caps.oci_runtime,
            "docker_info_runtime",
        ),
        "supported_tiers": [tier.value for tier in caps.supported_tiers],
        "real_isolation_verified": False,
        "diagnostics": list(caps.diagnostics),
        "timestamp": now_iso,
    }


def explain_sandbox_configuration(
    sandbox_id: UUID | None = None,
    profile: SandboxProfile | None = None,
    task_id: UUID | None = None,
    agent_id: str = "coding",
    lifecycle_manager: SandboxLifecycleManager | None = None,
) -> dict[str, Any]:
    """Inspect and explain the effective sandbox configuration, security boundaries, and authorization state.

    Never discloses credentials, secret tokens, or sensitive environment values.
    """
    caps = get_system_capabilities()
    active_profile = profile or SandboxProfile(
        profile_id="default_coding_profile",
        isolation_tier=IsolationTier.TIER_1_CONTAINER,
        base_image="python:3.11-slim",
        run_as_user="1000:1000",
        read_only_rootfs=True,
        no_new_privileges=True,
        drop_capabilities=("ALL",),
        network_profile=NetworkProfile.NONE,
        resource_limits=ResourceLimits(
            cpu_limit=1.0,
            memory_limit_mb=512,
            pids_limit=100,
            timeout_seconds=30.0,
        ),
    )

    is_daemon_active = caps.docker_daemon_available == CapabilityStatus.SUPPORTED

    managed_record = None
    if sandbox_id is not None and lifecycle_manager is not None:
        managed_record = lifecycle_manager.get_sandbox_record(sandbox_id)

    res_limits = active_profile.resource_limits

    return {
        "task_id": str(task_id or uuid4()),
        "agent_id": agent_id,
        "sandbox_id": str(sandbox_id) if sandbox_id else "ephemeral_on_dispatch",
        "requested_tier": active_profile.isolation_tier.value,
        "requested_profile": {
            "profile_id": active_profile.profile_id,
            "base_image": active_profile.base_image,
            "run_as_user": active_profile.run_as_user,
            "read_only_rootfs": active_profile.read_only_rootfs,
            "no_new_privileges": active_profile.no_new_privileges,
            "drop_capabilities": list(active_profile.drop_capabilities),
            "add_capabilities": list(active_profile.add_capabilities),
            "seccomp_profile": active_profile.seccomp_profile,
            "network_profile": active_profile.network_profile.value,
            "network_allowlist": list(active_profile.network_allowlist),
            "resource_limits": {
                "cpu_limit_cores": res_limits.cpu_limit,
                "memory_limit_mb": res_limits.memory_limit_mb,
                "pids_limit": res_limits.pids_limit,
                "disk_limit_mb": res_limits.disk_limit_mb,
                "timeout_seconds": res_limits.timeout_seconds,
                "max_output_bytes": res_limits.max_output_bytes,
            },
        },
        "selected_provider": "DockerSandboxProvider" if is_daemon_active else "None (Fail-Closed)",
        "backend": {
            "backend_type": "docker",
            "docker_backend": caps.docker_backend.value,
            "confidence": caps.backend_confidence,
            "contradictions": list(caps.backend_contradictions),
            "server_version": caps.docker_server_version or "unavailable",
            "operational": is_daemon_active,
        },
        "effective_security_controls": {
            "filesystem_isolation": "Read-only rootfs + scoped /workspace temporary directory",
            "workspace_mounts": [
                "<staging_dir>:/workspace:rw",
                "tmpfs:/tmp:rw,noexec,nosuid,size=64m",
            ],
            "network_policy": active_profile.network_profile.value.upper(),
            "credential_policy": "STRICT_NONE (zero host credentials injected into sandbox)",
            "docker_socket_boundary": "UNMOUNTED / INACCESSIBLE (hard invariant)",
            "user_and_privileges": f"Non-root ({active_profile.run_as_user}), no-new-privileges=true",
            "linux_capabilities": f"Dropped: {list(active_profile.drop_capabilities)}",
            "seccomp_status": active_profile.seccomp_profile,
            "resource_confinement": {
                "cgroup_v2": caps.cgroup_v2_supported.value,
                "cpu": f"{res_limits.cpu_limit} core(s)",
                "memory": f"{res_limits.memory_limit_mb} MB",
                "pids": f"{res_limits.pids_limit} PIDs",
                "timeout": f"{res_limits.timeout_seconds}s wall-clock",
            },
        },
        "authorization_and_governance": {
            "governing_authority": "JARVIS Policy Engine & Capability Firewall",
            "effect_broker": "Action Broker (Idempotency Ledger & Leases)",
            "fail_closed_invariant": "ACTIVE (unisolated host execution fallback strictly prohibited)",
        },
        "lifecycle_state": managed_record.state.value if managed_record else "NOT_DISPATCHED",
        "attestation_status": "NOT_VERIFIED (Host container daemon is inactive)"
        if not is_daemon_active
        else "READY_FOR_STATIC_AND_BEHAVIORAL_PROBE",
        "real_isolation_verified": False,
    }


__all__ = [
    "explain_sandbox_configuration",
    "get_backend_capability_report",
]
