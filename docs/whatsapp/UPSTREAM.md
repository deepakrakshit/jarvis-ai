# Upstream WhatsApp Rust Engine Provenance & Integration Manifest

## 1. Upstream Repositories & Revisions

### Primary Engine: `oxidezap/whatsapp-rust`
- **Repository URL**: `https://github.com/oxidezap/whatsapp-rust.git`
- **Vendored Location**: `vendor/whatsapp-rust`
- **Pinned Commit**: `d9f78b806f1f4ca80c8008caa5846e5d542c2c55` (post-v0.7.0 `main` HEAD)
- **Base Tag**: `v0.7.0` (commit `f8165f282008935732e0b26d6f5cca5038ecd222`)
- **Integration Date**: 2026-10-03
- **License**: MIT
- **Vendored Components**:
  - `whatsapp-rust`: Sans-IO core protocol engine, state machine, and session management.
  - `whatsapp-rust-proto`: Protocol buffers definitions and serialization.
  - `whatsapp-rust-crypto`: Signal protocol, Noise protocol, and HKDF cipher operations.
  - `whatsapp-rust-voip`: VoIP signaling state machine, SRTP encryption, Opus/MLow codec framing, and relay transport.
  - `whatsapp-rust-tokio`: Tokio-based network transport and TLS client.
- **Revision Selection Rationale**: Commit `d9f78b8` incorporates all core protocol fixes and VoIP transport enhancements following `v0.7.0` while maintaining full stability and wire-format compatibility with WhatsApp servers.

### JS/WASM Compatibility Layer: `oxidezap/baileyrs`
- **Repository URL**: `https://github.com/oxidezap/baileyrs.git`
- **Vendored Location**: `vendor/baileyrs`
- **Pinned Commit**: `724cc05e94b2a8d3e91d6c8b93998f480396fb1a`
- **Base Tag**: `v0.3.9`
- **Integration Date**: 2026-10-03
- **License**: MIT
- **Vendored Components**:
  - `useMultiFileAuthState`: Multi-file credential and device-bin storage.
  - `makeWASocket`: High-level socket builder and event bus dispatcher.
  - Proto utilities and message envelope formatting.

### WASM Bridge: `oxidezap/whatsapp-rust-bridge`
- **Repository URL**: `https://github.com/oxidezap/whatsapp-rust-bridge.git`
- **Vendored Location**: `vendor/whatsapp-rust-bridge`
- **Pinned Commit**: `eb910d8a55b11910efc79dd7011d882583856bc7`
- **Base Tag**: `v0.24.1`
- **Integration Date**: 2026-10-03
- **License**: MIT
- **Vendored Components**:
  - `WasmWhatsAppClient`: WebAssembly compiled binding of the core engine.
  - Call operations: `rejectCall`, `sendNode`, `queryNode`.
  - Event listener bridging for incoming call offers, termination, and message upserts.

### Conformance Reference: `WhiskeySockets/wacrg`
- **Repository URL**: `https://github.com/WhiskeySockets/wacrg.git`
- **Location**: `research/wacrg`
- **Pinned Commit**: `34ba677c7f428c0b29ce45e3ea3023e42ea21191`
- **Role**: Conformance and protocol specifications for call stanzas and media keys (reference only; no handwritten replacement code).

---

## 2. Architectural Boundary & Execution Model

The integration strictly adheres to the mandated hierarchy:

```
JARVIS Python Cognitive Kernel / ActionBroker
   │ (CLI / Subprocess IPC with JSON output)
   ▼
JARVIS TypeScript WhatsApp Integration Layer (`src/integrations/whatsapp/`)
   │ (Single-instance lock, auth manager, call state machine, audio bridge)
   ▼
`@oxidezap/baileyrs` & `@oxidezap/whatsapp-rust-bridge` (WASM / JS)
   │ (WebAssembly / Native V8 bindings)
   ▼
`oxidezap/whatsapp-rust` Engine (Compiled Rust core)
   │ (Noise protocol, Signal crypto, binary stanzas)
   ▼
WhatsApp Network Infrastructure
```

- **JARVIS Application Layer**: Remains 100% TypeScript and Python.
- **Protocol & Cryptography Engine**: Remains 100% upstream Rust executed via WebAssembly.
- **Modifications inside vendored upstream code**: None. Upstream code is kept immutable in `vendor/`.
- **Modifications made by JARVIS around upstream code**:
  - Single-instance lock mechanism (`data/whatsapp/rust/lock/whatsapp.lock`) to eliminate dual-socket collisions.
  - Multi-file auth wrapper persisting credentials into `data/whatsapp/rust/auth/`.
  - Phone number and LID resolution preserving exact identities without strip-mangling.
  - Realtime full-duplex PCM audio bridge coupling WhatsApp VoIP signaling with Gemini Live.
  - Structured CLI JSON output for automated integration with the Python ActionBroker.
