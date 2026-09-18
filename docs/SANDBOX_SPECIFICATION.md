# JARVIS v1.0.0 — Execution Fabric & Sandbox Security Specification
## Canonical Specification for Sandboxed Execution and Deep Agent Containment
*Classification: Engineering Design Document & Security Contract*  
*Version: 1.0.0*  
*Status: Approved Canonical Specification (Milestone 7 Baseline)*  

---

## 1. Executive Summary & Foundational Trust Boundaries

In the JARVIS zero-trust architecture, the model and agent are untrusted reasoning components. Code produced by an LLM or an autonomous agent is untrusted material with arbitrary intent. Running untrusted code on the host operating system exposes the system to data exfiltration, credential theft, process hijacking, and host compromise.

The sandbox is the **execution containment boundary**. It does not replace or weaken any existing control plane layers:

```text
               UNTRUSTED INTENT
            (Model / Deep Agent)
                     │
                     ▼
            CAPABILITY FIREWALL
        (Least-Privilege Tool Projection)
                     │
                     ▼
               POLICY ENGINE
         (Static & Dynamic Risk, HITL)
                     │
                     ▼
         COMMIT-TIME AUTHORIZATION
      (Target Witness & ETag Binding)
                     │
                     ▼
               ACTION BROKER
       (Idempotency, Leases, Deduplication)
                     │
                     ▼
             SANDBOX PROVIDER
     (Real Isolation Driver / Fail-Closed)
                     │
                     ▼
         ISOLATION EXECUTION FABRIC
   (Namespaces, Seccomp, Capabilities, Cgroups)
                     │
                     ▼
          EXTERNAL STATE VERIFIER
        (Point-in-Time Effect Receipt)
```

### Core Invariants

1. **The model is not the trust boundary.** Model refusal or prompt constraints provide zero security guarantees.
2. **Deep Agents is not the trust boundary.** An agent harness coordinates execution; it does not authorize actions or contain syscalls.
3. **The sandbox is the execution containment boundary.** The sandbox enforces kernel, network, filesystem, and resource isolation against untrusted workloads.
4. **Policy Engine remains the authorization authority.** No sandbox operation executes without centralized policy evaluation.
5. **Capability Firewall remains the capability authority.** Agents only observe tools permitted for their current role and risk budget.
6. **Action Broker remains the effect boundary.** Replay safety, idempotency tracking, and deduplication govern all side-effecting operations.
7. **External-state verification remains the reality boundary.** Sandbox exit codes and outputs are untrusted data until independently verified.
8. **Fail-Closed Rule:** If requested isolation capabilities are unavailable on the host, execution **fails closed** with structured diagnostics. The system **never** silently falls back to an unisolated host subprocess.
9. **Test Double vs. Real Isolation:** A mock or simulated driver is a `TEST_DOUBLE` used exclusively for lifecycle, contract, and error-handling tests. Only a `REAL_ISOLATION_PROVIDER` can provide empirical evidence for security exit gates.

---

## 2. Threat Model & Adversarial Capabilities

The sandbox is designed to defend the host operating system, credentials, and network against a malicious or compromised workload executing inside the environment.

| Threat Category | Attack Vector | Security Boundary & Countermeasure |
| :--- | :--- | :--- |
| **Host Filesystem Tampering** | Directory traversal (`..`), symlink attacks, mounting root filesystem | Dedicated ephemeral workspace; read-only rootfs (`--read-only`); no host mounts; strict path canonicalization. |
| **Host Credential Exfiltration** | Searching host environments, `.ssh/`, `.aws/`, `.env`, Git credentials | Zero secret injection; clean stripped environment; host home directory unmounted. |
| **Host Process Manipulation** | Signals to host processes, PID enumeration, process injection | Isolated PID namespace; container processes cannot see or signal host PIDs. |
| **Privilege Escalation** | `setuid` binaries, kernel exploits, capability abuse | Non-root UID (`1000:1000`); drop all Linux capabilities (`--cap-drop ALL`); `no-new-privileges=true`. |
| **Syscall Abuse** | Kernel exploitation via dangerous or unneeded system calls | Strict Seccomp profile blocking unneeded syscalls (`clone`, `ptrace`, `bpf`, `mount`). |
| **Resource Exhaustion / DoS** | Fork bombs, memory leaks, runaway CPU loops, disk fill | Cgroup enforcement: `--pids-limit 100`, memory quotas, CPU quotas, max execution timeouts, output buffer truncations. |
| **Network Egress / Exfiltration** | Data exfiltration, reverse shells, port scanning, metadata queries | Default-deny egress: `--network none`; explicit allowlists for network-enabled profiles. |
| **Container Breakout / Engine Control**| Accessing `/var/run/docker.sock` to control host Docker daemon | Docker socket strictly unmounted and inaccessible inside the container. |

---

## 3. Sandbox Identity Specification

Every active or historical sandbox instance possesses an immutable structured identity:

```python
class SandboxIdentity(BaseModel):
    sandbox_id: UUID
    task_id: UUID
    session_id: UUID | None
    agent_id: str
    owner: str
    profile_id: str
    backend_type: str  # e.g., "docker", "wsl2", "test_double"
    backend_version: str
    isolation_tier: str  # "TIER_1_CONTAINER", "TIER_2_MICROVM", "TIER_0_LOCAL"
    provider_category: str  # "REAL_ISOLATION_PROVIDER", "TEST_DOUBLE"
    created_at: datetime
    expires_at: datetime
```

---

## 4. Sandbox Lifecycle State Machine

The sandbox follows an explicit, fully observable lifecycle state machine:

```text
       ┌──────────┐
       │ CREATED  │
       └────┬─────┘
            │ start()
            ▼
       ┌──────────┐
       │ STARTING │
       └────┬─────┘
            │ container ready
            ▼
       ┌──────────┐
       │  READY   │◄──────────────┐
       └────┬─────┘               │
            │ execute()           │ execution complete
            ▼                     │
       ┌───────────┐              │
       │ EXECUTING ├──────────────┘
       └────┬──────┘
            │ terminate() / error / timeout
            ▼
       ┌───────────┐
       │ QUIESCING │
       └────┬──────┘
            │ cleanup()
            ▼
      ┌─────────────┐
      │ TERMINATING │
      └─────┬───────┘
            │ verified absence
     ┌──────┴──────┐
     ▼             ▼
┌────────────┐ ┌──────────────────┐
│ TERMINATED │ │ RECOVERY_PENDING │
└────────────┘ └──────────────────┘
```

### Lifecycle States

* **`CREATED`**: Sandbox profile configured and metadata recorded; container not yet initialized.
* **`STARTING`**: Container daemon starting the isolated workload.
* **`READY`**: Container initialized, rootfs secured, ready to accept execution requests.
* **`EXECUTING`**: Actively executing an isolated command.
* **`QUIESCING`**: Preparing for shutdown; draining outstanding I/O and sending termination signals.
* **`TERMINATING`**: Container stop and cleanup commands dispatched to container engine.
* **`TERMINATED`**: Verified terminal state; container absence confirmed via backend inspection.
* **`RECOVERY_PENDING`**: Non-terminal holding state; container cleanup could not be verified or provider was offline during teardown/crash recovery. Cleanup obligations remain tracked until verified.
* **`UNKNOWN`**: Ambiguous state; backend provider cannot determine container status.

### Failure & Interruption States (Transitions into Quiescing / Terminating)

* **`FAILED`**: Sandbox creation or startup encountered a fatal infrastructure error.
* **`TIMED_OUT`**: Command or task exceeded wall-clock timeout budget; process killed via `SIGKILL`.
* **`KILLED`**: Explicitly terminated by cancellation, user interrupt, or policy violation.
* **`EXPIRED`**: Sandbox TTL exceeded while idle; reclaimed by automatic garbage collection.

### Termination Verification Invariant

No sandbox record may transition to `TERMINATED` without **active verification of absence** from the underlying container provider:
1. `provider.terminate(sandbox_id)` sends SIGTERM/SIGKILL to the container.
2. `provider.cleanup(sandbox_id)` removes volumes, network endpoints, and container instances.
3. `provider.inspect(sandbox_id)` queries the container engine to confirm the container no longer exists.
4. If absence is verified (`inspect() == TERMINATED`), the record transitions to `TERMINATED`.
5. If inspection fails, returns non-terminated status, or provider is offline, the record transitions to `RECOVERY_PENDING`. False cleanup receipts are strictly prohibited.

### Lifecycle Crash Recovery

On system startup, `SandboxLifecycleManager.initialize()` reconciles orphaned sandboxes persisted on disk:
1. Identifies all non-terminal records.
2. Resolves the corresponding provider. If the provider is offline or unavailable, the record fails closed to `RECOVERY_PENDING`.
3. Inspects actual backend state:
   * **If Absent:** Reconciles directly to `TERMINATED` (`reconciled_absent_backend_after_restart`).
   * **If Present:** Initiates termination, cleanup, and absence verification. If verified, marks `TERMINATED`; if unverified, marks `RECOVERY_PENDING`.
   * **If Ambiguous:** Transitions to `RECOVERY_PENDING` with diagnostics.

---

## 5. Isolation Tiers & Autonomy Semantics

### Autonomy Semantics: Security Capability vs. Authorization Decision

> [!IMPORTANT]
> **An isolation tier is a SECURITY CAPABILITY, NOT an authorization decision.**
>
> The presence of `TIER_1_CONTAINER` isolation does **not** grant automatic autonomy. The authorization decision (whether an action executes autonomously, requires HITL confirmation, or is denied) is computed strictly by the **Policy Engine** using comprehensive context:
>
> $$\text{Autonomy} = f(\text{Task Risk}, \text{Capability}, \text{Sandbox Profile}, \text{Backend Caps}, \text{Network Policy}, \text{Credential Policy}, \text{Autonomy Level}, \text{Provenance})$$
>
> Isolation tier defines the execution containment boundary (what kernel protections exist if executed). It is an input to policy evaluation, never a bypass of it.

### Isolation Tiers

* **`TIER_0_LOCAL` (Legacy / Explicitly Unisolated):**
  * Local host process execution via `asyncio.create_subprocess_exec` (`shell=False`).
  * Confinement: Workspace directory checks and environment scrubbing.
  * Security Status: `sandbox_status = NOT_ISOLATED`.
  * Governance: Mandatory `REQUIRE_HITL` for all executions; strictly segregated from sandboxed capabilities. Never an automated fallback.
* **`TIER_1_CONTAINER` (Standard Production Container Isolation):**
  * OCI / Docker container isolation running on Linux or Windows via WSL2/Hyper-V backend.
  * Confinement: Kernel namespaces (PID, mount, network, IPC, UTS), non-root user, dropped capabilities (`--cap-drop ALL`), `no-new-privileges=true`, seccomp, read-only rootfs (`--read-only`), scoped writable workspace, strict cgroup resource limits.
  * Capability: Supports isolated execution; authorization determined by Policy Engine.
  * Network: `NETWORK_NONE` by default.
* **`TIER_2_APPLICATION_SANDBOX` (Application-Kernel Isolation):**
  * User-space kernel virtualization using **gVisor (`runsc`)**.
  * Confinement: Intercepts and executes Linux system calls in a user-space Sentry kernel written in Go, preventing the container workload from directly invoking host Linux kernel syscalls.
  * Capability: Supports user-space syscall containment; authorization determined by Policy Engine.
* **`TIER_3_MICROVM` (Hardware-Virtualized MicroVM Isolation):**
  * Hardware-virtualized lightweight guest-kernel isolation using **Kata Containers** or Firecracker/Cloud-Hypervisor.
  * Confinement: Runs the container inside a dedicated lightweight virtual machine with its own guest kernel, providing hardware virtualization boundaries (VT-x/AMD-V).
  * Capability: Supports hardware-virtualized hypervisor isolation; authorization determined by Policy Engine.


### Typed Sandbox Profile Schema

```python
class NetworkProfile(str, Enum):
    NONE = "none"
    ALLOWLIST = "allowlist"
    FULL = "full"


class ResourceLimits(BaseModel):
    cpu_limit: float = Field(default=1.0, ge=0.1, le=8.0)
    memory_limit_mb: int = Field(default=512, ge=64, le=8192)
    pids_limit: int = Field(default=100, ge=10, le=1000)
    disk_limit_mb: int = Field(default=256, ge=16, le=4096)
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    max_output_bytes: int = Field(default=50_000, ge=1000, le=1_000_000)


class SandboxProfile(BaseModel):
    profile_id: str
    isolation_tier: str  # TIER_1_CONTAINER, TIER_2_MICROVM, TIER_0_LOCAL
    base_image: str = "python:3.11-slim"
    run_as_user: str = "1000:1000"
    read_only_rootfs: bool = True
    no_new_privileges: bool = True
    drop_capabilities: list[str] = Field(default_factory=lambda: ["ALL"])
    add_capabilities: list[str] = Field(default_factory=list)
    seccomp_profile: str = "default"
    network_profile: NetworkProfile = NetworkProfile.NONE
    network_allowlist: list[str] = Field(default_factory=list)
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    allow_docker_socket: bool = False  # Hard invariant: ALWAYS False
    allow_credential_injection: bool = False  # Hard invariant: ALWAYS False
```

---

## 6. Artifact Transfer Specification

To prevent blanket host filesystem exposure, sandboxes do not bind-mount arbitrary host directories. All data movement follows explicit artifact staging:

```text
[Host Filesystem] ──(SHA-256 Digest)──► [Upload Buffer] ──► [Sandbox Workspace]
[Sandbox Workspace] ──(SHA-256 Digest)──► [Download Buffer] ──► [Host Filesystem]
```

### Transfer Record Schema

Every artifact transfer records:
* `transfer_id`: Unique transfer identifier.
* `task_id`: Bound JARVIS task ID.
* `sandbox_id`: Bound sandbox instance ID.
* `direction`: `UPLOAD` (host to sandbox) or `DOWNLOAD` (sandbox to host).
* `host_path`: Canonical path on the host.
* `sandbox_path`: Scoped path inside the sandbox workspace.
* `file_size_bytes`: Bounded size (maximum 10 MB per artifact).
* `sha256_digest`: Cryptographic digest verified before and after transfer.
* `timestamp`: Precise UTC transfer timestamp.

---

## 7. Deep Agents Integration Architecture

JARVIS integrates the Deep Agents harness using the **"sandbox-as-tool"** architecture pattern:

```text
                      JARVIS CONTROL PLANE (Host)
                     ┌───────────────────────────┐
                     │    LangGraph Engine       │
                     │    Policy Engine          │
                     │    Action Broker          │
                     └─────────────┬─────────────┘
                                   │
                                   ▼
                     ┌───────────────────────────┐
                     │   Deep Coding Specialist  │
                     │   - Planning & Todos      │
                     │   - Context Compaction    │
                     │   - Subagent Management   │
                     └─────────────┬─────────────┘
                                   │
               (Tool Invocations Mediated by JARVIS)
                                   │
                                   ▼
                     ┌───────────────────────────┐
                     │    Capability Firewall    │
                     │    Policy Evaluation      │
                     │    Effect Authorization   │
                     └─────────────┬─────────────┘
                                   │
                                   ▼
                     ┌───────────────────────────┐
                     │ Sandbox Capability Bridge │
                     │ (`sandbox:code:execute`)  │
                     └─────────────┬─────────────┘
                                   │
                                   ▼
                     ┌───────────────────────────┐
                     │  Container Sandbox Engine │
                     │  (Isolated Execution)     │
                     └───────────────────────────┘
```

### Architectural Guarantees:
1. **Agent Outside Sandbox:** Deep Agent state, LangGraph checkpoints, and model tokens remain strictly outside the execution container.
2. **Zero Injected Credentials:** LLM API keys (Google, OpenAI, Anthropic, Groq) are never passed to the sandbox.
3. **Mediated Protocol:** The Deep Agent interacts with the sandbox exclusively through governed tools (`sandbox:code:execute`, `sandbox:fs:read`, `sandbox:fs:write`). Every invocation is intercepted by the JARVIS Policy Engine and Action Broker.

---

## 8. Backend Capability Detection & Fail-Closed Invariant

The runtime dynamically discovers available container and virtualization backends on the host.

### 8.1 Windows Isolation Architecture: WSL2 vs. Hyper-V vs. Native Linux

On Windows hosts, Docker Desktop operates through different virtualization and containment engines with materially different security characteristics:

| Backend Engine | Containment Architecture | Kernel Boundary | Windows Host Interop Risk |
| :--- | :--- | :--- | :--- |
| **WSL2 (`DockerBackendType.WSL2`)** | Linux utility VM running a customized Microsoft Linux kernel. | **Shared Linux Kernel:** All WSL2 distributions and container workloads share the same utility VM kernel instance. | Host drives are mounted via `drvfs` / Plan 9 (`/mnt/c/`). Kernel vulnerabilities in container could theoretically affect the utility VM. |
| **Hyper-V (`DockerBackendType.HYPER_V`)** | Dedicated hardware-isolated Hyper-V VM partitions. | **Isolated Hyper-V Partition:** Hardware-level virtualization boundaries (VT-x/AMD-V) provide distinct address spaces. | Recommended by Docker documentation when a strict VM boundary from WSL2's shared-kernel model is required. |
| **Docker VMM (`DockerBackendType.DOCKER_VMM`)** | Proprietary lightweight virtualization engine. | **Lightweight VM:** Managed virtualization layer isolating container runtime from host OS. | Low interop surface; isolated virtual disk images. |
| **Native Linux (`DockerBackendType.NATIVE_LINUX`)** | Bare-metal Linux host execution. | **Host Linux Kernel:** Native cgroups v2, user namespaces, and seccomp BPF filters applied directly. | Direct host kernel interaction governed by namespaces. |

#### WSL Platform vs. User Linux Distros vs. Docker-Managed Environment

The runtime detector strictly separates independent environment properties:
1. **`WSL_PLATFORM_AVAILABLE`:** Probes whether the Windows Subsystem for Linux platform binary (`wsl.exe`) is installed and functional.
2. **`USER_WSL_DISTROS_PRESENT`:** Probes whether user-installed Linux distributions (e.g. Ubuntu, Debian) are configured.
3. **`DOCKER_DESKTOP_INSTALLED`:** Probes whether Docker Desktop is installed on the host across all-users (`%PROGRAMFILES%`) and per-user (`%LOCALAPPDATA%\Programs\DockerDesktop`) locations. Docker Desktop operates its own internal Docker-managed environment and **does not require** user-installed distributions.
4. **`DOCKER_CLI_AVAILABLE`:** Probes whether the `docker` CLI executable is discoverable in the active environment `PATH`.
5. **`DOCKER_DAEMON_AVAILABLE`:** Probes whether the Docker daemon is actively responding to control commands.
6. **`DOCKER_BACKEND`:** Resolves the operational backend engine (`WSL2`, `HYPER_V`, `DOCKER_VMM`, `NATIVE_LINUX`, `UNKNOWN`, `UNAVAILABLE`).
7. **`DOCKER_SERVER_VERSION`:** Discovers the active Docker engine server version string.

This architectural separation enforces the fundamental principle:
$$\text{Installation Presence} \neq \text{CLI Availability} \neq \text{Daemon Reachability} \neq \text{Active Backend Identity} \neq \text{Profile Compatibility} \neq \text{Real Isolation Verification}$$

* **Case A (WSL Installed, 0 User Distros, Docker Inactive):**
  $$\text{WSL Platform} = \text{SUPPORTED} \land \text{User Distros} = \text{UNAVAILABLE} \land \text{Docker Daemon} = \text{UNAVAILABLE} \implies \text{TIER\_1} = \text{UNAVAILABLE}$$
* **Case B (WSL Installed, 0 User Distros, Docker Desktop Active):**
  $$\text{WSL Platform} = \text{SUPPORTED} \land \text{User Distros} = \text{UNAVAILABLE} \land \text{Docker Daemon} = \text{SUPPORTED} \implies \text{TIER\_1} = \text{AVAILABLE (WSL2)}$$

#### Multi-Source Evidence Provenance & Contradiction Resolution

The detector evaluates multi-source evidence using the pure `BackendIdentityResolver` rather than guessing from a single string or conflating container isolation with backend hypervisor identity. Evidence is collected into typed provenance records (`EvidenceProvenance`) categorized by an explicit semantic taxonomy (`EvidenceClassification`):

* **`CONFIGURATION_EVIDENCE`:** Evaluates official configuration stores (`%APPDATA%\Docker\settings.json`, `%APPDATA%\Docker\settings-store.json`, `%PROGRAMDATA%\DockerDesktop\admin-settings.json`, `~/.docker/desktop/settings.json`, `~/.docker/desktop/settings-store.json`) using the documented `wslEngineEnabled` boolean flag. Unverified synthetic keys are prohibited.
* **`CONTAINER_ISOLATION_EVIDENCE`:** Captures container isolation modes (`docker info -> Isolation`: `process` vs `hyperv`) and engine container modes (`docker desktop engine ls`: `linux` vs `windows`). These describe container isolation technology, **not** the underlying VM hypervisor backend.
* **`DAEMON_RUNTIME_EVIDENCE`:** Querying active Docker daemon runtime state via `docker info` JSON (`KernelVersion`, `OperatingSystem`, `SecurityOptions`, `SystemStatus`, `ServerVersion`).
* **`CORROBORATING_EVIDENCE`:** External runtime inspections (`wsl.exe -l -v` tracking running Docker-managed distributions, platform binary presence).
* **`INSTALLATION_EVIDENCE`:** Filesystem package discovery across all-users (`%PROGRAMFILES%`) and per-user (`%LOCALAPPDATA%\Programs\DockerDesktop`) installation trees.

```python
class EvidenceClassification(StrEnum):
    BACKEND_IDENTITY_EVIDENCE = "BACKEND_IDENTITY_EVIDENCE"
    CONTAINER_ISOLATION_EVIDENCE = "CONTAINER_ISOLATION_EVIDENCE"
    DAEMON_RUNTIME_EVIDENCE = "DAEMON_RUNTIME_EVIDENCE"
    CONFIGURATION_EVIDENCE = "CONFIGURATION_EVIDENCE"
    INSTALLATION_EVIDENCE = "INSTALLATION_EVIDENCE"
    CORROBORATING_EVIDENCE = "CORROBORATING_EVIDENCE"
    NON_AUTHORITATIVE = "NON_AUTHORITATIVE"


class EvidenceProvenance(BaseModel):
    source: str
    observed_value: Any
    normalized_value: str
    classification: EvidenceClassification = EvidenceClassification.NON_AUTHORITATIVE
    confidence: str  # "HIGH", "MEDIUM", "LOW"
    timestamp: datetime
    availability: CapabilityStatus


class BackendResolutionResult(BaseModel):
    active_backend: DockerBackendType
    configured_backend: DockerBackendType
    confidence: str  # "HIGH", "MEDIUM", "LOW", "NONE"
    contradictions: tuple[str, ...]
    evidence: tuple[EvidenceProvenance, ...]
```

#### Contradiction & Ambiguity Rules:
1. **Container Isolation vs Backend Hypervisor Separation:** `docker info -> Isolation == "hyperv"` describes Windows container isolation technology. It must NEVER resolve or infer `DockerBackendType.HYPER_V` for the Linux container VM engine.
2. **CLI Engine Scope:** `docker desktop engine ls` describes container modes (Linux vs Windows containers) and is classified as `CONTAINER_ISOLATION_EVIDENCE`; it does not identify or alter the hypervisor backend choice.
3. **Contradiction Detection:** If configuration indicates WSL2 (`wslEngineEnabled: true`) but runtime reports a non-WSL generic kernel (or if `wslEngineEnabled: false` is accompanied by an active WSL2 kernel), the resolver records explicit contradiction diagnostics and fails closed to `DockerBackendType.UNKNOWN` with `confidence = "LOW"`.
4. **Ambiguity Handling:** If evidence is inconclusive (e.g. generic Linux kernel without affirmative WSL2 indicators), the resolver fails closed to `DockerBackendType.UNKNOWN` with `confidence = "NONE"`.
5. **No Inference from Platform Presence:** The presence of `wsl.exe` on Windows does NOT prove that Docker is using WSL2.
6. **No Inference from Stopped Distros:** The presence of a stopped `docker-desktop` distro does NOT prove that WSL2 is the active engine.
7. **No Inference from Negative Flags:** Disabling the WSL engine (`wslEngineEnabled: false`) does NOT prove Hyper-V or Docker VMM without affirmative runtime proof.
8. **Fail-Closed Gate:** In accordance with the fail-closed invariant, `evaluate_profile_satisfaction()` treats `DockerBackendType.UNKNOWN` as unsatisfied, preventing unverified execution.

### 8.2 Profile Attestation Engine (`SandboxAttestation`)

Before a container sandbox is trusted for code execution, the runtime conducts empirical runtime inspection via `provider.attest(sandbox_id)`. Rather than trusting CLI invocation flags alone, the attestation engine queries the container daemon's `docker inspect` API and constructs an `EffectiveSecurityConfiguration`:

```python
class EffectiveSecurityConfiguration(BaseModel):
    image: str
    runtime: str
    network_mode: str
    mounts: tuple[dict[str, Any], ...]
    readonly_rootfs: bool
    drop_capabilities: tuple[str, ...]
    add_capabilities: tuple[str, ...]
    security_options: tuple[str, ...]
    user: str
    pids_limit: int | None
    memory_limit_bytes: int | None
    nano_cpus: int | None
    env_keys: tuple[str, ...]  # Keys only, never secret values
    workspace_mapping: str


class SandboxAttestation(BaseModel):
    attestation_id: UUID
    sandbox_id: UUID
    profile_id: str
    backend_type: BackendType
    docker_backend: DockerBackendType
    backend_version: str
    isolation_tier: IsolationTier
    provider_category: ProviderCategory

    # 12 Individual Security Property Statuses
    host_filesystem_boundary: CapabilityStatus
    workspace_boundary: CapabilityStatus
    docker_socket_boundary: CapabilityStatus
    credential_boundary: CapabilityStatus
    process_namespace_boundary: CapabilityStatus
    child_process_containment: CapabilityStatus
    network_egress_policy: CapabilityStatus
    privilege_boundary: CapabilityStatus
    capability_restriction: CapabilityStatus
    read_only_rootfs: CapabilityStatus
    resource_limits: CapabilityStatus
    lifecycle_cleanup: CapabilityStatus

    status: AttestationStatus
    overall_state: AttestationStatus
    effective_configuration: EffectiveSecurityConfiguration | None
    failures: tuple[str, ...]
    unknowns: tuple[str, ...]
    unsatisfied_constraints: tuple[str, ...]
    evidence: dict[str, Any]
    verified_at: datetime
```

#### Configuration Evidence vs. Behavioral Runtime Verification

A critical architectural distinction is enforced between configuration evidence and behavioral runtime verification:
1. **Configuration Evidence (Static Inspection):** Inspecting container parameters via `docker inspect` demonstrates that hardening flags were passed to the engine. It proves configuration intent, but does not prove kernel boundary enforcement.
2. **Behavioral Runtime Verification (Dynamic Probing):** Executing hostile adversarial workloads within the running container actively probes the kernel boundaries. Only empirical hostile probe execution proves runtime isolation.
3. **Absence Verification in Lifecycle Termination:** Calling `docker rm -f` initiates container deletion, but does not guarantee the container has been destroyed. The provider must re-query the engine (`docker inspect <container>`) to affirmatively confirm absence before transitioning to `SandboxState.TERMINATED`. If absence cannot be confirmed, the lifecycle fails closed to `SandboxState.UNKNOWN`.

---

## 9. Security Exit Gate Requirements & The 12-Probe Sentinel Suite

Before isolated execution can be qualified as `TIER_1_REAL_ISOLATION_VERIFIED`, the backend must execute and pass the automated 12-Probe Sentinel Security Suite (`SentinelIsolationVerifier`):

| Probe Identifier | Security Boundary Tested | Execution Mechanism | Expected Verdict |
| :--- | :--- | :--- | :--- |
| **`HOST_FILESYSTEM_BOUNDARY`** | Host Filesystem Inaccessibility | Workload attempts to stat and read a controlled host sentinel file outside workspace. | `INACCESSIBLE` |
| **`WORKSPACE_BOUNDARY`** | Workspace Boundary Access | Workload reads and writes explicitly prepared artifacts inside `/workspace`. | `PASSED` |
| **`DOCKER_SOCKET_BOUNDARY`** | Docker Socket Inaccessibility | Workload probes for `/var/run/docker.sock`, `/run/docker.sock`, and daemon sockets. | `INACCESSIBLE` |
| **`CREDENTIAL_BOUNDARY`** | Host Credential Inaccessibility | Workload searches for host `.aws/`, `.ssh/`, `.env` sentinels and secret env tokens. | `INACCESSIBLE` |
| **`PROCESS_NAMESPACE_BOUNDARY`** | Process Namespace Isolation | Workload enumerates `/proc` PID tree; host processes must not appear. | `PASSED` |
| **`CHILD_PROCESS_CONTAINMENT`** | Child Process Containment | Workload spawns child processes; process tree must remain confined to container. | `PASSED` |
| **`NETWORK_EGRESS_POLICY`** | Default-Deny Network Egress | Workload attempts outbound DNS and TCP connection under `NETWORK_NONE`. | `BLOCKED` |
| **`PRIVILEGE_BOUNDARY`** | Non-Root Privilege Confinement | Workload inspects `os.getuid()` and attempts `setuid(0)`; must be non-root UID. | `PASSED` |
| **`CAPABILITY_RESTRICTION`** | Capability Dropping (`ALL`) | Workload reads `/proc/self/status` `CapEff`; effective capability set must be 0. | `PASSED` |
| **`READ_ONLY_ROOTFS`** | Read-Only Root Filesystem | Workload attempts write to `/etc/` (must fail) and `/workspace/` (must succeed). | `PASSED` |
| **`RESOURCE_LIMITS`** | Cgroup Quota Enforcement | Workload tests cgroup controllers (`pids.max`, `memory.max`); quotas must be bound. | `PASSED` |
| **`LIFECYCLE_CLEANUP`** | Container Absence Verification | Provider terminates container and confirms complete absence via backend inspection. | `PASSED` |

### Qualification Categories
* **`VERIFIED`**: Validated with empirical execution against an operational container backend on the host.
* **`NOT_VERIFIED`**: Implemented in code and verified under contract test doubles, but awaiting operational backend on host.
* **`UNSUPPORTED_BACKEND`**: Required feature is not supported by host environment.
* **`BLOCKED`**: Adversarial action was successfully prevented by the security boundary.
* **`FAILED`**: Isolation failed or security boundary was breached.

---

## 10. Host Capability Truth & Current Qualification Status

```text
================================================================================
JARVIS EXECUTION FABRIC — HOST CAPABILITY TRUTH REPORT
================================================================================
Host Operating System:           Windows 11 (Build 10.0.26200)
Host Architecture:               AMD64 / x86_64
WSL Platform Available:          SUPPORTED (wsl.exe present in Windows System32)
WSL User Distros Present:        UNAVAILABLE (0 user Linux distributions installed)
Docker Desktop Installed:        UNAVAILABLE (Not detected in standard system locations)
Docker CLI Executable:           UNAVAILABLE (Not found in system PATH)
Docker Daemon Socket:            UNAVAILABLE (Daemon is inactive)
Docker Backend Type:             UNAVAILABLE

TIER_1 IMPLEMENTED:              YES
TIER_1 CONTRACT_VERIFIED:        YES (Full lifecycle, transition matrix & recovery)
TIER_1 REAL_ISOLATION_VERIFIED:  NO
REASON:                          BACKEND UNAVAILABLE (Docker CLI/Daemon inactive on host)

TIER_2 (gVisor/runsc):           UNAVAILABLE (Requires Linux host/WSL2 gVisor config)
TIER_3 (Kata Containers):        UNAVAILABLE (Requires bare-metal hypervisor/nested VT-x)
================================================================================
```

---

## 11. Deep Agents Execution Layer Governance Architecture

### 11.1 Authority and Control Boundaries

Under the JARVIS zero-trust model, the LangChain Deep Agents orchestration framework (`deepagents`) is integrated strictly as an execution intelligence subsystem (Layers 12–14), never as an authorization authority:

```text
User / Intent
     │
     ▼
JARVIS Control Plane (Policy Engine, Capability Firewall, Action Broker)
     │
     ▼
Governed Deep Agent Factory (`create_governed_deep_agent`)
     │
     ├── Projection Middleware (`JarvisToolGovernanceMiddleware`)
     │      └── Capability Non-Disclosure (Tool stripping at Model Request)
     │      └── Zero-Trust Authorization (Tool Call Interception)
     │
     ├── Subagent Governor (`JarvisSubagentGovernor`)
     │      └── Monotonic Attenuation (`child ⊆ parent`)
     │      └── Anti-Escalation & Nesting Constraints
     │
     ▼
Execution Fabric (`JarvisSandboxBackend` : `BaseSandbox`)
     │
     ▼
Sandbox Provider (`DockerSandboxProvider` / `SandboxLifecycleManager`)
     │
     ▼
Container Absence Verification & Lifecycle Termination
```

### 11.2 The Sandbox Backend Adapter (`JarvisSandboxBackend`)

JARVIS provides `JarvisSandboxBackend`, a concrete subclass of `deepagents.backends.sandbox.BaseSandbox` wrapping `SandboxProvider` and `SandboxLifecycleManager`:

1. **Explicit Backend Binding:** Unbound or implicit host process execution (`LocalShellBackend`) is strictly prohibited. Every Deep Agent execution must bind to a verified sandbox backend.
2. **Fail-Closed Availability Guard:** If the host environment lacks an operational container backend (e.g. Docker daemon is inactive) and explicit test-double execution is not authorized, creation and execution fail closed immediately.
3. **Async and Sync Interface Support:** Implements both synchronous and asynchronous execution primitives (`execute`, `aexecute`, `upload_files`, `download_files`).
4. **Defensive Path and Buffer Handling:**
   - Path traversal defense: Relative paths are validated to prevent `..` escape outside `/workspace`.
   - Output buffer truncation: Execution outputs exceeding buffer limits are truncated safely with diagnostic notices.
   - SHA-256 digest calculation: File transfers compute and log cryptographic digests for external attestation.

### 11.3 Capability Non-Disclosure and Tool Projection

The LLM is an untrusted entity. Exposing unauthorized tools in the tool schema invites prompt injection, unauthorized invocation attempts, and hallucinated executions.

1. **Least-Privilege Projection (`JarvisCapabilityProjector`):** Calculates the exact subset of authorized tools based on task-assigned scopes and dynamic autonomy levels (e.g. `OBSERVE_ONLY` projects zero mutating tools, `AUTO_READ_ONLY` projects read-only tools).
2. **Model Call Interception (`wrap_model_call` / `awrap_model_call`):** Strips all unauthorized tools from `ModelRequest.tools` before the request is transmitted to the language model.
3. **Tool Call Interception (`wrap_tool_call` / `awrap_tool_call`):** Intercepts every tool invocation attempt:
   - Blocks any tool not in the authorized projection.
   - Evaluates central `PolicyEngine` rules in real time.
   - Halts execution cleanly with structured `ToolMessage` if `REQUIRE_HITL` is signaled.
   - Rejects the call if `DENY` is returned.

### 11.4 Monotonic Subagent Attenuation (`JarvisSubagentGovernor`)

When a Deep Agent spawns child subagents via delegation, authority is strictly attenuated:

1. **Monotonic Invariant (`child ⊆ parent`):**
   - `child.authorized_tools ⊆ parent.authorized_tools`
   - `child.authorized_scopes ⊆ parent.authorized_scopes`
   - `child.autonomy_level <= parent.autonomy_level`
2. **Anti-Escalation Enforcement:** Any attempt by a subagent to declare capabilities, scopes, or autonomy exceeding its parent is rejected with `SubagentPrivilegeEscalationError`.
3. **Delegation Scope Verification:** Subagent spawning requires explicit `agent:subagent:delegate` permission.
4. **Nesting Depth Caps:** Recursive subagent creation is strictly bounded to prevent resource exhaustion.
5. **Governance Inheritance:** Child agents automatically inherit governance middleware and backend lifecycle bindings.

### 11.5 Lifecycle Termination and Container Absence Verification

When a governed Deep Agent terminates or errors:
1. `GovernedDeepAgentHandle.terminate()` triggers graceful quiesce, process termination, and container destruction via `SandboxLifecycleManager`.
2. Post-termination absence inspection affirmatively verifies that the container ID is no longer present in the engine.
3. If absence cannot be confirmed, the handle reports `absence_verified = False`, triggering alert escalation.

### 11.6 Host Capability Attestation Reality

On the current host system:
- Docker daemon is inactive.
- Real Tier-1 isolation remains `NOT_VERIFIED`.
- Test doubles are permitted solely in designated test harnesses and explicitly report `real_isolation_verified = False`.

