# JARVIS WhatsApp Subsystem Engine Documentation

## 1. Architectural Overview & Boundaries

The JARVIS WhatsApp subsystem is powered by upstream `oxidezap/whatsapp-rust` executed via its official WebAssembly/JavaScript integration layer (`@oxidezap/baileyrs` and `@oxidezap/whatsapp-rust-bridge`).

The architecture strictly follows this runtime hierarchy:

```
+-------------------------------------------------------------+
|              JARVIS Python Cognitive Control Plane          |
|    - ActionBroker, Firewall Policy, CLI Subcommands        |
|    - Phone & LID Contact Resolution                         |
+------------------------------+------------------------------+
                               | Subprocess IPC / JSON
                               v
+-------------------------------------------------------------+
|         JARVIS TypeScript Integration & Audio Bridge        |
|    - Process-level Single-Instance Lock                     |
|    - Call State Machine & Bi-directional Audio Streaming    |
|    - Full-Duplex PCM Bridge to Gemini Live                  |
+------------------------------+------------------------------+
                               | WASM / JS Binding
                               v
+-------------------------------------------------------------+
|            oxidezap/whatsapp-rust (WebAssembly)             |
|    - WhatsApp Wire Protocol, Binary Node Framing            |
|    - Signal Protocol, Noise Handshake, HKDF Keys            |
|    - SRTP Packet Encryption & Opus/MLow Audio Framing       |
+-------------------------------------------------------------+
```

---

## 2. Invariants & Guarantees

### Single-Instance Session Lock Protocol
WhatsApp bans or resets credentials if concurrent sessions attempt to authenticate or read from the same state database. To prevent this:
- **Lock File**: `data/whatsapp/rust/lock/whatsapp.lock`
- **Mechanism**: The TypeScript process acquires an exclusive file lock on startup.
- **Fail-Fast**: If another process owns the lock, newly spawned short-lived commands defer or safely inspect read-only database mirrors without colliding.
- **Cleanup**: Process exit hooks (`SIGINT`, `SIGTERM`, `exit`) unconditionally release the lock.

### Authentication Persistence
- **Auth Directory**: `data/whatsapp/rust/auth/`
- **Format**: Durable binary and JSON credentials managed by upstream WASM auth state (`device-device.bin`, `app-state-sync-key`, etc.).
- **QR Pairing**: CLI command `jarvis whatsapp login` renders interactive QR codes directly in terminal or opens the browser for one-time pairing.
- **Auto-Reconnect**: Exponential backoff with random jitter recovers from transient socket drops.

---

## 3. VoIP Call Lifecycle & State Transitions

The VoIP call manager operates an explicit state machine:

```
[IDLE] ───► [RINGING] ───► [ANSWERING] ───► [CONNECTING] ───► [CONNECTED]
  ▲                                                                 │
  │                                                                 ▼
[ENDED] ◄────────────── [FAILED] ◄─────────────────────────── [DEGRADED]
```

- **IDLE**: No active call session.
- **RINGING**: Incoming offer received or outbound call stanza dispatched.
- **ANSWERING**: Inbound offer accepted; relay tokens negotiated.
- **CONNECTING**: SRTP media streams establishing via WhatsApp relay servers.
- **CONNECTED**: Full-duplex 16kHz PCM audio streaming active between caller and Gemini Live.
- **DEGRADED**: Transient packet loss or jitter detected; audio continues with concealment.
- **ENDING / ENDED**: Termination stanzas exchanged, session teardown, transcript persisted.

---

## 4. Full-Duplex Audio & Gemini Live Integration

- **Sample Rate**: 16,000 Hz, 16-bit Mono Linear PCM.
- **Chunk Size**: 20ms frames (640 bytes per chunk).
- **Latency Target**: Sub-250ms round-trip audio latency.
- **Barge-in / Interruption**: When inbound speech is detected from the user during assistant speech, the outbound buffer is immediately flushed to ensure responsive, natural conversation.
- **Diagnostics**: `jarvis whatsapp voip-test` executes local synthetic PCM loopback measuring jitter, packet loss, and latency without making an external phone call.

---

## 5. Contact Resolution & Identity Safety

- **Phone Number Normalization**: Automatically extracts international country codes (defaults to `+91` or configured setting).
- **LID / PN Handling**: Preserves `@lid` (Linked Identity) identifiers without strip-mangling, maintaining proper reverse lookup to phone numbers via `whatsapp.db`.
- **Disambiguation**: If an ambiguous contact name is supplied, resolution returns structured candidates rather than guessing.

---

## 6. CLI Command Matrix

The JARVIS CLI provides 22 subcommands under `jarvis whatsapp`:

| Command | Purpose | Risk Tier |
|---|---|---|
| `call` | Autonomous outbound VoIP voice call | `MEDIUM` |
| `status` | Subsystem availability and store metrics | `READ_ONLY` |
| `history` | View completed call transcripts and records | `READ_ONLY` |
| `login` | Authenticate WhatsApp via QR linking | `LOW` |
| `logout` | Purge stored session credentials | `MEDIUM` |
| `sync` | Mirror chats, contacts, and messages to SQLite | `LOW` |
| `contacts` | Search and manage contact aliases/tags | `READ_ONLY` |
| `chats` | List recent conversations | `READ_ONLY` |
| `messages` | View and retrieve chat messages | `READ_ONLY` |
| `unread` | Review unread messages and generate reply drafts | `READ_ONLY` |
| `send` | Send text, reaction, or voice note | `MEDIUM` |
| `send-file` | Dispatch document or media file | `MEDIUM` |
| `answer` | Answer incoming voice call by ID | `MEDIUM` |
| `reject` | Reject incoming voice call | `LOW` |
| `hangup` | Terminate active voice call | `LOW` |
| `call-status` | Inspect live call session metrics | `READ_ONLY` |
| `calls` | Combined view of active and recent calls | `READ_ONLY` |
| `voip-test` | Execute synthetic audio loopback diagnostics | `READ_ONLY` |
| `doctor` | Comprehensive subsystem diagnostic report | `READ_ONLY` |
| `resolve` | Resolve contact name, phone, alias, or JID | `READ_ONLY` |
| `check` | Verify phone number registration on WhatsApp | `READ_ONLY` |
| `search` | Full-text search across message history | `READ_ONLY` |
