"""JARVIS Sandbox Execution Fabric Typed Contracts and Models.

Defines typed schemas for sandbox identities, execution profiles,
isolation tiers, capability statuses, resource constraints, network policies,
and execution results.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IsolationTier(StrEnum):
    """Execution containment tiers supported by the architecture."""

    TIER_0_LOCAL = "TIER_0_LOCAL"  # Host process execution (explicitly NOT isolated, requires HITL)
    TIER_1_CONTAINER = "TIER_1_CONTAINER"  # Ephemeral OCI/Docker container with kernel namespaces (supports isolated execution; authorization determined by Policy Engine)
    TIER_2_APPLICATION_SANDBOX = "TIER_2_APPLICATION_SANDBOX"  # gVisor/runsc application-kernel isolation (supports isolated execution; authorization determined by Policy Engine)
    TIER_3_MICROVM = "TIER_3_MICROVM"  # Kata / microVM-class guest-kernel isolation (supports isolated execution; authorization determined by Policy Engine)


class ProviderCategory(StrEnum):
    """Categorizes whether a sandbox provider implements real host isolation or test doubles."""

    REAL_ISOLATION_PROVIDER = "REAL_ISOLATION_PROVIDER"
    TEST_DOUBLE = "TEST_DOUBLE"


class BackendType(StrEnum):
    """Underlying container or virtualization engine."""

    DOCKER = "docker"
    WSL2 = "wsl2"
    GVISOR = "gvisor"
    KATA = "kata"
    LOCAL_PROCESS = "local_process"
    TEST_DOUBLE = "test_double"


class DockerBackendType(StrEnum):
    """Specific isolation backend used by Docker engine."""

    WSL2 = "WSL2"
    HYPER_V = "HYPER_V"
    DOCKER_VMM = "DOCKER_VMM"
    NATIVE_LINUX = "NATIVE_LINUX"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


class CapabilityStatus(StrEnum):
    """Explicit status of a security or virtualization capability."""

    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    FAILED = "FAILED"


class NetworkProfile(StrEnum):
    """Network egress isolation profiles."""

    NONE = "none"  # Complete network isolation (loopback only)
    ALLOWLIST = "allowlist"  # Egress restricted strictly to approved FQDN/IP list
    FULL = "full"  # Unrestricted egress (requires explicit elevated policy)


class SandboxState(StrEnum):
    """Lifecycle states of an execution sandbox."""

    CREATED = "CREATED"
    STARTING = "STARTING"
    READY = "READY"
    EXECUTING = "EXECUTING"
    QUIESCING = "QUIESCING"
    TERMINATING = "TERMINATING"
    TERMINATED = "TERMINATED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    KILLED = "KILLED"
    EXPIRED = "EXPIRED"
    RECOVERY_PENDING = "RECOVERY_PENDING"  # Active cleanup obligation or uncertain recovery state
    UNKNOWN = "UNKNOWN"  # Ambiguous or unverifiable backend state


class ArtifactDirection(StrEnum):
    """Direction of explicit file transfer staging."""

    UPLOAD = "UPLOAD"  # Host -> Sandbox
    DOWNLOAD = "DOWNLOAD"  # Sandbox -> Host


class ResourceLimits(BaseModel):
    """Enforceable cgroup/process constraints."""

    model_config = ConfigDict(frozen=True)

    cpu_limit: float = Field(default=1.0, ge=0.1, le=16.0, description="CPU quota in cores")
    memory_limit_mb: int = Field(default=512, ge=64, le=16384, description="RAM limit in Megabytes")
    pids_limit: int = Field(default=100, ge=10, le=2000, description="Maximum simultaneous PIDs")
    disk_limit_mb: int = Field(
        default=256, ge=16, le=10240, description="Writable layer quota in Megabytes"
    )
    timeout_seconds: float = Field(
        default=30.0, ge=0.01, le=600.0, description="Wall-clock timeout in seconds"
    )
    max_output_bytes: int = Field(
        default=50_000, ge=1000, le=5_000_000, description="Max stdout/stderr byte buffer"
    )


class SandboxProfile(BaseModel):
    """Declarative specification for container isolation and security controls."""

    model_config = ConfigDict(frozen=True)

    profile_id: str
    isolation_tier: IsolationTier = IsolationTier.TIER_1_CONTAINER
    base_image: str = "python:3.11-slim"
    run_as_user: str = "1000:1000"
    read_only_rootfs: bool = True
    no_new_privileges: bool = True
    drop_capabilities: tuple[str, ...] = ("ALL",)
    add_capabilities: tuple[str, ...] = ()
    seccomp_profile: str = "default"
    network_profile: NetworkProfile = NetworkProfile.NONE
    network_allowlist: tuple[str, ...] = ()
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    allow_docker_socket: bool = False
    allow_credential_injection: bool = False

    @field_validator("allow_docker_socket")
    @classmethod
    def enforce_no_docker_socket(cls, val: bool) -> bool:
        if val:
            raise ValueError(
                "Security violation: Docker socket exposure is strictly prohibited in SandboxProfile."
            )
        return False

    @field_validator("allow_credential_injection")
    @classmethod
    def enforce_no_credentials(cls, val: bool) -> bool:
        if val:
            raise ValueError(
                "Security violation: Credential injection into sandbox is strictly prohibited."
            )
        return False


class SandboxIdentity(BaseModel):
    """Structured identity record for an instantiated sandbox."""

    model_config = ConfigDict(frozen=True)

    sandbox_id: UUID = Field(default_factory=uuid4)
    task_id: UUID = Field(default_factory=uuid4)
    session_id: UUID | None = None
    agent_id: str = "coding"
    owner: str = "core"
    profile_id: str
    backend_type: BackendType
    isolation_tier: IsolationTier
    provider_category: ProviderCategory
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime


class ArtifactTransferRecord(BaseModel):
    """Audit record for explicit host-sandbox file transfers."""

    model_config = ConfigDict(frozen=True)

    transfer_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    sandbox_id: UUID
    direction: ArtifactDirection
    host_path: str
    sandbox_path: str
    file_size_bytes: int = Field(ge=0)
    sha256_digest: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SandboxExecutionResult(BaseModel):
    """Structured result returned from a sandboxed command or script execution."""

    model_config = ConfigDict(frozen=True)

    sandbox_id: UUID
    task_id: UUID
    execution_id: UUID = Field(default_factory=uuid4)
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float = Field(ge=0.0)
    timed_out: bool = False
    state: SandboxState
    backend_type: BackendType
    isolation_tier: IsolationTier
    provider_category: ProviderCategory
    network_profile: NetworkProfile
    started_at: datetime
    finished_at: datetime
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    termination_reason: str | None = None


class EvidenceClassification(StrEnum):
    """Semantic classification of environment evidence for backend identity resolution."""

    BACKEND_IDENTITY_EVIDENCE = "BACKEND_IDENTITY_EVIDENCE"
    CONTAINER_ISOLATION_EVIDENCE = "CONTAINER_ISOLATION_EVIDENCE"
    DAEMON_RUNTIME_EVIDENCE = "DAEMON_RUNTIME_EVIDENCE"
    CONFIGURATION_EVIDENCE = "CONFIGURATION_EVIDENCE"
    INSTALLATION_EVIDENCE = "INSTALLATION_EVIDENCE"
    CORROBORATING_EVIDENCE = "CORROBORATING_EVIDENCE"
    NON_AUTHORITATIVE = "NON_AUTHORITATIVE"


class EvidenceProvenance(BaseModel):
    """Typed evidence record with explicit provenance for backend resolution."""

    model_config = ConfigDict(frozen=True)

    source: str
    observed_value: Any
    normalized_value: str
    classification: EvidenceClassification = EvidenceClassification.NON_AUTHORITATIVE
    confidence: str = "HIGH"  # "HIGH", "MEDIUM", "LOW"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    availability: CapabilityStatus = CapabilityStatus.SUPPORTED


class BackendResolutionResult(BaseModel):
    """Result of pure backend resolution evaluating multi-source evidence."""

    model_config = ConfigDict(frozen=True)

    active_backend: DockerBackendType
    configured_backend: DockerBackendType = DockerBackendType.UNAVAILABLE
    confidence: str = "NONE"  # "HIGH", "MEDIUM", "LOW", "NONE"
    contradictions: tuple[str, ...] = ()
    evidence: tuple[EvidenceProvenance, ...] = ()


class SystemCapabilities(BaseModel):
    """Discovered host environment virtualization and container capabilities.

    Exposes explicit capability states rather than lossy boolean flags.
    """

    model_config = ConfigDict(frozen=True)

    host_os: str
    host_arch: str

    # WSL Platform & User Distro Separation
    wsl_platform_available: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    user_wsl_distros_present: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    wsl_distributions: tuple[str, ...] = ()
    wsl_available: CapabilityStatus = (
        CapabilityStatus.UNAVAILABLE
    )  # Legacy/compat alias for wsl_platform_available

    # Docker Platform & Installation Separation
    docker_desktop_installed: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    docker_desktop_version: str | None = None
    docker_cli_available: CapabilityStatus
    docker_daemon_available: CapabilityStatus
    docker_version: str | None = None
    docker_server_version: str | None = None
    docker_backend: DockerBackendType = DockerBackendType.UNAVAILABLE
    configured_backend: DockerBackendType = DockerBackendType.UNAVAILABLE
    backend_confidence: str = "NONE"
    backend_contradictions: tuple[str, ...] = ()
    oci_runtime: str | None = None
    backend_detection_evidence: dict[str, Any] = Field(default_factory=dict)

    rootless_supported: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    user_namespace_supported: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    seccomp_supported: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    cgroup_v2_supported: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    cpu_limit_supported: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    memory_limit_supported: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    pids_limit_supported: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    read_only_rootfs_supported: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    network_none_supported: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    network_allowlist_supported: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    no_new_privileges_supported: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    capability_drop_supported: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    mount_isolation_supported: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    artifact_staging_supported: CapabilityStatus = CapabilityStatus.SUPPORTED

    gvisor_available: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    kata_available: CapabilityStatus = CapabilityStatus.UNAVAILABLE

    supported_tiers: tuple[IsolationTier, ...] = (IsolationTier.TIER_0_LOCAL,)
    diagnostics: tuple[str, ...] = ()


class ProfileSatisfactionResult(BaseModel):
    """Result of evaluating whether a requested SandboxProfile is satisfiable by system capabilities."""

    model_config = ConfigDict(frozen=True)

    is_satisfied: bool
    isolation_tier: IsolationTier
    unsatisfied_reasons: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


class LifecycleTransitionEvent(BaseModel):
    """Audit record for state transitions in the sandbox lifecycle."""

    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    sandbox_id: UUID
    task_id: UUID
    request_id: UUID | None = None
    previous_state: SandboxState
    new_state: SandboxState
    reason: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProviderHealthReport(BaseModel):
    """Detailed health and readiness report of a sandbox provider backend."""

    model_config = ConfigDict(frozen=True)

    is_available: bool
    is_healthy: bool
    docker_cli_available: bool = False
    backend_type: BackendType
    docker_backend: DockerBackendType = DockerBackendType.UNAVAILABLE
    backend_version: str | None = None
    server_version: str | None = None
    runtime: str | None = None
    supported_tiers: tuple[IsolationTier, ...] = ()
    supported_features: dict[str, CapabilityStatus] = Field(default_factory=dict)
    capacity_readiness: str = "READY"
    smoke_test_passed: bool | None = None
    diagnostics: tuple[str, ...] = ()
    evidence: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AttestationStatus(StrEnum):
    """Status of empirical profile security property attestation."""

    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    FAILED = "FAILED"
    NOT_VERIFIED = "NOT_VERIFIED"


class SentinelProbeId(StrEnum):
    """Canonical identifier for individual container security boundary probes."""

    HOST_FILESYSTEM_BOUNDARY = "HOST_FILESYSTEM_BOUNDARY"
    WORKSPACE_BOUNDARY = "WORKSPACE_BOUNDARY"
    DOCKER_SOCKET_BOUNDARY = "DOCKER_SOCKET_BOUNDARY"
    CREDENTIAL_BOUNDARY = "CREDENTIAL_BOUNDARY"
    PROCESS_NAMESPACE_BOUNDARY = "PROCESS_NAMESPACE_BOUNDARY"
    CHILD_PROCESS_CONTAINMENT = "CHILD_PROCESS_CONTAINMENT"
    NETWORK_EGRESS_POLICY = "NETWORK_EGRESS_POLICY"
    PRIVILEGE_BOUNDARY = "PRIVILEGE_BOUNDARY"
    CAPABILITY_RESTRICTION = "CAPABILITY_RESTRICTION"
    READ_ONLY_ROOTFS = "READ_ONLY_ROOTFS"
    RESOURCE_LIMITS = "RESOURCE_LIMITS"
    LIFECYCLE_CLEANUP = "LIFECYCLE_CLEANUP"


class EffectiveSecurityConfiguration(BaseModel):
    """Diagnostic evidence capturing effective container configuration before execution."""

    model_config = ConfigDict(frozen=True)

    image: str = ""
    runtime: str = ""
    network_mode: str = ""
    mounts: tuple[dict[str, Any], ...] = ()
    readonly_rootfs: bool = False
    drop_capabilities: tuple[str, ...] = ()
    add_capabilities: tuple[str, ...] = ()
    security_options: tuple[str, ...] = ()
    user: str = ""
    pids_limit: int | None = None
    memory_limit_bytes: int | None = None
    nano_cpus: int | None = None
    env_keys: tuple[str, ...] = ()  # Keys only, never secret values
    workspace_mapping: str = ""


class SentinelProbeVerdict(StrEnum):
    """Execution verdict for an isolation sentinel probe."""

    PASSED = "PASSED"
    BLOCKED = "BLOCKED"
    INACCESSIBLE = "INACCESSIBLE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    UNAVAILABLE = "UNAVAILABLE"


class SentinelProbeResult(BaseModel):
    """Outcome of an individual security sentinel probe."""

    model_config = ConfigDict(frozen=True)

    probe_id: str
    name: str
    description: str
    expected_verdict: SentinelProbeVerdict
    actual_verdict: SentinelProbeVerdict
    status: CapabilityStatus = CapabilityStatus.SUPPORTED
    passed: bool
    details: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class SandboxAttestation(BaseModel):
    """Evidence-based attestation of an active container sandbox's isolation properties."""

    model_config = ConfigDict(frozen=True)

    attestation_id: UUID = Field(default_factory=uuid4)
    sandbox_id: UUID
    profile_id: str
    backend_type: BackendType
    docker_backend: DockerBackendType = DockerBackendType.UNAVAILABLE
    backend_version: str = ""
    isolation_tier: IsolationTier
    provider_category: ProviderCategory

    # 12 Individual Security Property Statuses
    host_filesystem_boundary: CapabilityStatus = CapabilityStatus.UNKNOWN
    workspace_boundary: CapabilityStatus = CapabilityStatus.UNKNOWN
    docker_socket_boundary: CapabilityStatus = CapabilityStatus.UNKNOWN
    credential_boundary: CapabilityStatus = CapabilityStatus.UNKNOWN
    process_namespace_boundary: CapabilityStatus = CapabilityStatus.UNKNOWN
    child_process_containment: CapabilityStatus = CapabilityStatus.UNKNOWN
    network_egress_policy: CapabilityStatus = CapabilityStatus.UNKNOWN
    privilege_boundary: CapabilityStatus = CapabilityStatus.UNKNOWN
    capability_restriction: CapabilityStatus = CapabilityStatus.UNKNOWN
    read_only_rootfs: CapabilityStatus = CapabilityStatus.UNKNOWN
    resource_limits: CapabilityStatus = CapabilityStatus.UNKNOWN
    lifecycle_cleanup: CapabilityStatus = CapabilityStatus.UNKNOWN

    # Backward-compatibility / alias fields for existing consumers
    filesystem_isolation: CapabilityStatus = CapabilityStatus.UNKNOWN
    network_isolation: CapabilityStatus = CapabilityStatus.UNKNOWN
    privilege_isolation: CapabilityStatus = CapabilityStatus.UNKNOWN
    process_isolation: CapabilityStatus = CapabilityStatus.UNKNOWN
    resource_controls: CapabilityStatus = CapabilityStatus.UNKNOWN
    credential_exposure: CapabilityStatus = CapabilityStatus.UNKNOWN
    docker_socket_access: CapabilityStatus = CapabilityStatus.UNKNOWN

    status: AttestationStatus = AttestationStatus.NOT_VERIFIED
    overall_state: AttestationStatus = AttestationStatus.NOT_VERIFIED

    probe_results: dict[str, SentinelProbeResult] = Field(default_factory=dict)
    effective_configuration: EffectiveSecurityConfiguration | None = None
    backend_evidence: dict[str, Any] = Field(default_factory=dict)
    failures: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    unsatisfied_constraints: tuple[str, ...] = ()

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SentinelSuiteReport(BaseModel):
    """Comprehensive test report of all 12 isolation sentinel probes."""

    model_config = ConfigDict(frozen=True)

    suite_id: UUID = Field(default_factory=uuid4)
    sandbox_id: UUID | None = None
    backend_type: BackendType
    docker_backend: DockerBackendType = DockerBackendType.UNAVAILABLE
    provider_category: ProviderCategory
    isolation_tier: IsolationTier
    probes: dict[str, SentinelProbeResult] = Field(default_factory=dict)
    effective_configuration: EffectiveSecurityConfiguration | None = None
    all_probes_passed: bool = False
    real_isolation_verified: bool = False
    diagnostics: tuple[str, ...] = ()
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
