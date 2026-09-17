# JARVIS v1.0.0: Model Roster & Quota-Aware Gateway Specification

## Operational Model Inventory, Capability Routing & Quota Allocation Matrix

**Classification:** Engineering Design Document (Model Plane Baseline)
**Status:** Proposed successor to the previous canonical roster
**Target System:** JARVIS v1.0.0 Free-Tier Distributed Inference Reservoir + Realtime Voice Plane
**Research Cutoff:** 16 September 2026
**Primary change:** Addition of Gemini 3.8 Live and Gemini 3.8 Live Extended Thinking as first-class realtime voice models.

> **Naming correction:** Google’s official API model identifier is `gemini-3.8-live`, not “Gemini 3.8 Flash Live”. The corresponding high-reasoning model is `gemini-3.8-live-extended-thinking`.

---

# 0. Executive Decision

JARVIS should **not** collapse the entire model roster into Gemini 3.8 Live.

Instead, JARVIS should evolve from a single generalized model reservoir into two coordinated model planes:

```text
                         JARVIS MODEL PLANE
                                │
              ┌─────────────────┴─────────────────┐
              │                                   │
       REALTIME VOICE PLANE                  WORK / REASONING PLANE
              │                                   │
   ┌──────────┴──────────┐               ┌─────────┴─────────────┐
   │                     │               │                       │
3.8 Live           3.8 Live ET      3.8 Flash              Specialist Pool
Fast voice         Deep voice       Structured/deep        Qwen / GPT-OSS
conversation       reasoning        agent work             / Gemma
   │                     │               │
   └──────────┬──────────┘               │
              │                           │
              └──────────────┬────────────┘
                             ↓
                    JARVIS MODEL GATEWAY
                             ↓
               Policy / Capability / Quota
                             ↓
                  JARVIS CONTROL PLANE
```

The key design rule is:

> **Gemini 3.8 Live is a realtime conversational model, not the JARVIS authority layer.**

It can receive audio, produce spoken audio, reason, and make function calls. Those function calls enter JARVIS as untrusted proposed intent and are still governed by the existing deterministic Policy Engine, Capability Firewall, Action Broker, verification, and audit mechanisms.

---

# 1. Research Scope & Evidence Quality

This roster is based on:

1. The complete previously supplied JARVIS model-roster document, including its model inventory, quotas, gateway contract, quota-reservation implementation, failure semantics, scoring function, and operational role assignments.
2. Current Google AI for Developers documentation for Gemini 3.8 Flash, Gemini 3.8 Live, Gemini 3.8 Live Extended Thinking, Live API tools, Live API session management, Live API capabilities, pricing, models, deprecations, and embeddings.
3. Google DeepMind model-card material for Gemini 3.8 Audio and Gemma 4.
4. Current Groq documentation for Qwen 3.8 27B, GPT-OSS 120B, built-in tools, rate-limit headers, and model capabilities.
5. Independent benchmark/reporting sources used only for supplementary cross-checking of voice-agent benchmark results.

### Evidence hierarchy

When sources disagree:

```text
JARVIS runtime observation / provider response headers
        ↓
Provider API documentation
        ↓
Provider model cards / release notes
        ↓
Independent benchmark sources
        ↓
Third-party articles / community reports
```

Provider dashboards and response headers remain authoritative for the actual runtime project state.

---

# 2. Critical Findings From the Previous Roster

The previous roster correctly established several principles that remain unchanged:

- quotas are non-fungible;
- model selection is capability-aware;
- free-tier quotas must be treated as runtime configuration rather than permanent guarantees;
- model selection and autonomy are separate concerns;
- model outputs never form the authorization boundary;
- quota state requires provider observation + local reservations + atomic admission;
- ambiguous post-dispatch failures must become orphaned leases rather than being silently released;
- fallback must be capability-aware rather than blind round-robin;
- raw RPD is not equivalent to useful task capacity.

These principles remain canonical for the gateway design.

The previous roster also established the current work-plane pools:

```text
Pool A   Gemini Flash Quality
Pool B   Gemini Flash-Lite
Pool C   Gemma 4 31B compression
Pool D   Groq specialists
Memory   Gemini Embedding 2
Search   Google Search grounding
```

Those are not removed by introducing Live.

---

# 3. New Architectural Concept: Realtime Voice Plane

## 3.1 Purpose

Gemini 3.8 Live and Gemini 3.8 Live Extended Thinking introduce a dedicated realtime conversational interface.

They should be treated as:

```text
ears + conversational brain + mouth + realtime tool caller
```

not:

```text
JARVIS security authority
```

The Live API provides bidirectional WebSocket streaming, native audio generation, multimodal inputs, and function calling.

---

# 4. Complete Updated Model Inventory

## 4.1 Realtime Voice Pool — New

### `gemini-3.8-live`

**Official name:** Gemini 3.8 Live
**Pool:** `POOL_E_REALTIME_VOICE`
**Provider:** Google
**Status:** Stable / GA
**Release:** 15 September 2026
**Primary role:** Realtime conversational voice agent

### API capabilities

- Text input
- Image input
- Video input
- Audio input
- Native audio output
- Function calling
- Asynchronous function calling
- Search grounding
- Interleaved reasoning
- Live API
- Proactive audio
- Client content updates
- Session resumption
- Context window compression
- Input/output transcription support

### Explicitly unavailable

- Code execution
- File Search
- URL Context
- Structured Outputs
- Image generation
- Google Maps grounding
- Batch API

### Runtime limits

Google’s model page currently reports:

```text
Input context:   131,072 tokens
Output limit:     65,536 tokens
```

Google DeepMind’s model card describes the audio models as having up to approximately 128K token context; the API model page should be treated as the runtime contract.

### Tool semantics

Function calling is supported.

For Gemini 3.8 Live:

- asynchronous / `NON_BLOCKING` execution is the default;
- synchronous `BLOCKING` execution is still supported for backwards compatibility;
- function-result scheduling supports `INTERRUPT`, `WHEN_IDLE`, and `SILENT`.

JARVIS should prefer `NON_BLOCKING` for long-running read/research work.

### JARVIS role

Use Gemini 3.8 Live for:

```text
wake / conversational interaction
voice questions
fast spoken answers
voice-controlled JARVIS functions
lightweight multimodal interaction
voice navigation
interactive tool requests
hands-free conversational control
```

---

## 4.2 Realtime Voice Deep-Reasoning Pool — New

### `gemini-3.8-live-extended-thinking`

**Official name:** Gemini 3.8 Live Extended Thinking
**Pool:** `POOL_E_REALTIME_VOICE`
**Provider:** Google
**Status:** Stable / GA
**Release:** 15 September 2026
**Primary role:** High-reasoning realtime voice agent

### Capabilities

- Text input
- Image input
- Video input
- Audio input
- Native audio output
- Background reasoning
- Thinking levels: low / medium / high
- Asynchronous function calling
- Search grounding
- Proactive audio
- Session resumption
- Context compression
- Continuous streamed audio

### Important restrictions

- Function calling is async-only.
- `behavior: NON_BLOCKING` is required.
- Blocking function calls are not supported.
- Function scheduling modes are not supported.
- Structured output is not supported.
- Code execution is not supported.
- File Search is not supported.
- URL context is not supported.

### Interaction lifecycle

The client must not assume:

```text
turnComplete == system idle
```

For Extended Thinking, JARVIS must observe `interaction_status`:

```text
IN_PROGRESS
    =
background reasoning and/or async tool calls may still be active

IDLE
    =
model is finished with the interaction
```

### JARVIS role

Use this model for:

```text
complex voice requests
multi-step reasoning
voice + research
voice + coding orchestration
voice + external tools
multi-phase planning
tasks where the user wants continuous spoken progress
```

Example:

```text
USER:
"JARVIS, investigate why my project build is failing."

Gemini 3.8 Live Extended Thinking:
"Sure, I'll inspect the build and trace the failure."

        ↓ async function call

JARVIS CONTROL PLANE
        ↓
Coding Specialist
        ↓
Model Gateway
        ↓
local / remote work-plane model
        ↓
tool execution
        ↓
verification
        ↓
FunctionResponse

Gemini:
"I found the issue..."
```

---

# 5. Realtime Voice Quota Policy

## 5.1 Current Project Dashboard Observation

The current Google AI Studio project dashboard supplied with this roster shows:

```text
Gemini 3.8 Live
    RPM: Unlimited
    TPM: 65K
    RPD: Unlimited

Gemini 3.8 Live Extended Thinking
    RPM: Unlimited
    TPM: 65K
    RPD: Unlimited
```

These are **project-environment observations**, not permanent provider guarantees.

### Critical interpretation

Do NOT calculate:

```text
"Unlimited RPM + Unlimited RPD = infinite capacity"
```

Instead:

```text
Unlimited dashboard dimension
+
finite TPM / token consumption
+
session constraints
+
connection lifecycle
+
provider load / service controls
=
actual capacity
```

The Live models therefore receive a new quota domain rather than being added into the old finite `18,980 operations/day` arithmetic.

---

# 6. New Quota Domain

The previous quota enum was:

```python
class QuotaDomain(str, Enum):
    GENERATION = "GENERATION"
    EMBEDDING = "EMBEDDING"
    SEARCH_GROUNDING = "SEARCH_GROUNDING"
```

The updated gateway should add:

```python
class QuotaDomain(str, Enum):
    GENERATION = "GENERATION"
    LIVE_AUDIO = "LIVE_AUDIO"
    EMBEDDING = "EMBEDDING"
    SEARCH_GROUNDING = "SEARCH_GROUNDING"
```

## 6.1 Live quota dimensions

The Live quota manager should track, where available:

```text
rpm_limit
rpd_limit
tpm_limit
tpd_limit

provider_remaining_requests
provider_remaining_tokens

active_live_sessions
active_session_seconds
audio_input_tokens
audio_output_tokens

connection_reconnects
session_resumptions

context_compression_events
```

For the current project:

```text
RPM = provider dashboard reports Unlimited
RPD = provider dashboard reports Unlimited
TPM = 65K observed
```

---

# 7. Why Live Sessions Are Different From Normal Generation Calls

The previous gateway was request-oriented:

```text
reserve
→ dispatch
→ reconcile
```

Live requires an additional session-oriented layer:

```text
session admission
        ↓
WebSocket connection
        ↓
audio streaming
        ↓
function calls
        ↓
tool execution
        ↓
function responses
        ↓
continued audio
        ↓
session resumption
        ↓
session termination
```

The gateway therefore needs **two related ledgers**:

```text
REQUEST / INFERENCE LEASE
+
LIVE SESSION LEASE
```

A Live session is not equivalent to one ordinary generation request.

---

# 8. Continuous Conversation: What “Unlimited” Actually Means

Google documents several independent session constraints.

Without context compression:

```text
audio-only session ≈ 15 minutes
audio + video session ≈ 2 minutes
```

The WebSocket connection itself can terminate after roughly 10 minutes.

Google recommends:

1. Context window compression.
2. Session resumption.
3. Handling server `GoAway`.
4. Maintaining the latest resumption token.

With context window compression, the effective conversation can be extended for an unlimited duration.

Therefore JARVIS should implement:

```text
LIVE SESSION
      │
      ├── connection A
      │       ↓
      │   GoAway / close
      │
      ├── session resumption
      │       ↓
      │   connection B
      │
      ├── context compression
      │       ↓
      │   compacted conversation state
      │
      └── continued conversation
```

The user should experience this as one continuous JARVIS conversation even though the underlying WebSocket connection may be repeatedly renewed.

---

# 9. Live Audio Engineering Contract

Google recommends sending microphone audio in small chunks, approximately:

```text
20ms – 100ms
```

Microphone audio should generally be resampled to:

```text
16 kHz input
```

Google's Live examples use:

```text
24 kHz output audio
```

JARVIS should therefore normalize audio at the edge instead of allowing arbitrary device sample rates to leak into the Live adapter.

---

# 10. Proactive Audio and Cost/Resource Behavior

Gemini 3.8 Live and Gemini 3.8 Live Extended Thinking have proactive audio permanently enabled.

This has an architectural consequence:

```text
microphone is continuously listened to
        ↓
input audio tokens can accumulate continuously
```

Therefore:

**Always-listening is a resource policy decision.**

JARVIS should support modes such as:

```text
PUSH_TO_TALK
WAKE_WORD
ACTIVE_CONVERSATION
ALWAYS_LISTEN
```

The last mode should not be treated as the default purely because the model supports it.

---

# 11. Transcription Strategy

Gemini Live can provide input/output transcriptions.

This enables JARVIS to persist:

```text
audio
+
user transcript
+
model transcript
+
function calls
+
task IDs
+
verification records
```

However, transcription may create additional text-token billing on paid usage.

The JARVIS voice adapter should therefore make transcription configurable:

```text
DISPLAY_ONLY
DISPLAY_AND_SESSION_LOG
FULL_AUDIT
DISABLED
```

Privacy and retention remain governed by JARVIS policy.

---

# 12. Function Calling Is the Bridge Between Live and the JARVIS Core

The critical architecture is:

```text
                 GEMINI 3.8 LIVE
                        │
                        │ function call
                        ↓
              JARVIS LIVE ADAPTER
                        │
                        ↓
               TRUST CLASSIFICATION
                        │
                        ↓
               CAPABILITY FIREWALL
                        │
                        ↓
                 POLICY ENGINE
                        │
              ┌─────────┴─────────┐
              │                   │
          Read path          Effect path
              │                   │
              ↓                   ↓
       Model / Tool         HITL / Revalidate
                                  ↓
                           Commit-Time Auth
                                  ↓
                            Action Broker
                                  ↓
                              Effect
                                  ↓
                            Verification
                                  ↓
                          FunctionResponse
                                  ↓
                         GEMINI LIVE
```

Gemini's function call is therefore analogous to any other model proposal.

It is **not an authorization token**.

---

# 13. Live Capability Exposure Policy — Canonical Projection, Not Raw Registry Dump

The original proposal to expose only seven high-level functions is now superseded by the
current implementation audit and by the capability model already present in the frozen
architecture.

The correct rule is:

> **Expose every user-legitimate capability that the current task policy permits to Gemini Live,
> but never expose raw internal governance primitives or grant the model execution authority.**

This is a policy projection, not a blind export of the entire registry.

## 13.1 Single source of truth

The canonical capability registry remains the source of truth:

```text
Canonical Capability Registry
        ↓
Capability Manifest
        ↓
Task/Session Policy Projection
        ↓
LIVE-EXPOSABLE capability filter
        ↓
Provider schema translation
        ↓
Gemini Live function declarations
```

The Live plane MUST NOT maintain a second independent capability registry.

For each registry capability, the projection evaluates:

```text
capability_id
name
description
input_schema
output_schema
risk_class
side_effect_class
required_scopes
allowed_autonomy_levels
approval_requirement
verification_requirement
sandbox_requirement
network_requirement
supports_async
idempotency_semantics
live_exposure_policy
```

## 13.2 Live-exposure classes

Each capability should resolve to one of:

```text
LIVE_ALLOWED
LIVE_ALLOWED_WITH_APPROVAL
LIVE_ALLOWED_ONLY_IN_SANDBOX
LIVE_NOT_EXPOSED
INTERNAL_CONTROL_ONLY
```

Examples of likely user-facing capabilities:

```text
native:clock:get_time
native:fs:read_file
native:fs:list_dir
native:fs:write_file
native:shell:execute
native:web:search
native:web:fetch
native:calc:evaluate
native:system:get_stats
```

Whether a particular mutation is actually executable is decided by policy at invocation time.

Internal control capabilities remain hidden:

```text
quota-state mutation
lease mutation
policy-version mutation
authorization-token minting
Action Broker internal dispatch
secret-vault administration
provider credential management
raw registry mutation
```

## 13.3 Provider-facing names vs canonical capability IDs

Gemini Live can receive stable provider-safe aliases:

```text
Gemini Live function name
        ↕
Live Capability Projection
        ↕
canonical capability_id
```

Example:

```yaml
name: jarvis_write_file
capability_id: native:fs:write_file
```

The alias is not an authorization record.

## 13.4 Schema translation

Do not manually maintain Google-specific copies of every capability schema.

Generate provider-facing declarations from canonical capability schemas and translate at the
adapter boundary:

```text
canonical JSON Schema
        ↓
Live schema adapter
        ↓
Google GenAI function declaration
```

Translation is deterministic and must have contract tests.

## 13.5 Tool-selection rules

Gemini Live should use a capability when the user request requires:

```text
current / external / authoritative state
filesystem state
system state
network state
deterministic computation
any mutation
any operation explicitly requiring a JARVIS capability
```

Examples:

```text
"What time is it?"
→ native:clock:get_time

"List the files here."
→ native:fs:list_dir

"Read requirements.txt."
→ native:fs:read_file

"Create safe.txt with this content."
→ native:fs:write_file

"Run a speed test using the sandbox."
→ native:shell:execute, subject to sandbox and policy

"Search the web for the latest..."
→ native:web:search

"Calculate..."
→ native:calc:evaluate
```

General conceptual conversation can be answered directly without a tool.

## 13.6 Security boundary

The Live model receives capability visibility, not execution authority.

Every function call remains an untrusted model proposal:

```text
Gemini Live
    ↓
Live Capability Projection
    ↓
Trust Classification
    ↓
Capability Firewall
    ↓
Policy Engine
    ├── DENY
    ├── ALLOW read path
    └── REQUIRE_HITL / hardened write path
                  ↓
          Post-Approval Revalidation
                  ↓
          Commit-Time Authorization
                  ↓
             Action Broker
                  ↓
            Execution Fabric
                  ↓
             Verification
                  ↓
            FunctionResponse
                  ↓
             Gemini Live
```

## 13.7 Current implementation status

The current implementation is NOT yet conformant because the audit found:

```text
LiveToolBridge
    ├── hard-coded tool list
    ├── local synthetic manifests
    ├── inline ad-hoc execution
    ├── Action Broker bypass
    └── REQUIRE_HITL fall-through
```

These are pre-commit defects that must be closed.

---

# 14. Why Gemini 3.8 Live Does Not Replace Gemini 3.8 Flash

This distinction is fundamental.

## Gemini 3.8 Live

Designed for:

```text
realtime audio
voice conversation
live multimodal interaction
async tool use
streaming dialogue
```

But the current API does **not** provide:

```text
Structured Outputs
Code Execution
File Search
URL Context
Computer Use
```

## Gemini 3.8 Flash

Designed for:

```text
long-horizon software engineering
autonomous agents
structured output
code execution
computer use
file search
URL context
Search
function calling
1M context
64K output
```

Therefore:

```text
Live ≠ Flash
```

The two models are complementary.

---

# 15. Updated Role Matrix

| Workload | Primary | Secondary | Reason |
|---|---|---|---|
| Realtime voice conversation | Gemini 3.8 Live | 3.8 Live ET | Lowest-latency conversational plane |
| Difficult realtime voice task | Gemini 3.8 Live ET | 3.8 Live | Background reasoning + async tools |
| Voice-controlled JARVIS | Gemini 3.8 Live | 3.8 Live ET | Function calling into JARVIS gateway |
| Voice + deep research | 3.8 Live ET | 3.8 Live | Async function calling |
| Text general reasoning | Gemini 3.8 Flash | 3.7 Flash | Structured non-realtime work |
| Deep planning / SWE | Gemini 3.8 Flash | 3.7 Flash | Structured output + long context + code capabilities |
| Agentic coding | Gemini 3.8 Flash | Qwen 3.8-27B | Tooling + structured work |
| Fast multimodal coding | Qwen 3.8-27B | 3.8 Flash | Very low latency + vision + JSON |
| Heavy browser-search reasoning | GPT-OSS 120B | Gemini Search path | Groq browser search |
| High-volume compression | Gemma 4 31B IT | 3.5 Flash-Lite | Very high RPD allocation |
| Lightweight routing | Gemini 3.1 Flash-Lite | 3.5 Flash-Lite | High-volume classifier |
| Structured high-volume work | Gemini 3.5 Flash-Lite | 3.1 Flash-Lite | Structured-output capable |
| Multimodal memory | Gemini Embedding 2 | — | Unified multimodal embedding |
| Google Search grounding | Gemini Search path | GPT-OSS browser search | Separate grounding quota domain |

---

# 16. Revised Default Routing Strategy

The Model Gateway should become context-aware rather than model-name-first.

## Route A — User is speaking

```text
VOICE INPUT
   ↓
Gemini 3.8 Live
```

Use Live as the default voice conversational model.

## Route B — User speaks and request is clearly complex

```text
VOICE INPUT
   ↓
Gemini 3.8 Live Extended Thinking
```

## Route C — Voice model needs real work

```text
Gemini Live
   ↓
JARVIS function call
   ↓
Task Router
   ↓
Work-plane model selected independently
```

## Route D — User is interacting by text

```text
TEXT
 ↓
3.1 Flash-Lite router
 ↓
3.5 Flash-Lite / 3.8 Flash / specialist
```

The voice entry point should therefore not bypass the existing model gateway.

---

# 17. Recommended Model Consolidation

Gemini 3.8 Live creates an opportunity to reduce **duplicated interactive work**, not to delete specialist capabilities.

## Workloads that can move substantially toward Live

```text
Conversational Q&A
Voice navigation
Voice status requests
Voice task initiation
Voice task monitoring
Voice lightweight reasoning
Voice multimodal discussion
Voice tool orchestration
```

This may substantially reduce calls to:

```text
Gemini 3.5 Flash-Lite
Gemini 3.6 Flash
Gemini 3.7 Flash
```

for voice-originated interactive requests.

## Workloads that should remain specialized

```text
Structured-output-heavy pipelines
Code execution
File Search
URL Context
Computer Use
High-volume compression
Embedding generation
Groq Browser Search
Specialized multimodal JSON workflows
```

---

# 18. The Most Important Model-Plane Split

JARVIS should conceptually become:

```text
                    USER
                     │
          ┌──────────┴───────────┐
          │                      │
      TEXT INPUT             VOICE INPUT
          │                      │
          ↓                      ↓
   WORK-PLANE ROUTER       GEMINI 3.8 LIVE
          │                      │
          │                conversation
          │                + function call
          │                      │
          └──────────┬───────────┘
                     ↓
               JARVIS GATEWAY
                     ↓
            DETERMINISTIC CONTROL
                     ↓
      ┌──────────────┼──────────────────┐
      │              │                  │
      ↓              ↓                  ↓
   3.8 Flash       Qwen/GPT-OSS       Gemma
      │              │                  │
      └──────────────┴──────────────────┘
                     ↓
                 TOOLS / DATA
                     ↓
                VERIFICATION
                     ↓
                  RESULT
                     ↓
         ┌───────────┴───────────┐
         │                       │
       TEXT                  GEMINI LIVE
                                 ↓
                              AUDIO
```

---

# 19. Extended Thinking Is Not Always the Default

Do not make Extended Thinking the default for every spoken sentence.

Use a router policy:

```text
LOW COMPLEXITY
→ Gemini 3.8 Live

HIGH COMPLEXITY
→ Gemini 3.8 Live Extended Thinking
```

Examples:

```text
"What time is it?"
→ 3.8 Live

"Open my project."
→ 3.8 Live

"Explain what Docker is."
→ 3.8 Live

"Research the latest changes to Linux kernel scheduling and compare them."
→ 3.8 Live Extended Thinking

"Analyze my entire repository, identify architectural problems, patch them, test everything and report back."
→ 3.8 Live Extended Thinking → JARVIS deep work plane
```

This preserves realtime responsiveness without paying the latency of extended reasoning for trivial turns.

---

# 20. Search Strategy With Live

Gemini 3.8 Live supports Google Search grounding.

However JARVIS should NOT automatically make Google grounding the only research mechanism.

Keep:

```text
Live Search Grounding
+
JARVIS Native Search Tools
+
GPT-OSS Browser Search
+
3.8 Flash reasoning
```

as separate mechanisms.

This preserves:

- source provenance;
- independent verification;
- model diversity;
- fallback paths;
- quota isolation.

---

# 21. Structured Output Policy

Because Gemini 3.8 Live currently does not support Structured Outputs, JARVIS must never depend on raw Live prose for control-plane contracts.

Use:

```text
Live model
    ↓
Function call
    ↓
Pydantic validation
    ↓
Capability manifest
    ↓
Policy decision
```

rather than:

```text
Live model
    ↓
"JSON-looking text"
    ↓
execute
```

The second design violates the spirit of the frozen zero-trust architecture.

---

# 22. Updated Failure Model for Live

New failure modes must be added to the model gateway.

### LIVE-01 — WebSocket disconnect

Action:

```text
retain task/session state
→ attempt session resumption
→ continue
```

### LIVE-02 — GoAway

Action:

```text
persist latest resumption token
→ reconnect before termination
```

### LIVE-03 — Audio input interruption

Action:

```text
stop / reprioritize current response
→ preserve task state
→ continue user interaction
```

### LIVE-04 — Async function timeout

Action:

```text
function call becomes task
→ JARVIS tracks task ID
→ result returned later
```

### LIVE-05 — Client disconnect during active tool call

Action:

```text
do NOT assume cancellation
→ preserve task/effect state
→ follow JARVIS cancellation semantics
```

### LIVE-06 — Live model outage

Action:

```text
fallback to text / alternate voice path
→ preserve task state
→ mark DEGRADED when appropriate
```

### LIVE-07 — Context compression failure

Action:

```text
preserve persisted JARVIS summary/state
→ rebuild session context
→ resume conversation
```

---

# 23. New Live Session Contract

Recommended model-plane contract:

```python
class LiveSessionState(str, Enum):
    CONNECTING = "CONNECTING"
    ACTIVE = "ACTIVE"
    GENERATING = "GENERATING"
    TOOL_PENDING = "TOOL_PENDING"
    BACKGROUND_REASONING = "BACKGROUND_REASONING"
    RECONNECTING = "RECONNECTING"
    DEGRADED = "DEGRADED"
    CLOSED = "CLOSED"
    FAILED = "FAILED"
```

A Live session must always be associated with:

```text
request_id
session_id
task_id
user_id
model_id
provider
connection_id
resumption_handle
created_at
last_server_event_at
audio_input_tokens
audio_output_tokens
```

---

# 24. Updated Gateway Scoring

The existing scoring function remains valid for work-plane inference:

```text
Score(model, task)
=
CapabilityFit
× AvailabilityWeight
× HealthWeight
× QuotaHeadroom
× LatencyFit
```

For Live selection, add:

```text
RealtimeSuitability
+
SessionHealth
+
AudioLatencyFit
+
ToolConcurrencyFit
```

Conceptually:

```text
LiveScore
=
CapabilityFit
×
RealtimeSuitability
×
SessionHealth
×
QuotaHeadroom
×
AudioLatencyFit
×
ToolCompatibility
```

Hard filters remain mandatory.

---

# 25. Updated Model Roster

## Pool A — Gemini Flash Quality

```text
gemini-3.8-flash
    Role:
    Premium structured work / deep agent reasoning

gemini-3.7-flash
    Role:
    Advanced agentic coding / fallback

gemini-3.6-flash
    Role:
    Quality workhorse / fallback

gemini-3.5-flash
    Role:
    Compatibility fallback
```

Existing observed baseline from the previous roster:

```text
RPM: 5 each
TPM: 250K each
RPD: 20 each
```

Runtime provider observations override static values.

---

## Pool B — Gemini Flash-Lite

```text
gemini-3.1-flash-lite
    Role:
    Router / classifier / preprocessing

gemini-3.5-flash-lite
    Role:
    High-volume structured-output everyday brain
```

Existing observed baseline:

```text
RPM: 15 each
TPM: 250K each
RPD: 500 each
```

---

## Pool C — Gemma Compression

```text
gemma-4-31b-it

Role:
    compression
    summarization
    fact extraction
    high-volume background processing
```

Existing observed baseline:

```text
RPM: 30
TPM: 16K
RPD: 14,400
Context: 256K
```

---

## Pool D — Groq Specialists

```text
qwen/qwen3.8-27b
    Role:
    fast multimodal / coding / vision

openai/gpt-oss-120b
    Role:
    heavy reasoning / browser search / code execution
```

Existing roster baseline:

```text
RPM: 30 each
TPM: 8K each
RPD: 1,000 each
TPD: 200K each
```

**Important:** Current Groq documentation displays some rate-limit fields differently across its model and rate-limit pages. Runtime response headers must therefore remain authoritative. The previous roster's semantic interpretation of Groq headers should be retained, but the static values must be validated at runtime.

---

## Pool E — Realtime Voice

```text
gemini-3.8-live
    Role:
    Default realtime voice agent

gemini-3.8-live-extended-thinking
    Role:
    High-reasoning realtime voice agent
```

Current project dashboard observation:

```text
RPM: Unlimited
RPD: Unlimited
TPM: 65K
```

Do not convert "Unlimited" to an infinite numerical quota.

---

## Memory Domain

```text
gemini-embedding-2

Role:
    multimodal vector embeddings

Input:
    text / image / video / audio / PDF

Output:
    embeddings

Dimensions:
    128–3072
Recommended:
    768 / 1536 / 3072
```

Observed baseline:

```text
RPM: 100
TPM: 30K
RPD: 1,000
```

---

## Search Grounding Domain

```text
Gemini 2.5 Flash / Flash-Lite

Role:
    Google Search grounding allowance
```

Previous project observation:

```text
500 grounded prompts/day shared
```

Runtime project state remains authoritative.

---

# 26. Optional Audio Specialists

The roster may retain these as secondary/experimental audio models:

```text
gemini-3.5-live-translate-preview
gemini-3.5-transcribe
gemini-3.5-transcribe-live
gemini-3.1-flash-tts-preview
```

They should NOT be the main JARVIS conversational voice path by default.

Reason:

```text
Gemini 3.8 Live already combines:
audio input
+
reasoning
+
tool calling
+
native audio output
```

Dedicated transcription/translation models remain useful when JARVIS specifically needs:

```text
pure transcription
translation-only workloads
batch transcript processing
specialized audio processing
```

---

# 27. Model Replacement / Retirement Policy

Do not immediately delete old models.

Instead:

```text
NEW MODEL
   ↓
QUALIFICATION
   ↓
SHADOW
   ↓
CANARY
   ↓
PRODUCTION
   ↓
RETIRE OLD ROLE ONLY AFTER EVIDENCE
```

For Gemini 3.8 Live specifically:

### Candidate migrations

```text
Voice conversational workloads
    old → 3.8 Live

Voice complex reasoning
    old → 3.8 Live Extended Thinking

Voice tool orchestration
    old → 3.8 Live / ET

Voice live research
    old → 3.8 Live ET
```

Do not migrate:

```text
structured output work
code execution
file search
URL context
computer use
embedding generation
bulk compression
specialist browser search
```

unless a dedicated replacement capability exists.

---

# 28. Recommended Initial Production Architecture

```text
                           USER
                            │
               ┌────────────┴────────────┐
               │                         │
             TEXT                      VOICE
               │                         │
               ↓                         ↓
      Gemini Flash-Lite            Gemini 3.8 Live
         Router                     / Live ET
               │                         │
               └────────────┬────────────┘
                            ↓
                     JARVIS Gateway
                            │
           ┌────────────────┼─────────────────┐
           │                │                 │
           ↓                ↓                 ↓
      3.8 Flash       Qwen / GPT-OSS      Gemma
     Work plane        specialists       compression
           │                │                 │
           └────────────────┼─────────────────┘
                            ↓
                       TOOL PLANE
                            ↓
                       VERIFICATION
                            ↓
                       EFFECT RECEIPT
                            ↓
                    RESPONSE SYNTHESIS
                            │
                 ┌──────────┴──────────┐
                 │                     │
               TEXT                 VOICE
                                      ↓
                               Gemini Live
```

---

# 29. Example End-to-End Voice Interaction

## Simple task

```text
USER:
"Hey JARVIS, what time is it?"

        ↓

Gemini 3.8 Live

        ↓ function call

jarvis_system_status / clock

        ↓

Capability Firewall
        ↓
Policy Engine
        ↓
Clock tool
        ↓
Verified observation

        ↓ FunctionResponse

Gemini Live

        ↓

"It's 3:42 PM."
```

No second general-purpose LLM is necessary.

---

# 30. Example Deep Voice Task

```text
USER:
"JARVIS, audit this repository for security problems."

        ↓

Gemini 3.8 Live Extended Thinking

        ↓

jarvis_code / jarvis_task

        ↓

JARVIS Task

        ↓

Policy

        ↓

Coding Specialist

        ↓

Gemini 3.8 Flash
or
Qwen 3.8-27B
or
GPT-OSS 120B

        ↓

Sandbox

        ↓

tests

        ↓

verification

        ↓

result

        ↓

FunctionResponse

        ↓

Gemini Live ET

        ↓

spoken report
```

This is the intended relationship between the realtime and work-plane models.

---

# 31. Why This Is Better Than Using Live Alone

A single model cannot simultaneously be:

```text
best realtime voice model
+
best structured-output model
+
best code-execution model
+
best file-search model
+
best high-volume compression model
+
best embedding model
+
best browser-search model
```

The model-plane should therefore optimize for:

```text
capability
+
latency
+
quota
+
tool compatibility
+
context requirements
+
failure isolation
```

rather than simply:

```text
"pick the smartest available model."
```

---

# 32. Updated Routing Philosophy

The primary question is no longer:

> Which model is smartest?

The primary question becomes:

> **Which model provides the required capability with the correct modality, latency, tool contract, context budget, quota headroom, and health state?**

Therefore:

```text
voice
→ Live

hard voice
→ Live Extended Thinking

structured deep work
→ 3.8 Flash

fast vision/coding
→ Qwen

browser-search-heavy work
→ GPT-OSS

high-volume compression
→ Gemma

memory embeddings
→ Embedding 2
```

---

# 33. Security Invariants

The following are mandatory:

1. Live model output is untrusted model-generated content.
2. Live function calls do not bypass the Capability Firewall.
3. Live function calls do not bypass the Policy Engine.
4. Live function calls do not bypass HITL requirements.
5. Live function calls do not bypass commit-time authorization.
6. Live function calls for capabilities requiring the Action Broker MUST traverse the same Action Broker path.
7. Live function results do not become verified truth without the applicable verifier.
8. Live-generated prose never acts as an authorization token.
9. Raw audio/transcripts are subject to privacy and retention controls.
10. Ephemeral Live credentials should be used for client-facing connections where appropriate.

---

# 34. Client Security

For a browser/client-to-server voice architecture, prefer ephemeral authentication tokens rather than exposing a long-lived Gemini API key to the client.

A secure pattern is:

```text
JARVIS backend
    ↓
create ephemeral token
    ↓
client
    ↓
Gemini Live WebSocket
```

The token should be restricted to the intended model/session configuration where possible.

---

# 35. Quota Architecture Update

The previous three-layer quota architecture remains:

```text
Layer 1
Provider observation

Layer 2
Local reservation / lease state

Layer 3
Admission prediction
```

But Live adds:

```text
Layer 2b
Live Session Resource State
```

The complete structure becomes:

```text
Provider
   ↓
authoritative observation
   ↓
request reservation
   +
live-session reservation
   ↓
atomic admission
   ↓
dispatch / stream
   ↓
reconcile
```

---

# 36. Effective Capacity Calculation

The previous formula:

```text
Daily Task Capacity
=
min(
    RPD / AvgCallsPerTask,
    DailyTokenBudget / AvgTokensPerTask
)
```

remains valid for ordinary generation.

For Live:

```text
Live Task Capacity
=
min(
    session/resource headroom,
    token headroom,
    connection health,
    active-session capacity,
    provider observations
)
```

Do not infer unlimited task capacity from Unlimited RPD alone.

---

# 37. Telemetry Additions

Add Live-specific spans:

```text
live.session.open
live.connection.open
live.audio.input
live.audio.output
live.interruption
live.function_call
live.function_response
live.background_reasoning
live.context_compression
live.session_resume
live.connection.close
```

Metrics:

```text
live_ttfa
live_audio_input_tokens
live_audio_output_tokens
live_session_duration
live_reconnect_count
live_resume_success_rate
live_tool_call_latency
live_interruption_latency
live_background_task_duration
live_active_sessions
```

Never export raw audio or sensitive transcripts without an explicit telemetry policy.

---

# 38. Evaluation Plan for Gemini 3.8 Live

Live models require a dedicated evaluation suite rather than being evaluated only with text benchmarks.

### Conversation

```text
turn taking
barge-in
interruption
silence handling
topic switching
conversation recovery
long-session continuity
```

### Speech

```text
speech recognition
accent robustness
numbers
names
technical vocabulary
Hindi
English
code-switching / Hinglish
```

### Agentic behavior

```text
function selection
argument correctness
tool latency
async completion
background reasoning
task completion
```

### JARVIS security

```text
prompt injection
voice prompt injection
malicious function requests
tool poisoning
approval forgery
privilege escalation
data exfiltration
cancellation races
stale function responses
```

### Long-session resilience

```text
10-minute connection rollover
context compression
session resumption
GoAway recovery
client disconnect
provider disconnect
tool timeout
late function response
```

---

# 39. Recommended Live Benchmark Set

The Google-published September 2026 voice evidence includes:

```text
Speech-to-Speech Quality Index
τ-Voice
Sierra τ-Voice Banking
Big Bench Audio
ServiceNow EVA-Bench
```

Published reporting currently places Gemini 3.8 Live Extended Thinking at:

```text
Speech-to-Speech Quality Index: 82.6
τ-Voice task completion:        68.6%
Sierra τ-Voice Banking:         35.1%
Big Bench Audio:                97.7%
```

These numbers must remain clearly labeled as benchmark-specific and configuration-specific. The Extended Thinking results use high reasoning effort and should not be interpreted as universal performance across all tasks.

The standard Gemini 3.8 Live and Extended Thinking models should therefore be evaluated separately.

---

# 40. Required Gateway Convergence for Live

The current implementation audit identified a critical discrepancy between the intended
architecture and the implementation:

```text
Text/CLI:
proposal → capability → policy → EffectAuthorization → Action Broker → execution → verification

Live:
function call → local manifest → policy → inline Python branch
```

The second path is not acceptable as the final architecture.

## 40.1 Mandatory convergence

All model-originated capabilities, including Gemini Live, must converge on the same
governance boundary:

```text
MODEL PROPOSAL
      ↓
CAPABILITY REGISTRY
      ↓
CAPABILITY FIREWALL
      ↓
POLICY ENGINE
      ↓
┌───────────────┴────────────────┐
│                                │
READ PATH                     EFFECT PATH
│                                │
direct governed read        HITL / revalidation
│                                │
result normalization        commit-time auth
│                                │
IFC                            Action Broker
│                                │
└───────────────┬────────────────┘
                ↓
         execution / verifier
                ↓
          effect receipt
```

## 40.2 HITL behavior

The Live bridge MUST treat:

```text
DENY
ALLOW
REQUIRE_HITL
```

as distinct outcomes.

`REQUIRE_HITL` MUST NOT fall through as success.

Voice-originated approval must be bound to a deterministic approval payload, proposal identity,
canonical arguments hash, target witness, policy version, and expiration. A spoken "yes" is
only a confirmation signal for that specific pending approval; it is not a general authorization
for subsequent model actions.

## 40.3 No parallel authorization semantics

The Live bridge must not maintain its own alternative risk or permission logic.

Where an existing canonical capability already exists, the Live bridge should translate
provider function-call arguments into the canonical invocation and then use the existing
governance path.

## 40.4 No inline native execution

Branches equivalent to:

```python
datetime.now()
Path.read_text()
Path.write_text()
subprocess.run(...)
```

must not be separate Live security semantics when a canonical capability exists.

The Live adapter may translate protocol events and provider schemas, but execution authority
belongs to the canonical capability/tool plane.

## 40.5 Required interfaces

```python
class RealtimeModelAdapter(Protocol):
    async def connect(self, ...): ...
    async def send_audio(self, ...): ...
    async def receive_events(self, ...): ...
    async def close(self, ...): ...
    async def resume(self, ...): ...
```

```python
class LiveCapabilityProjector(Protocol):
    def project(self, registry, session_context, policy_context): ...
```

```python
class LiveToolBridge(Protocol):
    async def dispatch_function_call(self, call, session_context): ...
```

The bridge must call the same central governance path used by other model-originated
capability proposals.

---

# 41. Live Capability Contract

Expose to the provider-facing declaration:

```text
name
description
parameters
canonical capability_id
supports_async
```

Keep internal authorization details outside the model-visible description unless needed for
safe tool selection.

## 41.1 One registry, multiple projections

```text
Canonical Registry
   ├── CLI projection
   ├── Specialist projection
   ├── Deep Agent projection
   ├── MCP/A2A projection
   └── Gemini Live projection
```

Every projection is policy-constrained.

Aliases must map deterministically to one canonical capability ID.

## 41.2 Task-aware least privilege

The Live tool list should be computed from:

```text
user/session identity
+
active task
+
current autonomy level
+
policy state
+
data-flow constraints
+
sandbox availability
+
tool health
+
model compatibility
```

A capability legitimate for one task may be absent for another.

## 41.3 Runtime capability changes

If task state, policy, or environment materially changes capability visibility during a Live
session, update the model-visible declaration using the supported Live API configuration/update
mechanism where available.

In-flight calls must always be revalidated against the current canonical manifest and policy.

---
# 42. Compatibility Matrix

| Capability | 3.8 Live | 3.8 Live ET | 3.8 Flash | Qwen 3.8 | GPT-OSS 120B | Gemma 4 31B |
|---|---:|---:|---:|---:|---:|---:|
| Realtime audio | YES | YES | NO | NO | NO | NO |
| Native spoken output | YES | YES | NO | NO | NO | NO |
| Async function calls | YES | YES | YES | YES | YES | YES |
| Thinking | Interleaved | LOW/MEDIUM/HIGH | YES | YES | YES | YES |
| Structured output | NO | NO | YES | YES | YES | YES |
| Code execution | NO | NO | YES | Model/tool dependent | YES | Model dependent |
| File Search | NO | NO | YES | NO | NO | NO |
| URL Context | NO | NO | YES | NO | NO | NO |
| Search grounding | YES | YES | YES | External/tool dependent | Browser Search |
| Vision | YES | YES | YES | YES | NO | YES |
| Long context | ~131K API contract | ~131K API contract | 1M | 131K | 131K | 256K |
| Low-latency voice | PRIMARY | PRIMARY | NO | NO | NO | NO |
| High-volume compression | NO | NO | NO | NO | NO | PRIMARY |

---

# 43. Final Recommended Roster

```text
════════════════════════════════════════════════════════════
JARVIS MODEL ROSTER
════════════════════════════════════════════════════════════

REALTIME VOICE
────────────────────────────────────────────────────────────
Gemini 3.8 Live
    realtime conversational voice
    default voice model

Gemini 3.8 Live Extended Thinking
    complex realtime voice
    background reasoning
    async tool workflows


WORK / REASONING
────────────────────────────────────────────────────────────
Gemini 3.8 Flash
    structured deep work
    coding
    computer use
    file search
    URL context
    long-context work

Gemini 3.7 Flash
    advanced coding fallback

Gemini 3.6 Flash
    quality workhorse fallback

Gemini 3.5 Flash
    compatibility fallback


HIGH-VOLUME OPERATIONS
────────────────────────────────────────────────────────────
Gemini 3.5 Flash-Lite
    everyday structured work

Gemini 3.1 Flash-Lite
    router / classifier / preprocessing

Gemma 4 31B IT
    compression / summarization / fact extraction


SPECIALISTS
────────────────────────────────────────────────────────────
Qwen 3.8-27B
    fast coding + visual understanding

GPT-OSS 120B
    heavy reasoning + browser search


MEMORY
────────────────────────────────────────────────────────────
Gemini Embedding 2
    multimodal embeddings


SEARCH
────────────────────────────────────────────────────────────
Gemini Search Grounding
    Google Search grounding quota


OPTIONAL AUDIO SPECIALISTS
────────────────────────────────────────────────────────────
Gemini 3.5 Transcribe Live
Gemini 3.5 Live Translate
Gemini 3.1 Flash TTS
```

---

# 44. Final Architecture Decision

The introduction of Gemini 3.8 Live does **not** mean:

```text
"JARVIS uses Gemini Live for everything."
```

It means:

```text
"JARVIS finally has a first-class realtime voice model plane."
```

The recommended operating model is:

```text
                GEMINI 3.8 LIVE
                      │
                realtime voice
                      │
          ┌───────────┴───────────┐
          │                       │
      simple work             complex work
          │                       │
      3.8 Live          3.8 Live Extended Thinking
                                  │
                                  ↓
                         JARVIS function call
                                  │
                                  ↓
                         JARVIS Control Plane
                                  │
                 ┌────────────────┼─────────────────┐
                 ↓                ↓                 ↓
             3.8 Flash         Qwen            GPT-OSS
             deep work         specialist       specialist
                 │                │                 │
                 └────────────────┼─────────────────┘
                                  ↓
                               Tools
                                  ↓
                             Verification
                                  ↓
                              Result
                                  ↓
                           Gemini Live
                                  ↓
                                VOICE
```

The model roster therefore becomes **simpler at the user-facing level without becoming weaker at the capability level**.

---

# 45. Source Register

Primary provider sources:

1. Google AI for Developers — Gemini 3.8 Live
   https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live

2. Google AI for Developers — Gemini 3.8 Live Extended Thinking
   https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live-extended-thinking

3. Google AI for Developers — Live API capabilities
   https://ai.google.dev/gemini-api/docs/live-api/capabilities

4. Google AI for Developers — Live API tools
   https://ai.google.dev/gemini-api/docs/live-api/tools

5. Google AI for Developers — Live API session management
   https://ai.google.dev/gemini-api/docs/live-api/session-management

6. Google AI for Developers — Live API best practices
   https://ai.google.dev/gemini-api/docs/live-api/best-practices

7. Google AI for Developers — Live API thinking
   https://ai.google.dev/gemini-api/docs/live-api/thinking

8. Google AI for Developers — Live API SDK quickstart
   https://ai.google.dev/gemini-api/docs/live-api/get-started-sdk

9. Google AI for Developers — Gemini pricing
   https://ai.google.dev/gemini-api/docs/pricing

10. Google AI for Developers — Gemini model catalog
    https://ai.google.dev/gemini-api/docs/models

11. Google AI for Developers — Gemini deprecations
    https://ai.google.dev/gemini-api/docs/deprecations

12. Google AI for Developers — Gemini 3.8 Flash
    https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash

13. Google AI for Developers — Gemini Embedding 2
    https://ai.google.dev/gemini-api/docs/models/gemini-embedding-2

14. Google DeepMind — Gemini 3.8 Audio model card
    https://deepmind.google/models/model-cards/gemini-3-8-audio/

15. Google AI for Developers — Gemma 4 model card
    https://ai.google.dev/gemma/docs/core/model_card_4

16. Google DeepMind — Gemma 4
    https://deepmind.google/models/gemma/gemma-4/

17. Groq Docs — Rate limits
    https://console.groq.com/docs/rate-limits

18. Groq Docs — Qwen 3.8 27B
    https://console.groq.com/docs/model/qwen/qwen3.8-27b

19. Groq Docs — GPT-OSS 120B
    https://console.groq.com/docs/model/openai/gpt-oss-120b

20. Groq Docs — Built-in tools
    https://console.groq.com/docs/tool-use/built-in-tools

21. Groq Docs — Browser Search
    https://console.groq.com/docs/tool-use/built-in-tools/browser-search

22. Groq Docs — Code Execution
    https://console.groq.com/docs/tool-use/built-in-tools/code-execution

23. Groq Docs — Vision
    https://console.groq.com/docs/vision

24. Google Gemini API GitHub skills / Live API implementation material
    https://github.com/google-gemini/gemini-skills

Supplementary independent benchmark/reporting material was consulted for cross-checking the newly released 3.8 Audio evaluation claims, but provider documentation is the authoritative source for API semantics and the user's own Google AI Studio dashboard is authoritative for current project-specific quota observations.

---

# 46. Implementation Recommendation

The current implementation audit changes the immediate priority.

Before any commit, the Live plane must converge with the canonical security architecture.

Implement in this order:

```text
1. Replace the hard-coded Live capability registry with a canonical capability projection.
2. Map Live aliases deterministically to canonical capability IDs.
3. Route Live read capabilities through canonical governed dispatch.
4. Route Live write/effect capabilities through the full HITL → revalidation → commit-time authorization → Action Broker path.
5. Eliminate ad-hoc inline Live execution where a canonical capability already exists.
6. Add Live capability projection contract tests.
7. Add Live REQUIRE_HITL tests.
8. Add Live Action Broker execution tests.
9. Add Live verification/effect-receipt tests.
10. Re-run continuous voice/background-task tests.
11. Re-run Flash Pool protection tests.
12. Only then qualify Live as the production realtime interface.
```

The existing work-plane model reservoir remains active throughout this process.

The Live voice plane may be implemented before the formal Milestone 14 HUD release as an
engineering validation overlay, but this does not reorder the canonical 14 milestones and does
not mean Milestone 14 is complete.

---

# 47. Current Implementation Audit Alignment — 16 September 2026

The current implementation audit identified these pre-commit discrepancies:

```text
1. Live capability declarations are hard-coded.
2. Live uses a second local capability namespace.
3. Live read/write execution is partly implemented inline.
4. Live bypasses the Action Broker.
5. Live does not correctly handle REQUIRE_HITL.
6. Several canonical user-facing capabilities are not Live-visible.
```

These are implementation gaps, not changes to the frozen JARVIS trust model.

### Required closure criteria

```text
[ ] Canonical registry is the source of Live capability projection.
[ ] Per-task least-privilege projection works.
[ ] Canonical capability IDs and provider aliases are mapped deterministically.
[ ] Live reads traverse the governed read path.
[ ] Live effects traverse the full hardened write path.
[ ] REQUIRE_HITL cannot fall through.
[ ] Action Broker remains authoritative.
[ ] External-state verification remains authoritative.
[ ] No raw JSON/prose is treated as authorization.
[ ] Real manual tests prove current-state tools.
[ ] Real manual tests prove user-facing filesystem/shell capabilities subject to policy.
[ ] Flash Pool A remains protected for ordinary voice interaction.
```

## Canonical Design Principle

> **Gemini 3.8 Live is the realtime voice interface to JARVIS — not the authority inside JARVIS; all Live-originated effects remain subject to the canonical capability, policy, approval, broker, and verification path.**

That single rule allows JARVIS to gain continuous, natural voice interaction without sacrificing the zero-trust control architecture.
