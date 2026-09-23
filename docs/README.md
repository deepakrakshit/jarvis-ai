# JARVIS System Documentation

Welcome to the official technical documentation for **JARVIS**, a stateful, multimodal, personal artificial intelligence operating system designed for deep Windows desktop interaction, real-time voice and vision cognition, and autonomous task execution.

---

## 1. Documentation Index

| Document | Focus & Scope |
| :--- | :--- |
| **[Architecture](ARCHITECTURE.md)** | End-to-end system design, dual-engine cognitive core, trust boundaries, and execution fabric. |
| **[Audio & Acoustic Echo Cancellation](AUDIO_AEC_SUBSYSTEM.md)** | WASAPI loopback capture, PBFDAF adaptive filtering, double-talk detection, and spectral suppression. |
| **[Application Control Engine](APPLICATION_CONTROL_ENGINE.md)** | Multi-provider automation hierarchy: Programmatic URI, COM UI Automation, Playwright, and CUA fallback. |
| **[Execution Substrate & IPC](EXECUTION_SUBSTRATE_IPC.md)** | Native Node.js execution substrate daemon, JSON-RPC IPC, coordinate actions, and display pipelines. |
| **[Gateway & Control Plane](GATEWAY_AND_CONTROL_PLANE.md)** | Typed WebSocket daemon, session state, heartbeat scheduling, tool broker, and agent delegation. |
| **[Configuration & Security](CONFIGURATION_AND_SECURITY.md)** | Dynamic configuration, zero-hardcoding invariants, capability firewall, and credentials governance. |

---

## 2. System Philosophy & Core Invariants

1. **The Model is Not the Trust Boundary:** Models propose actions and generate natural dialogue, but the local control plane enforces policy, validation, approvals, and authorization.
2. **Zero Hardcoding Invariant:** All paths, models, endpoints, timeouts, and thresholds are discoverable and dynamically resolved via configuration schemas or runtime environment context.
3. **Closed-Loop Verification:** Desktop interactions must execute through an Observe $\rightarrow$ Act $\rightarrow$ Verify sequence to confirm state transitions before reporting completion.
4. **Deterministic Audio Isolation:** Microphone audio sent to real-time cognitive models is filtered using hardware loopback reference subtraction, preventing assistant self-echo or speaker contamination.
