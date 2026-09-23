# JARVIS Gateway Protocol & Cognitive Control Plane

**Status:** Technical Architecture & Protocol Specification  
**Daemon:** `jarvis.gateway.server.GatewayServer`  
**Control Plane:** `jarvis.control_plane.control_plane.ControlPlane`  
**Protocol:** Typed WebSocket Protocol (`ws://127.0.0.1:8765`)  

---

## 1. System Role & Responsibilities

The JARVIS Gateway is the central ingress and egress point for external clients, user interfaces, mobile nodes, and background daemons.

It operates independently of any active LLM stream:
1. Maintains long-lived client connections.
2. Manages persistent user sessions and conversation histories.
3. Authenticates connecting nodes via shared tokens or local loopback authorization.
4. Dispatches tool requests to the local Action Broker and specialist subagents.
5. Runs periodic heartbeat cron schedules for proactive background tasks.

---

## 2. Gateway Protocol & Message Types

All messages exchanged over the WebSocket transport adhere to typed JSON envelopes:

### 2.1 Handshake & Authentication (`connect`)
```json
{
  "type": "connect",
  "client_id": "jarvis-desktop-ui",
  "version": "1.0.0",
  "token": "optional-auth-token"
}
```

The gateway validates credentials and responds with:
```json
{
  "type": "connected",
  "session_id": "sess-8f3a2c-491b",
  "capabilities": ["voice", "vision", "automation", "subagents"]
}
```

### 2.2 Client Invocations (`request`)
Clients send structured requests for actions, chat turns, or status queries:
```json
{
  "type": "request",
  "id": "req-1029",
  "method": "action.execute",
  "params": {
    "action": "open_application",
    "target": "Notepad"
  }
}
```

### 2.3 Real-Time Event Streams (`event`)
The gateway pushes asynchronous telemetry, speech transcriptions, and notification events to clients:
```json
{
  "type": "event",
  "event": "transcription.partial",
  "data": {
    "speaker": "user",
    "text": "open the browser"
  }
}
```

---

## 3. Session State Machine & Memory Persistence

Conversations are tracked as persistent session models in SQLite/JSON:
- **Active Turn Buffer:** Tracks in-flight user speech, model proposals, and tool outputs.
- **Context Compaction:** When conversation history approaches model context limits, a compaction summarizer distills older turns into high-density memory entries.
- **Standing Intents:** Persistent triggers (e.g. "Alert me when CPU usage exceeds 90%") evaluated on background cron cycles.

---

## 4. Multi-Agent Dispatch & Specialist Workers

When a user request requires deep reasoning or long-running execution, the Control Plane spawns specialized subagent workflows:
- **Research Agent:** Performs web searches, documentation lookups, and data synthesis.
- **Coding Agent:** Runs code analysis, test generation, and diff application in a sandboxed directory.
- **Verification Agent:** Evaluates whether requested tasks completed successfully before reporting back to the user.
