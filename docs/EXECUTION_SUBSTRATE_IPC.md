# JARVIS Native Execution Substrate & JSON-RPC IPC

**Status:** Technical Specification & IPC Protocol  
**Runner:** `jarvis/substrate/runner/jarvis_substrate_runner.ts`  
**Bridge Client:** `jarvis.execution.substrate_bridge.SubstrateBridge`  
**Runtime:** Node.js + TypeScript (`tsx`)  

---

## 1. Overview & Architectural Role

The JARVIS Execution Substrate provides an isolated, resilient execution environment for native computer use, coordinate automation, process control, display capture, and tool call repair.

While the primary JARVIS brain runs in Python AsyncIO, the execution substrate runs as a separate Node.js child process communicating over standard input and standard output using strict **JSON-RPC 2.0** wire protocol.

```
┌─────────────────────────────────┐
│     JARVIS Python Control       │
│             Plane               │
│   (SubstrateBridge Singleton)   │
└────────────────┬────────────────┘
                 │
                 │ JSON-RPC 2.0 (stdin / stdout)
                 ▼
┌─────────────────────────────────┐
│    JARVIS Substrate Runner      │
│  (jarvis_substrate_runner.ts)   │
│                                 │
│  ├─ JarvisSubstrateRunner       │
│  ├─ JarvisWindowsDriverSession  │
│  ├─ Coordinate CUA Engine       │
│  └─ Tool Call Repair Parser     │
└─────────────────────────────────┘
```

---

## 2. IPC Wire Protocol (JSON-RPC 2.0)

All communication between Python and the substrate runner is newline-delimited JSON over stdio streams:

### 2.1 Request Schema
```json
{
  "jsonrpc": "2.0",
  "id": "req-uuid-v4",
  "method": "computer.act",
  "params": {
    "action": "left_click",
    "x": 450,
    "y": 320
  }
}
```

### 2.2 Response Schema (Success)
```json
{
  "jsonrpc": "2.0",
  "id": "req-uuid-v4",
  "result": {
    "ok": true,
    "details": {
      "text": "Clicked at (450, 320)"
    }
  }
}
```

### 2.3 Response Schema (Error)
```json
{
  "jsonrpc": "2.0",
  "id": "req-uuid-v4",
  "error": {
    "message": "COMPUTER_UNSUPPORTED_ACTION: invalid_action"
  }
}
```

---

## 3. Supported Substrate Methods

| Method | Parameters | Description |
| :--- | :--- | :--- |
| `health` | `{}` | Liveness probe returning platform details and Node version. |
| `capabilities` | `{}` | Returns supported CUA actions, observations, and delivery modes. |
| `screen.snapshot` | `{"format": "jpeg", "maxWidth": 1280}` | Captures display snapshot, scales image, and returns base64. |
| `computer.act` | `{"action": "...", ...}` | Dispatches coordinate mouse, keyboard, or window management action. |
| `system.run` | `{"command": "..."}` | Executes a shell command with buffered stdout/stderr output. |
| `tool.repair` | `{"text": "..."}` | Parses and extracts malformed or plain-text tool call blocks. |

---

## 4. Supported Computer Actions (`computer.act`)

- **`left_click`, `right_click`, `middle_click`:** Mouse button clicks at $(x, y)$ coordinates.
- **`double_click`, `triple_click`:** Rapid multi-click sequences.
- **`mouse_move`:** Smooth cursor positioning.
- **`left_click_drag`:** Drags from $(fromX, fromY)$ to $(toX, toY)$ with interpolated steps.
- **`scroll`:** Emits vertical mouse wheel ticks (direction up or down).
- **`type`:** Types raw text strings with Unicode support.
- **`key`:** Presses key chords and keyboard shortcuts with modifier normalization (`ctrl`, `alt`, `shift`).
- **`list_windows`:** Enumerates all top-level desktop windows with hwnd, pid, title, and bounding rectangles.
- **`list_apps`:** Lists running executable processes and launch paths.
- **`launch_app`:** Spawns an executable application by name or path.
- **`kill_app`:** Gracefully terminates a process by PID.
- **`bring_to_front`:** Restores and sets foreground focus for a target window handle.

---

## 5. Subprocess Management & Fault Tolerance

The Python `SubstrateBridge` implements robust lifecycle management:
1. **Dynamic Resolution:** Detects the runner path automatically relative to the repository workspace or via `JARVIS_SUBSTRATE_RUNNER_PATH`.
2. **Daemon Auto-Spawn:** Spawns the daemon on first invocation if not already active.
3. **Async Reader Loop:** Processes stdout asynchronously, matching responses back to pending request futures by request ID.
4. **Crash Recovery:** If the Node.js process exits unexpectedly, in-flight futures are rejected cleanly and subsequent requests restart the runner automatically.
