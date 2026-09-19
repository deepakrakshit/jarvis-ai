# JARVIS Execution Substrate: OpenClaw Source Integration Audit

## 1. Architectural Mandate & Principle

JARVIS operates with OpenClaw as an actual implementation substrate. 
Where OpenClaw contains mature implementations for runtime execution, node hosting, application control, and protocol schemas, JARVIS reuses the actual OpenClaw source code directly within `jarvis/substrate/`, rather than reimplementing equivalent logic in Python.

### Architectural Taxonomy
* **Category A (Reused OpenClaw Implementation):**
  Mature execution logic, driver clients, protocol schemas, and contracts originating from OpenClaw, preserved as TypeScript/Node modules and executed directly by the Node runtime.
* **Category B (JARVIS-Native Orchestration):**
  High-level cognitive and governance components (Control Plane, Gemini 3.8 Live dialogue, Policy Engine & Capability Firewall, Action Broker, Model Router, Telemetry, and Conversation State).
* **Category C (Genuinely New / Platform Specific):**
  Capabilities absent in OpenClaw that are purpose-built for JARVIS (e.g. Gemini 3.8 Live bidirectional audio stream with hardware acoustic echo cancellation, Windows COM UI Automation bridge).

---

## 2. Comprehensive Source Integration Audit Table

| JARVIS Subsystem / Component | Existing OpenClaw Implementation | Actual OpenClaw Source Files | Category | Integration Status & Action |
| :--- | :--- | :--- | :---: | :--- |
| **Computer-Use Action Contract & Enums** | Canonical TypeBox action definitions, schemas, and error codes (`COMPUTER_USE_V2_ACTION_NAMES`) | `substrate/plugins/computer-use-contract.ts`<br>`substrate/plugin-sdk/computer-use.ts` | **A** | **Direct Source Reuse**: Preserved in `substrate/plugins/` and validated by Node runner. |
| **Targeted Window Actions & Routing** | Window and element target routing, `requireWindowTarget`, parameter decoders | `substrate/extensions/cua-computer/src/window-actions.ts`<br>`substrate/extensions/cua-computer/src/action-targets.ts` | **A** | **Direct Source Reuse**: Preserved and executed via Node substrate runner. Supersedes parallel Python reimplementation. |
| **Modifier Normalization & Key Chords** | Keyboard normalization, platform modifier mapping, chord parsing | `substrate/extensions/cua-computer/src/actions.ts` | **A** | **Direct Source Reuse**: Preserved and executed via Node substrate runner. Supersedes parallel Python reimplementation. |
| **Observation & Driver Result Projections** | Action envelopes, window observation trees, process projection, app listing | `substrate/extensions/cua-computer/src/driver-result.ts`<br>`substrate/extensions/cua-computer/src/frame.ts` | **A** | **Direct Source Reuse**: Preserved and executed via Node substrate runner. Supersedes parallel Python reimplementation. |
| **Execution Resources & Lifecycles** | Reference generation, window handle leases, resource disposal | `substrate/extensions/cua-computer/src/execution-resources.ts`<br>`substrate/extensions/cua-computer/src/execution-state.ts` | **A** | **Direct Source Reuse**: Preserved and executed via Node substrate runner. Supersedes parallel Python reimplementation. |
| **Computer Driver Client & MCP Proxy** | Direct SDK sessions and MCP transport fallbacks | `substrate/extensions/cua-computer/src/driver-client.ts`<br>`substrate/extensions/cua-computer/src/mcp-driver-client.ts` | **A** | **Direct Source Reuse**: Preserved in substrate; instantiated by substrate runner. |
| **Node Worker Computer Protocol** | Typed worker carrier protocol for `capabilities`, `snapshot`, `act`, and `close` | `substrate/worker/node-computer-protocol.ts`<br>`substrate/node-host/computer-command.ts` | **A** | **Direct Source Reuse**: Protocol definitions reused for the Node-Python IPC bridge. |
| **Execution Policy & Filesystem Drift** | System run policy checks, mutating tool isolation, safe command wrappers | `substrate/node-host/exec-policy.ts`<br>`substrate/security/exec-filesystem-policy.ts`<br>`substrate/node-host/invoke-system-run.ts` | **A** | **Direct Source Reuse**: Ingested into `substrate/`; referenced by security boundary. |
| **Browser Execution Subsystem** | Browser session management, page interactions, CDP integration | `substrate/extensions/browser/` | **A** | **Direct Source Reuse**: Preserved under `substrate/extensions/browser/`. |
| **Control Plane & Cognitive Loop** | Realtime agent turn orchestrator, model handoffs, memory integration | `src/jarvis/core/control_plane.py`<br>`src/jarvis/core/live_dialogue.py` | **B** | **Keep (JARVIS Native)**: Coordinates cognitive flow and routes actions. |
| **Policy Engine & Capability Firewall** | Autonomous action authorization, risk evaluation, user approvals | `src/jarvis/policy/engine.py`<br>`src/jarvis/policy/firewall.py` | **B** | **Keep (JARVIS Native)**: Acts as the primary security gate prior to substrate invocation. |
| **Action Broker & Dispatcher** | Dynamic capability routing, execution dispatching, lifecycle management | `src/jarvis/actions/broker.py`<br>`src/jarvis/actions/registry.py` | **B** | **Keep (JARVIS Native)**: Connects high-level intents to substrate bridge. |
| **Substrate IPC Execution Bridge** | Bidirectional IPC process manager executing Node substrate runner | `src/jarvis/execution/substrate_bridge.py` | **B** | **New Bridge Component**: Spawns Node runner to execute Category A modules. |
| **Gemini 3.8 Live Realtime Pipeline** | Low-latency voice/audio capture with hardware acoustic echo cancellation | `src/jarvis/core/live_dialogue.py`<br>`src/jarvis/hardware/audio_aec.py` | **C** | **Keep (JARVIS Native)**: DeepMind Gemini Live multimodal socket connection. |
| **Windows UI Automation COM Bridge** | Direct `IUIAutomation` COM engine for Win32/WPF/XAML application accessibility trees | `src/jarvis/execution/windows/uia.py`<br>`src/jarvis/execution/windows/window_manager.py` | **C** | **Keep (Platform Native)**: Windows native accessibility provider plugged into substrate dispatch. |

---

## 3. Substrate Bridge Architecture

```
┌─────────────────────────────────────────────────────────┐
│              JARVIS Python Control Plane                │
│  - Gemini 3.8 Live Multimodal Dialogue                  │
│  - Policy Engine & Capability Firewall (Security Gate)  │
│  - Action Broker (Capability Router)                    │
└────────────────────────────┬────────────────────────────┘
                             │
                             │ stdin / stdout JSON-RPC IPC
                             ▼
┌─────────────────────────────────────────────────────────┐
│       JARVIS OpenClaw Substrate Runner (Node.js)        │
│  - runner/openclaw_substrate_runner.mjs                 │
├─────────────────────────────────────────────────────────┤
│  Category A Reused OpenClaw Source Code:                │
│  - plugins/computer-use-contract.ts                     │
│  - extensions/cua-computer/src/window-actions.ts        │
│  - extensions/cua-computer/src/driver-result.ts         │
│  - extensions/cua-computer/src/actions.ts               │
│  - extensions/cua-computer/src/execution-resources.ts   │
│  - extensions/cua-computer/src/commands.ts              │
│  - extensions/cua-computer/src/driver-client.ts         │
│  - extensions/browser/                                  │
│  - node-host/ & worker/                                 │
└─────────────────────────────────────────────────────────┘
```

### Communication Protocol
1. **Invocation**: The JARVIS `ActionBroker` delegates to `SubstrateBridge.execute_action(action, params)`.
2. **Payload Serialization**: The bridge packages the action conforming to OpenClaw's `NodeWorkerComputerInputSchema`:
   ```json
   {
     "jsonrpc": "2.0",
     "id": "req-001",
     "method": "computer.act",
     "params": {
       "action": "list_windows"
     }
   }
   ```
3. **Execution**: The substrate runner loads OpenClaw modules directly, dispatches to the corresponding handler (`handleWindowAct`, `commands`, etc.), and returns the exact OpenClaw `actionEnvelope` or projected observation.
4. **Resolution**: Results are returned asynchronously to Python without loss of type fidelity or structured metadata.
