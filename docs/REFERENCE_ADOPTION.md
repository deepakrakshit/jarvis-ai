# Upstream Pattern Adoption & Provenance Record

## 1. Overview & Provenance

This document records the design patterns, security controls, and operational architectures adapted from the upstream reference codebase (`secret/`) into JARVIS.

* **Upstream Commit SHA:** `77bcdfff337e1d3cd207ed13e6393b8783d7a2c3`
* **Upstream License:** MIT License (with Third-Party Notices as documented upstream)
* **Authoritative JARVIS Policy:** The reference codebase serves strictly as a pattern source. JARVIS retains canonical authority over the control plane, trust boundaries, Capability Firewall, Policy Engine, and Action Broker.

---

## 2. Adoption & Adaptation Mapping

### A. Sandbox Provider Abstraction & Registry
* **Upstream Source:** `secret/src/agents/sandbox/backend.ts`, `backend.types.ts`, `backend-handle.types.ts`
* **JARVIS Target:** [`jarvis/sandbox/provider.py`](file:///C:/Users/deepa/OneDrive/Desktop/JARVIS-AI/jarvis/sandbox/provider.py), [`jarvis/sandbox/registry.py`](file:///C:/Users/deepa/OneDrive/Desktop/JARVIS-AI/jarvis/sandbox/registry.py)
* **Adopted Behavior:** Clean separation of provider lifecycle methods (`create`, `start`, `health_check`, `execute`, `upload_artifacts`, `download_artifacts`, `inspect`, `terminate`, `cleanup`, `attest`).
* **Adapted Behavior:** Re-implemented in Python 3.11 with async/await and Pydantic models. Separates `REAL_ISOLATION_PROVIDER` from `TEST_DOUBLE`.
* **Intentionally Excluded:** Upstream's Node process handle callbacks and SQLite table coupling.

### B. Security Validation & Guard Rails
* **Upstream Source:** `secret/src/agents/sandbox/validate-sandbox-security.ts`, `host-paths.ts`, `network-mode.ts`
* **JARVIS Target:** [`jarvis/sandbox/security.py`](file:///C:/Users/deepa/OneDrive/Desktop/JARVIS-AI/jarvis/sandbox/security.py)
* **Adopted Behavior:** Blocked host system root paths (`/etc`, `/proc`, `/sys`, `/run/docker.sock`, Windows `System32`), user credential folders (`.ssh`, `.aws`, `.azure`, `.docker`, `.env`), traversal tokens (`..`), UNC paths, and Windows drive-letter escapes.
* **Adapted Behavior:** Re-implemented using cross-platform `pathlib.Path.resolve()` with ancestor traversal for symlink protection.
* **Intentionally Excluded:** Upstream POSIX-specific `lstat` assumptions.

### C. Container Lifecycle & Orphan Recovery
* **Upstream Source:** `secret/src/agents/sandbox/registry.ts`, `prune.ts`, `docker-partial-cleanup.ts`
* **JARVIS Target:** [`jarvis/sandbox/manager.py`](file:///C:/Users/deepa/OneDrive/Desktop/JARVIS-AI/jarvis/sandbox/manager.py)
* **Adopted Behavior:** Strict lifecycle state machine, serialized container operations (`asyncio.Lock`), and crash-recovery reconciliation on restart.
* **Adapted Behavior:** Durable JSON records per sandbox; explicit `inspect()` call to empirically verify absence before transitioning to `TERMINATED`.
* **Intentionally Excluded:** Upstream marks containers terminated immediately on `docker rm` exit 0 without post-removal inspection.

### D. Canonical Sandbox Execution Tool
* **Upstream Source:** `secret/src/agents/sandbox/docker-backend.ts`, `docker.ts`
* **JARVIS Target:** [`jarvis/tools/native/sandbox_code.py`](file:///C:/Users/deepa/OneDrive/Desktop/JARVIS-AI/jarvis/tools/native/sandbox_code.py)
* **Adopted Behavior:** Hardened container arguments (`--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`, `--init`, `--pids-limit`, `--memory`).
* **Adapted Behavior:** Registered as native capability `sandbox:code:execute` under JARVIS Capability Firewall with fail-closed semantics.
* **Intentionally Excluded:** Upstream fallback to host execution when sandboxes are unavailable. In JARVIS, host fallback is strictly prohibited.

### E. Sandbox Inspection & Capability Reporting
* **Upstream Source:** `secret/src/agents/sandbox/manage.ts`, `runtime-status.ts`
* **JARVIS Target:** [`jarvis/sandbox/inspector.py`](file:///C:/Users/deepa/OneDrive/Desktop/JARVIS-AI/jarvis/sandbox/inspector.py), [`jarvis/cli.py`](file:///C:/Users/deepa/OneDrive/Desktop/JARVIS-AI/jarvis/cli.py)
* **Adopted Behavior:** Diagnostic commands explaining sandbox configuration, mounts, network policy, and attestation.
* **Adapted Behavior:** Exposed via CLI commands `jarvis sandbox explain` and `jarvis sandbox report`.
* **Intentionally Excluded:** Output of internal database IDs or stack traces.

---

## 3. Core Differences & Security Hardening in JARVIS

| Architectural Concern | Upstream Reference Pattern | JARVIS Hardened Implementation |
| :--- | :--- | :--- |
| **Trust Boundary** | Agent configuration controls sandboxing. | Untrusted LLM/agent; Policy Engine and Capability Firewall govern authority. |
| **Failure Semantics** | Silent fallback to host execution when disabled or offline. | Strict fail-closed: raises `SandboxBackendUnavailableError`; zero host fallback. |
| **Elevated Execution** | Provides `elevated: true` flag to escape sandboxes to host. | Completely rejected. Host tools remain separate, explicitly governed capabilities. |
| **Host Reality** | Assumes WSL presence implies Linux container readiness. | Evidence-based detection; Docker socket must be live. Windows host qualifies as `NOT_VERIFIED`. |
| **Evidence Validity** | Test doubles can satisfy verification assertions. | Test doubles strictly report `NOT_VERIFIED`; real evidence requires real execution. |

---

## 4. Test Suite Validation

The following dedicated test suites validate the integrated patterns:
* [`tests/unit/sandbox/test_sandbox_code_capability.py`](file:///C:/Users/deepa/OneDrive/Desktop/JARVIS-AI/tests/unit/sandbox/test_sandbox_code_capability.py): Lifecycle and timeout handling for `sandbox:code:execute`.
* [`tests/unit/sandbox/test_failure_safety_no_host_fallback.py`](file:///C:/Users/deepa/OneDrive/Desktop/JARVIS-AI/tests/unit/sandbox/test_failure_safety_no_host_fallback.py): Proof that broken providers fail closed without calling host runners.
* [`tests/unit/sandbox/test_security_path_guard.py`](file:///C:/Users/deepa/OneDrive/Desktop/JARVIS-AI/tests/unit/sandbox/test_security_path_guard.py): Host path denylist, traversal prevention, and allowed root constraints.
* [`tests/unit/sandbox/test_inspector.py`](file:///C:/Users/deepa/OneDrive/Desktop/JARVIS-AI/tests/unit/sandbox/test_inspector.py): Machine-readable capability and explanation structures.
* [`tests/unit/agents/test_coding_specialist_modernized.py`](file:///C:/Users/deepa/OneDrive/Desktop/JARVIS-AI/tests/unit/agents/test_coding_specialist_modernized.py): Coding agent proposal and synthesis of sandbox execution.
* [`tests/unit/sandbox/test_lifecycle_manager.py`](file:///C:/Users/deepa/OneDrive/Desktop/JARVIS-AI/tests/unit/sandbox/test_lifecycle_manager.py): Crash recovery, timeout, and cancellation propagation.
