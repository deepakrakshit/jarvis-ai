# JARVIS ↔ OpenClaw Source Map & Architectural Classification

**Version:** 1.0.0  
**Project:** JARVIS Personal AI Operating System  
**Substrate:** OpenClaw Monorepo (MIT Licensed)  
**Governance:** Architecture Baseline (`ARCHITECTURE.md`)

---

## 1. Architectural Strategy & Ownership Principles

JARVIS treats OpenClaw as an extensive, mature implementation substrate rather than rebuilding complex plumbing from zero. However, JARVIS exercises strict ownership inversion:

```text
JARVIS Control Plane & Security Authority
                    │
                    ▼
       JARVIS Compatibility Boundary
                    │
                    ▼
    OpenClaw-Derived Subsystems & Nodes
                    │
                    ▼
             Physical Execution
```

Every subsystem in OpenClaw is classified into one of seven formal disposition categories:
1. `KEEP` — Retained unmodified in its upstream location, used as a direct internal dependency.
2. `KEEP + JARVIS WRAPPER` — Retained, but accessed strictly through a JARVIS-typed interface.
3. `ADAPT` — Lightly modified or extended with JARVIS policy hooks, telemetry, or configuration bridges.
4. `JARVIS OVERRIDE` — Superseded by a JARVIS-owned implementation (e.g., Control Plane, Policy Engine, Action Broker, Model Router, Quota Manager).
5. `RUN AS EXTERNAL SERVICE` — Executed in an isolated process/worker boundary (e.g., Windows Node, Browser Worker, Container Sandbox).
6. `OPTIONAL` — Non-essential capabilities or platform connectors evaluated on demand.
7. `REMOVE LATER` — Deprecated or unneeded channels/runtimes flagged for dependency-closed pruning.

---

## 2. Comprehensive Subsystem Classification Matrix

| OpenClaw Subsystem Path | Description & Functional Responsibility | Upstream Dependencies | Current OpenClaw Owner | Future JARVIS Owner | Disposition | Direct / Mod / New | Retain Tests | Attribution / License | Priority | Risk |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `packages/gateway-protocol/` | Typed WebSocket & HTTP protocol schemas, validators, and error contracts | None (pure TS schemas) | `gateway-protocol` | `core.protocol` | `KEEP + JARVIS WRAPPER` | Direct + Wrap | Yes | MIT (OpenClaw Foundation) | Immediate | Low |
| `packages/gateway-client/` | Client transport, WebSocket reconnect, watchdog, device auth | `gateway-protocol`, `ws` | `gateway-client` | `core.transport.client` | `KEEP + JARVIS WRAPPER` | Direct + Wrap | Yes | MIT (OpenClaw Foundation) | Immediate | Low |
| `src/gateway/` | Gateway server daemon, HTTP/WS endpoints, auth session routing | `packages/*`, `node:sqlite`, `Kysely` | `gateway` | `gateway.daemon` | `ADAPT` | Modified | Yes | MIT (OpenClaw Foundation) | High | Medium |
| `src/security/` | Basic audit checks, path validation, secret masks, dangerous flags | Core utils | `security` | `policy.engine` | `JARVIS OVERRIDE` | JARVIS Owned | Extend | MIT (OpenClaw Foundation) | Critical | High |
| `src/agents/agent-tools*.ts` | Tool registry, execution hooks, tool definitions | `packages/agent-core` | `agents.tools` | `actions.registry` | `ADAPT` | Modified | Yes | MIT (OpenClaw Foundation) | High | Medium |
| `src/agents/runtime/` | Agent loop, model turns, streaming, compaction | `packages/llm-core` | `agents.runtime` | `core.runtime` | `KEEP + JARVIS WRAPPER` | Direct + Wrap | Yes | MIT (OpenClaw Foundation) | High | Medium |
| `src/agents/sessions/` | Session persistence, transcripts, row projection | SQLite, FS | `agents.sessions` | `core.sessions` | `ADAPT` | Modified | Yes | MIT (OpenClaw Foundation) | High | Medium |
| `src/llm/` & `packages/llm-core/` | Model providers, streaming adapters, rate limits | Provider SDKs, HTTP | `llm` | `cognition.providers` | `KEEP + JARVIS WRAPPER` | Direct + Wrap | Yes | MIT (OpenClaw Foundation) | Critical | Medium |
| `extensions/browser/` | Browser service, CDP integration, snapshotting, DOM automation | Playwright / Puppeteer | `extensions.browser` | `execution.browser` | `RUN AS EXTERNAL SERVICE` | Direct + Wrap | Yes | MIT (OpenClaw Foundation) | High | Medium |
| `src/daemon/` & Windows Tasks | Windows task supervisor, schtasks probe, process trees | Windows APIs, `schtasks` | `daemon.windows` | `execution.windows` | `ADAPT` | Modified | Yes | MIT (OpenClaw Foundation) | High | Medium |
| `apps/android/` | Android node companion, device pairing, telemetry, camera/audio | Android SDK, Java/Kotlin | `apps.android` | `nodes.android` | `RUN AS EXTERNAL SERVICE` | Direct + Adapt | Yes | MIT (OpenClaw Foundation) | Medium | Medium |
| `packages/acp-core/` & `src/acp/` | Anthropic Context Protocol & external agent delegation | `packages/*` | `acp` | `agents.external` | `KEEP + JARVIS WRAPPER` | Direct + Wrap | Yes | MIT (OpenClaw Foundation) | Medium | Low |
| `src/cron/` & `src/automation/` | Cron scheduling, background event bus, task registries | SQLite, Node timers | `cron` | `automation.engine` | `ADAPT` | Modified | Yes | MIT (OpenClaw Foundation) | Medium | Low |
| `src/memory/` & `packages/memory-host-sdk/` | Memory retrieval, vector search, embeddings, SQLite stores | SQLite, embedding models | `memory` | `memory.plane` | `ADAPT` | Modified | Yes | MIT (OpenClaw Foundation) | High | Medium |
| `src/secrets/` | Secret store, credential references, egress masking | OS keychain, files | `secrets` | `security.secrets` | `ADAPT` | Modified | Yes | MIT (OpenClaw Foundation) | Critical | High |
| `extensions/` (Channels: Slack, Discord, Telegram, etc.) | Channel connectors | Vendor APIs | `extensions.channels` | `channels.*` | `OPTIONAL` | Direct | Yes | MIT (OpenClaw Foundation) | Low | Low |
| `ui/` | Control UI web dashboard, dynamic widgets, transcript viewers | React / Solid / Vite | `ui` | `ui.control_center` | `KEEP + JARVIS WRAPPER` | Adapted | Yes | MIT / GitHub Octicons | Medium | Low |

---

## 3. JARVIS Architectural Overrides (Pure JARVIS Authority)

The following components are **JARVIS-owned from first instantiation** and never delegated to upstream unverified logic:

1. **JARVIS Control Plane (`core.control_plane`):**
   - Implemented in Python using LangGraph and stateful task graphs.
   - Canonical Task State Machine (`CREATED` → `NORMALIZING` → `CLASSIFIED` → `CONTEXT_READY` → `PLANNED` → `POLICY_CHECK` → `AUTHORIZED` → `DISPATCHED` → `RUNNING` → `OBSERVING` → `VERIFYING` → `COMPLETED`).
   - Supervised execution, cancellation, recovery, and durable persistence.

2. **JARVIS Policy Engine & Capability Firewall (`policy.engine`):**
   - Authoritative deterministic security gate.
   - Evaluates every action against session, capability, target, risk, and user approvals.
   - Fail-closed semantics: Returns `ALLOW`, `DENY`, `ASK`, `CONDITIONAL`, or `DEFER`.

3. **JARVIS Action Broker (`actions.broker`):**
   - The single trusted execution conduit between cognitive intent and host/node actions.
   - Issues immutable, auditable `ActionRequest` and `ActionResult` instances with unique Action IDs.

4. **JARVIS Model Router & Quota Manager (`cognition.model_router` & `cognition.quota_manager`):**
   - Strictly enforces the six approved model families:
     - `Gemini 3.8 Live`
     - `GPT-OSS 120B`
     - `Qwen 3.8 27B`
     - `Gemini 3.1 Flash-Lite`
     - `Gemini 3.5 Flash-Lite`
     - `Gemma 4 31B`
   - Dynamically tracks RPM, TPM, RPD, reset epochs, latency, and provider health.

5. **JARVIS Gemini 3.8 Live Bridge (`cognition.gemini_live`):**
   - Realtime bidirectional WebSocket bridge handling natural voice, streaming text, vision, and tool calls.
   - Translates Gemini function calls into canonical `ActionRequest` objects sent to the Policy Engine.

6. **JARVIS Windows Node Engine (`execution.windows`):**
   - Native host execution harness combining PowerShell, Win32 APIs, UI Automation, process inspection, filesystem operations, screen capture, and audio/video control.

7. **JARVIS Verification & Recovery Engine (`core.verification` & `core.recovery`):**
   - Closed-loop observation: `PLAN` → `ACT` → `OBSERVE` → `VERIFY`.
   - Independent verification channels (separate verifier models, deterministic OS assertions, DOM inspections).
