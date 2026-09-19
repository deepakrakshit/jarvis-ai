# JARVIS Feature Inventory & Capability Architecture

**Version:** 1.0.0  
**Domain:** Functional Capabilities & Architecture Matrix  
**Authority:** Master Build Directive & `ARCHITECTURE.md`

---

## 1. Feature Architecture Overview

JARVIS integrates sixteen core capability domains into a unified personal AI operating system:

```text
                                 ╔═══════════════════════════════════════════════════╗
                                 ║               JARVIS PERSONAL AI OS               ║
                                 ╚═══════════════════════════════════════════════════╝
                                                           │
         ┌───────────────────┬───────────────────┬─────────┴─────────┬───────────────────┬───────────────────┐
         ▼                   ▼                   ▼                   ▼                   ▼                   ▼
 ┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
 │ Gemini 3.8    │   │ Multi-Model   │   │ Control Plane │   │ Policy Engine │   │ Windows Node  │   │ Browser & Web │
 │ Live Bridge   │   │ Router & Quota│   │ & LangGraph   │   │ & Broker      │   │ & OS Control  │   │ Automation    │
 └───────────────┘   └───────────────┘   └───────────────┘   └───────────────┘   └───────────────┘   └───────────────┘
         │                   │                   │                   │                   │                   │
         ▼                   ▼                   ▼                   ▼                   ▼                   ▼
 ┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
 │ Memory Plane  │   │ Subagents     │   │ Android Node  │   │ Automation    │   │ Verification  │   │ Dynamic UI &  │
 │ & Governance  │   │ & ACP Coding  │   │ & Telemetry   │   │ & Schedules   │   │ & Recovery    │   │ Artifacts     │
 └───────────────┘   └───────────────┘   └───────────────┘   └───────────────┘   └───────────────┘   └───────────────┘
```

---

## 2. Detailed Capability Domain Matrix

### Domain 1: Realtime Voice & Multimodal Interaction
- **Primary Engine:** Gemini 3.8 Live over bidirectional WebSockets.
- **Capabilities:**
  - Full-duplex low-latency audio input and output.
  - Streaming text input and output.
  - Realtime video and screen frame analysis.
  - Conversational barge-in and voice interruption detection.
  - Session resumption and reconnect resilience without loss of long-term state.

### Domain 2: Multi-Model Routing & Quota Management
- **Strict 6-Model Allowlist:**
  1. `Gemini 3.8 Live` — Realtime voice, audio I/O, streaming conversational turns, live vision.
  2. `GPT-OSS 120B` — Complex multi-step reasoning, difficult coding refactors, verification analysis.
  3. `Qwen 3.8 27B` — Multimodal vision, image analysis, coding, structured data processing.
  4. `Gemini 3.1 Flash-Lite` — Fast classification, query normalization, simple tool calls, high-volume tasks.
  5. `Gemini 3.5 Flash-Lite` — Context extraction, document summarization, lightweight background workers.
  6. `Gemma 4 31B` — Media understanding, background worker agents, moderate reasoning.
- **Quota Tracking:** Realtime tracking of RPM, TPM, RPD, reset epochs, latency, and provider health.
- **Pre-Execution Budgeting:** Rejects or reroutes plans that would violate token or rate limits.

### Domain 3: Control Plane & Task State Machine
- **Framework:** LangGraph stateful task graphs in Python.
- **Lifecycle States:** `CREATED` → `NORMALIZING` → `CLASSIFIED` → `CONTEXT_READY` → `PLANNED` → `POLICY_CHECK` → `AUTHORIZED` → `DISPATCHED` → `RUNNING` → `OBSERVING` → `VERIFYING` → `COMPLETED`.
- **Fault Tolerance:** Checkpointed transitions allowing crash recovery and task resumption.

### Domain 4: Security, Policy Engine & Action Broker
- **Core Principle:** The model is not the trust boundary.
- **Policy Engine:** Evaluates identity, session, capability, risk tier, target, and approvals.
- **Capability Firewall:** Canonical capability names (`filesystem.*`, `process.*`, `shell.*`, `browser.*`, `computer.*`, `android.*`, `system.*`).
- **Action Broker:** The exclusive bridge for real-world side effects. Issues immutable `ActionRequest` and `ActionResult` pairs with unique Action IDs and cryptographic provenance.

### Domain 5: Windows OS & Native Host Control
- **Windows Body Engine:**
  - Process lifecycle: Launch, enumerate, monitor, and terminate Windows processes.
  - Shell execution: Governed PowerShell and CMD command runners with timeouts and output capture.
  - Filesystem management: Sandboxed read, write, edit, search, and guarded deletion.
  - Desktop control: Screenshot capture, coordinate translation, mouse clicks, keyboard entry.
  - System controls: Audio volume (PyCAW), display brightness, network state, battery, notifications.

### Domain 6: Browser Control & Web Research
- **Substrate:** OpenClaw browser extension / Playwright engine.
- **Capabilities:** Managed profiles, DOM tree snapshots, interactive click/type/drag, screenshot capture, file downloads, network inspection, and untrusted-content sanitization.

### Domain 7: Memory Plane & Context Engine
- **Memory Categories:** Working memory, episodic memory, semantic memory, user preferences, project memory, and learned procedures.
- **Governance:** Provenance tracking (trusted user instructions vs untrusted web content), TTL retention, and relevance filtering to prevent context bloat.

### Domain 8: Specialist Subagents & ACP External Coding
- **Specialist Agents:** Research Agent, Coding Agent, Browser Agent, Verification Agent.
- **Privilege Floor:** Subagents can never escalate beyond their parent task permissions.
- **ACP Integration:** Delegation of repository-scale coding, testing, and PR creation to autonomous coding harnesses.

### Domain 9: Android Device Integration
- **Node Architecture:** Android companion node with mutual cryptographic pairing.
- **Capabilities:** Telemetry, battery/device status, notifications, camera capture, location, contacts, and dedicated ADB/accessibility UI control bridge.

### Domain 10: Automation, Schedules & Heartbeats
- **Engines:** Cron scheduler, event triggers (file change, system boot, network change), periodic heartbeats for background awareness and proactive assistance.

### Domain 11: Verification & Recovery Engine
- **Pattern:** `PLAN` → `ACT` → `OBSERVE` → `VERIFY` → `COMPLETE` / `RECOVER`.
- **Verification Modes:** State inspection, process checks, DOM assertions, second-model review.
- **Recovery:** Exponential backoff, strategy switching, alternate model failover, and operator escalation.

### Domain 12: Artifacts & Dynamic UI
- **Artifacts:** Structured storage and referencing of screenshots, logs, patches, diffs, and generated reports.
- **Control Center UI:** Interactive dashboard for conversation, voice visualization, task timelines, active models, quotas, approvals, and system health.
