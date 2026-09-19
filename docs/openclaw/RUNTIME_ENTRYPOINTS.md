# JARVIS Runtime Entrypoints & Process Architecture

**Version:** 1.0.0  
**Domain:** System Bootstrap & Lifecycle Management  
**Authority:** Master Build Directive & `ARCHITECTURE.md`

---

## 1. System Entrypoints Overview

JARVIS coordinates multiple process entrypoints across Python and Node.js environments.

```text
                               ┌────────────────────────┐
                               │     USER / OPERATOR    │
                               │  (CLI / Voice / Web)   │
                               └───────────┬────────────┘
                                           │
                                           ▼
                               ┌────────────────────────┐
                               │  jarvis.bootstrap      │
                               │  (Python Entrypoint)   │
                               └───────────┬────────────┘
                                           │
                        ┌──────────────────┴──────────────────┐
                        ▼                                     ▼
             ┌─────────────────────┐               ┌─────────────────────┐
             │ JARVIS Control      │               │ OpenClaw Substrate  │
             │ Plane Daemon        │               │ Gateway / Nodes     │
             │ - LangGraph Engine  │               │ - WS Gateway        │
             │ - Gemini Live Bridge│   IPC / RPC   │ - Browser Daemon    │
             │ - Policy Engine     │◄─────────────►│ - SQLite Stores     │
             │ - Action Broker     │               │ - ACP Harness       │
             │ - Windows Body      │               │ - Skills Registry   │
             └─────────────────────┘               └─────────────────────┘
```

---

## 2. JARVIS Python Entrypoints

| Module / Script | Class / Function | Purpose |
| :--- | :--- | :--- |
| `jarvis.main` | `main()` / `asyncio.run(boot_jarvis())` | Master CLI and runtime bootstrapper; parses CLI flags, loads `.env`, initializes services. |
| `jarvis.core.control_plane` | `JarvisControlPlane` | Coordinates task creation, state transitions, model planning, and verification loops. |
| `jarvis.cognition.gemini_live` | `GeminiLiveBridge` | Manages real-time bidirectional WebSocket stream with Gemini 3.8 Live. |
| `jarvis.policy.engine` | `PolicyEngine` | Evaluates action requests against capability rules, trust tiers, and approval requirements. |
| `jarvis.actions.broker` | `ActionBroker` | Canonical executor dispatching approved actions to Windows, Browser, or Node backends. |
| `jarvis.execution.windows` | `WindowsNode` | Host OS management (PowerShell, Win32 APIs, UI Automation, process inspection). |

---

## 3. OpenClaw Substrate Entrypoints

| Path | Type | Purpose |
| :--- | :--- | :--- |
| `OpenClaw/openclaw.mjs` | Node Executable | CLI bootstrap wrapper; verifies Node/SQLite environment, configures compile cache, respawns into `src/entry.ts`. |
| `OpenClaw/src/entry.ts` | TypeScript Entry | Main CLI entry point; handles command dispatch (`gateway`, `doctor`, `models`, `cron`). |
| `OpenClaw/src/gateway/server.ts` | Server Daemon | Gateway daemon hosting WebSocket server, HTTP API, and session routing. |
| `OpenClaw/extensions/browser/src/server.ts` | Browser Worker | Dedicated Playwright/CDP browser management service. |
| `OpenClaw/packages/acp-core/` | ACP Harness | External coding agent execution harness. |

---

## 4. Boot Sequence & Lifecycle States

The JARVIS boot sequence executes in deterministic order:

```text
[BOOT:1] Load & Validate Configuration
  ├─ Discover & load environment variables (.env)
  ├─ Verify API keys (GEMINI_API_KEY, GROQ_API_KEY)
  └─ Initialize dynamic logging & telemetry

[BOOT:2] Initialize Security & Policy Fabric
  ├─ Initialize PolicyEngine with fail-closed default rules
  ├─ Initialize CapabilityFirewall with canonical identifiers
  └─ Verify sandbox & path isolation boundaries

[BOOT:3] Initialize Storage & State
  ├─ Connect to durable state SQLite database
  ├─ Execute pending database migrations
  └─ Reconcile pending/unsettled tasks from previous run

[BOOT:4] Initialize Model Fabric & Quota Engine
  ├─ Register the 6 approved model families
  ├─ Query initial provider quotas & health scores
  └─ Initialize ModelRouter with latency & token budget estimators

[BOOT:5] Initialize Action Broker & Execution Fabric
  ├─ Register Windows native execution engine
  ├─ Probe host display, audio, and shell capabilities
  └─ Connect to browser automation worker if enabled

[BOOT:6] Start Gateway & Bridges
  ├─ Start JARVIS Control Plane event loop
  ├─ Establish Gemini 3.8 Live session bridge
  └─ Transition system status to ONLINE
```

---

## 5. Graceful Shutdown & Emergency Stop

- **SIGINT / SIGTERM Handling:** Traps signals, halts acceptance of new tasks, cancels pending non-idempotent action requests, persists state checkpoints to SQLite, and flushes audit logs.
- **Emergency Stop (`jarvis.core.emergency_stop`):** Independent hardware/software kill switch that immediately revokes all capability tokens, terminates active child processes, and forces the system into `SAFE_STATE` without waiting for LLM completion.
