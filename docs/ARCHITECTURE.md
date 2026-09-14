# JARVIS v1.0.0: Stateful Personal AI Operating System
## Master Architecture Specification & Threat Model
*Classification: Engineering Design Document (Canonical Baseline)*  
*Version: 1.0.0-CANONICAL*  
*Paradigm: Personal AI Operating System engineered using zero-trust principles*

---

## 1. Executive Summary & Foundational Axioms

Most autonomous agent architectures fail in production because they rely on implicit trust: trusting the model to follow instructions, trusting external tools to execute reliably, trusting delimiters to isolate untrusted data, and trusting tool return codes as ground truth.

**JARVIS v1.0.0** is engineered using **Zero-Trust Distributed Systems Principles**.

### The Foundational Axioms

1. **The Model Is Not the Trust Boundary:** The Large Language Model (LLM) is an untrusted reasoning component producing **proposed intent**. The policy engine, action broker, and external verification layers form the security, authorization, and execution boundary.
2. **Data Trust $\neq$ Instruction Authority:** An agent may ingest and read external data, but external data possesses **zero instruction authority** over the system. Delimiters and markup provide context hygiene, not authorization. Reasoning alone can **never** elevate untrusted data into trusted status.
3. **Intent $\rightarrow$ Policy $\rightarrow$ Broker $\rightarrow$ Reality:**
   * **Model:** Proposes *what* it wants to do (**Proposed Intent**).
   * **Pre-Approval Policy Guard:** Evaluates *whether* it may do it (**Authorization & Dynamic Risk**).
   * **HITL & Commit-Time Authorization:** Tamper-resistant human authorization bound to an immutable capability record and verified against the target state witness immediately prior to dispatch.
   * **Action Broker:** Enforces *how* it is safely executed (**Idempotency, Leases, Deduplication, Compensation & Circuit Breakers**).
   * **External State Verifier:** Validates *whether* the desired real-world effect actually occurred at timestamp $t$ (**Point-in-Time Reality**).
4. **Durable Replay-Safe Semantics:** Crash recovery guarantees resume from the last persisted checkpoint, but external side effects are protected by idempotency, leases, and verification to guarantee replay safety.

---

## 2. Control Plane vs. Data Plane Separation

To prevent context contamination and security review ambiguity, JARVIS v1.0.0 strictly bifurcates state and communication into two independent planes:

```text
                               JARVIS v1.0.0
                                      │
          ┌───────────────────────────┴───────────────────────────┐
          ▼                                                       ▼
    CONTROL PLANE                                            DATA PLANE
  (What may happen)                                     (The actual content)
          │                                                       │
 ├── LangGraph State Machine                             ├── Artifacts / Filesystem
 ├── Policy Engine Rules                                 ├── Raw Tool Payloads / Data
 ├── Dynamic Risk Scoring                                ├── Memory Embeddings / Text
 ├── Approval Tokens & Hashes                            ├── Model Generation Streams
 ├── Task Lifecycle & DLQ                                └── Sensitive Secrets
 └── Component Heartbeats
```

* **Control Plane:** Low-bandwidth, strictly-typed metadata controlling execution flow, state transitions, permission decisions, and lifecycle states.
* **Data Plane:** High-bandwidth data channels carrying file payloads, large tool outputs, media streams, and database records. Large data never traverses the Control Plane; it is referenced via URI, hash, and metadata.

---

## 3. Read Path vs. Write / Effect Path

JARVIS v1.0.0 optimizes latency by maintaining two distinct execution paths through the policy and action layers:

```text
                    AGENT INTENT PROPOSAL
                              │
               ┌──────────────┴──────────────┐
               ▼                             ▼
           READ PATH                    WRITE / EFFECT PATH
       (Zero side-effects)           (State-mutating operations)
               │                             │
        Capability Firewall           Capability Firewall
               │                             │
       Tool Execution (Native/MCP)     Pre-Approval Policy Guard
               │                             │
       Result Normalization          Dynamic Risk Assessment
               │                             │
       Information-Flow Control       HITL Approval (if required)
               │                             │
        Return to Context            Post-Approval Revalidation
                                             │
                                       Commit-Time Authorization
                                             │
                                       Action Broker (Idempotency)
                                             │
                                       Execution Fabric / Sandbox
                                             │
                                       External-State Verification
                                             │
                                       Temporal Effect Receipt
```

* **Read Path (Fast):** Read-only operations (file reads, search queries, database `SELECT`, system metrics) bypass the heavy approval and effect-ledger overhead. They pass through the Capability Firewall and Information-Flow Control (IFC) sanitizers directly.
* **Write Path (Hardened):** Any mutation (file write, terminal command, email dispatch, database `INSERT/UPDATE`) traverses the complete 5-stage approval pipeline, Action Broker idempotency ledger, sandbox isolation, and external verification loop.

---

## 4. Cross-Cutting Governance Plane

Surrounding all layers of JARVIS v1.0.0 is an immutable Governance Plane enforcing organizational, financial, and operational constraints:

```text
╔══════════════════════════════════════════════════════════════════════════════╗
║                       CROSS-CUTTING GOVERNANCE PLANE                         ║
║                                                                              ║
║  Identity & RBAC   │ Secrets Vault    │ Policy Versioning │ Audit Trails    ║
║  Privacy/DLP       │ Spend Budgets    │ Continuous Evals  │ Chaos Testing   ║
║  Lifecycle Manager │ Semantic Routing │ Contract Tests    │ Dead Letter Q   ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

* **Configuration Snapshots:** Every task execution records the exact configuration snapshot: `agent_version`, `prompt_version`, `skill_version`, `tool_version`, `policy_version`, `model_version`, `memory_schema_version`, and `workspace_version`.
* **Policy Drift Detection:** Automated CI/CD regression suites run against the declarative policy engine on every commit to prevent accidental permission weakening.
* **Failure Injection (Chaos Engineering):** Continuous test suites deliberately inject LLM timeouts, HTTP 429/500 errors, network partitions, corrupted JSON, stale approvals, and worker crashes to verify recovery behavior.

---

## 5. Master Architecture Diagram

```text
╔══════════════════════════════════════════════════════════════════════════════╗
║                               JARVIS v1.0.0                                  ║
║                    STATEFUL PERSONAL AI OPERATING SYSTEM                     ║
╚══════════════════════════════════════════════════════════════════════════════╝

 USER SURFACES
 Voice │ Text │ Blue Holographic HUD │ Mobile │ Web │ Messaging │ API
                         │
                         ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │                         01. EDGE / GATEWAY                          │
 │ auth │ 02. identity │ sessions │ rate limits │ request IDs │ stream │
 └────────────────────────────────┬────────────────────────────────────┘
                                  │
                                  ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │                       03. INPUT TRUST LAYER                         │
 │                                                                     │
 │ normalize │ classify │ trust labels │ context hygiene delimiters    │
 └────────────────────────────────┬────────────────────────────────────┘
                                  │
                                  ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │             04. INFORMATION-FLOW CONTROL (IFC) / DLP                │
 │                                                                     │
 │ FIDES-inspired pattern: integrity & confidentiality labels          │
 │ provenance tracking │ cross-trust flow │ sensitive sink enforcement │
 └────────────────────────────────┬────────────────────────────────────┘
                                  │
                                  ▼
 ╔═════════════════════════════════════════════════════════════════════╗
 ║                   05. LANGGRAPH CONTROL PLANE                       ║
 ║                                                                     ║
 ║  Task Init (14 Task States)                                         ║
 ║      ↓                                                              ║
 ║  06. Context Builder & Dynamic Artifact Offloading                  ║
 ║      ↓                                                              ║
 ║  Goal / Proposed Intent Synthesis                                   ║
 ║      ↓                                                              ║
 ║  07. Capability Router                                              ║
 ║      │                                                              ║
 ║      ├──── Fast Path (target: sub-second deterministic tools)       ║
 ║      ├──── Specialist Path (target: low-latency interactive domain) ║
 ║      ├──── Deep Path (target: asynchronous long-running harness)    ║
 ║      └──── Background Task Path (durable worker queue & DLQ)        ║
 ║                                                                     ║
 ║             ↓                                                       ║
 ║      CAPABILITY FIREWALL (Pre-Registry Projection & Least Privilege)║
 ║             ↓                                                       ║
 ║      Result Normalization                                           ║
 ║             ↓                                                       ║
 ║      16. Verification (Semantic · Evidence · Execution · Policy)    ║
 ║        │                                                            ║
 ║        ├──── PASS                                                   ║
 ║        ├──── REPAIR / REPLAN                                        ║
 ║        └──── BLOCK                                                  ║
 ║             ↓                                                       ║
 ╚═════════════╤═══════════════════════════════════════════════════════╝
               │
               ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │              08 & 09. AGENT EXECUTION PLANE                         │
 │                                                                     │
 │ 09. Deep Agents Harness (Foundational Subsystem)                    │
 │     Supplies: planning/todos, filesystem tools, subagents, skills,   │
 │     context compaction, and core HITL hooks.                        │
 │                                                                     │
 │ 08. Default Capability Specialists (Configurable memory mode)       │
 │     Research │ Coding │ Computer │ Personal │ Analysis              │
 └────────────────────────────────┬────────────────────────────────────┘
                                  │
                                  ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │               10. CAPABILITY & TOOL PLANE                           │
 │                                                                     │
 │ Capability Registry (ownership, scopes, metadata)                   │
 │       │                                                             │
 │       ├──── Native High-Trust Tools                                 │
 │       ├──── MCP Protocol Adapter (Target: 2026-07-28, Negotiated)   │
 │       └──── A2A Protocol Gateway (1.0.x / v1.0.1+ Outer Boundary)   │
 └────────────────────────────────┬────────────────────────────────────┘
                                  │
                                  ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │                   11. TOOL SECURITY GATE                            │
 │                                                                     │
 │ schema validator │ MCP identity │ tool hash │ injection scanner     │
 └────────────────────────────────┬────────────────────────────────────┘
                                  │
                                  ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │                    12. POLICY ENGINE (PRE-GUARD)                    │
 │                                                                     │
 │ Centralized Policy Gates ALL Tools (including custom/MCP/DeepAgents)│
 │ static tool risk + dynamic invocation risk │ autonomy levels 0-5    │
 └────────────────────────────────┬────────────────────────────────────┘
                                  │
                    ┌─────────────┴──────────────┐
                    ▼                            ▼
               READ PATH                 WRITE PATH / HITL
             (Direct Safe)                       │
                    │                      Pre-Approval Guard
                    │                            │
                    │                      Tamper-Resistant HITL
                    │                            │
                    │                      Post-Approval Revalidation
                    │                            │
                    │                      COMMIT-TIME AUTHORIZATION
                    │                      (Target Witness Bound)
                    │                            │
                    └─────────────┬──────────────┘
                                  ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │                       14. ACTION BROKER                             │
 │                                                                     │
 │ logical effect ID │ idempotency taxonomy │ dedupe │ leases          │
 │ circuit breakers (CLOSED/OPEN/HALF_OPEN) │ SAGA COMPENSATION PLANS  │
 └────────────────────────────────┬────────────────────────────────────┘
                                  │
                                  ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │                     15. EXECUTION FABRIC                            │
 │                                                                     │
 │ APIs │ Browser │ OS │ Shell │ Git │ DB │ Playwright                 │
 │ Multi-tier Sandboxing (Docker $\rightarrow$ gVisor / Kata / microVM)│
 │ Default-deny network egress & proxy inspection                      │
 └────────────────────────────────┬────────────────────────────────────┘
                                  │
                                  ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │                 16. EXTERNAL-STATE VERIFIER                         │
 │                                                                     │
 │ observe actual state │ compare desired state │ point-in-time check  │
 └────────────────────────────────┬────────────────────────────────────┘
                                  │
                                  ▼
                              EFFECT RECEIPT
                      (observed at $t$, ETag, state hash)
                                  │
               ┌──────────────────┼──────────────────┐
               ▼                  ▼                  ▼
     17. MEMORY PLANE     18. EVENT PLANE     19. OBSERVABILITY
               │                  │                  │
               ▼                  ▼                  ▼
       scoped / temporal      scheduler /       two-layer tracing /
       provenance / ACL       causal graph /    20. continuous evals
       optimistic lock        worker leases     chaos testing / DLQ
```

---

## 6. Detailed Subsystem Specifications

### Layer 1 & 2: Edge / Gateway & Identity
* **Distributed Tracing Headers:** Ingress gateway issues correlation headers on every request:
  * `request_id`: UUIDv4 assigned at edge ingress.
  * `session_id`: Client session lifecycle identifier.
  * `task_id`: Top-level orchestrator lifecycle unit.
  * `user_id`: Authenticated entity identity.
* **Streaming Protocol:** Bi-directional WebSockets supporting low-latency telemetry (UI sound waves, agent state pills) and chunked audio streaming via Edge-TTS.

### Layer 3: Input Trust Layer
* **Explicit Trust Taxonomy:**
  ```python
  class TrustLevel(str, Enum):
      SYSTEM_POLICY = "SYSTEM_POLICY"  # Platform policies (highest authority)
      USER_INPUT = "USER_INPUT"  # Direct user input
      MODEL_GENERATED = "MODEL_GENERATED"  # Proposed agent data (untrusted intent)
      ARTIFACT_INTEGRITY_VERIFIED = "ARTIFACT_INTEGRITY_VERIFIED"  # Byte-level cryptographic match
      ARTIFACT_SEMANTICALLY_VERIFIED = "ARTIFACT_SEMANTICALLY_VERIFIED"  # Facts verified by evidence
      EXTERNAL_UNTRUSTED = "EXTERNAL_UNTRUSTED"  # Web crawls, emails, PDFs, MCP tool outputs
  ```
* **Context Hygiene:** Delimiters and XML isolation boundaries provide visual and contextual structure to the model context, but do **not** serve as authorization gates.

### Layer 4: Information-Flow Control (IFC) / DLP (The FIDES Model Pattern)
* **Architecture Pattern:** Follows a FIDES-inspired labeling architecture implemented natively within JARVIS (not coupled to experimental external frameworks).
* **Dual-Label Metadata:** Every data object carries orthogonal labels:
  1. **Integrity Label:** (`UNTRUSTED`, `USER_CONTROLLED`, `SYSTEM_TRUSTED`).
  2. **Confidentiality Label:** (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `SECRET`).
* **Non-Elevation Axiom:** Model reasoning cannot elevate an `UNTRUSTED` label to `SYSTEM_TRUSTED`.
* **Sensitive Sink Enforcement:** Data labeled with high confidentiality (e.g., passwords, API keys) is blocked from flowing into external network sinks or untrusted tool parameters.

### Layer 5: LangGraph Control Plane & Extended Task Lifecycle
* **Comprehensive 14-State Lifecycle Machine:**
  ```python
  class TaskStatus(str, Enum):
      CREATED = "CREATED"
      QUEUED = "QUEUED"
      PLANNING = "PLANNING"
      EXECUTING = "EXECUTING"
      WAITING_TOOL = "WAITING_TOOL"
      WAITING_USER = "WAITING_USER"
      VERIFYING = "VERIFYING"
      REPLANNING = "REPLANNING"
      DEGRADED = "DEGRADED"  # Completed with reduced service/fallback model
      QUARANTINED = "QUARANTINED"  # Stalled/anomalous task moved to DLQ
      COMPLETED = "COMPLETED"
      FAILED = "FAILED"
      CANCELED = "CANCELED"  # Gracefully aborted by user or policy
      EXPIRED = "EXPIRED"  # Exceeded maximum wall-time budget
  ```
* **Graceful Degradation (`DEGRADED`):** If a primary model or tool is unavailable, the fallback pipeline continues execution in a known degraded state, explicitly flagging the receipt so the user is informed of reduced confidence.
* **Cascading Cancellation Semantics:** When a task enters `CANCELED` (e.g., "JARVIS, stop"), a cancellation signal propagates hierarchically:
  $$\text{Parent Task} \longrightarrow \text{Child Subagents} \longrightarrow \text{Active Tool Calls} \longrightarrow \text{Browser Sessions} \longrightarrow \text{Sandbox PIDs}$$

### Layer 6: Context Builder & Dynamic Artifact Offloading
* **Dynamic Offload Criteria:** Data is offloaded to the workspace filesystem (`artifacts/`) if:
  * Estimated token count exceeds threshold (>1000 tokens).
  * Output is structured (code diffs, JSON datasets, CSVs).
  * Content is sensitive or binary (images, PDFs, memory dumps).
  * Tool output is paginatable.
* **Verification Separation:**
  * Byte-level hashing validates `ARTIFACT_INTEGRITY_VERIFIED`.
  * Claims extracted from artifacts require evidence verification to achieve `ARTIFACT_SEMANTICALLY_VERIFIED`.

### Layer 7: Capability Router & Capability Firewall
* **Capability Firewall (Least Privilege Projection):** The Capability Registry maintains all installed tools, but the **Capability Firewall** dynamically restricts what the agent can *see and invoke* for the active task run based on task domain, risk budget, and data-flow constraints.
* **Operational Performance Targets:**
  1. **Fast Tool Path:** Sub-second target when practical. Deterministic tools (calculator, unit conversion, local clock).
  2. **Specialist Path:** Low-latency interactive target. Single-turn, domain-specific execution.
  3. **Deep Agent Path:** Asynchronous / long-running target. Multi-phase planning, worker subagents, and iterative refinement.
  4. **Background Task Path:** Durable asynchronous workers operating on queues.

### Layer 8 & 9: Agent Execution Plane & Deep Agents Harness
* **Deep Agents Harness Integration:** JARVIS integrates the **Deep Agents harness** for foundational execution primitives:
  * Planning, todo management, and progress tracking.
  * Filesystem tools, workspace management, and context compaction.
  * Subagent spawning and isolation.
  * Built-in skills and long-term memory hooks.
* **JARVIS Platform Additions:** JARVIS builds enterprise governance *on top* of the harness:
  * Centralized Policy Engine gating **ALL** tools (closing the loophole where Deep Agents permissions only govern built-in filesystem tools).
  * OpenClaw-style immutable effect authorization records.
  * Action broker with idempotency ledgers, circuit breakers, and Saga compensation plans.
  * Cross-agent resource leases and optimistic concurrency.
  * Temporal memory governance and external-state verification.

### Layer 10: Capability & Tool Plane (MCP & A2A)
* **MCP Protocol Adapter:**
  * **Target Revision:** `2026-07-28` (stateless protocol core, standardized routing headers, cacheable list results).
  * **Negotiated Compatibility:** Dynamically negotiates protocol capabilities with older server implementations (e.g., `2025-11-25`).
  * **Server & Tool Trust Policy:** Tool annotations are treated as untrusted unless originating from an explicitly trusted, authenticated server.
* **A2A Protocol Gateway:** Standardized on **A2A 1.0.x** (targeting release `v1.0.1+` for outer-boundary cross-organizational agent federation; v1.0.0 uses MCP exclusively for all capability tools).

### Layer 11: Tool Security Gate & Component Lifecycle
* **Component Lifecycle State Machine:** Applied to all agents, tools, MCP servers, skills, and models:
  ```text
  REGISTERED ──► VALIDATED ──► ENABLED ──► RUNNING
                                 ▲           │
                                 │       degradation / anomaly
                                 │           ▼
                              REPAIR ◄── QUARANTINED ──► DISABLED ──► RETIRED
  ```

### Layer 12: Policy Engine & Centralized Gate
* **Centralized Gatekeeping:** The Policy Engine evaluates **every** tool invocation, regardless of whether it originates from a native tool, an MCP server, or the Deep Agents harness.
* **Dynamic Invocation Risk:**
  $$\text{InvocationRisk} = f(\text{Tool}, \text{Arguments}, \text{TargetResource}, \text{Environment}, \text{DataSensitivity})$$
* **Autonomy Levels (0–5):**
  * `Level 0`: Observe only.
  * `Level 1`: Recommend action (user executes).
  * `Level 2`: Auto-execute safe, read-only operations.
  * `Level 3`: Auto-execute bounded mutations within approved workspaces.
  * `Level 4`: Fully autonomous within policy budget.
  * `Level 5`: Mandatory approval required for all mutations.

### Layer 13: Hardened 5-Step HITL Approval Pipeline & Commit-Time Authorization
To minimize the Time-of-Check to Time-of-Use (TOCTOU) window to an atomic commit boundary, write/effect operations execute via a strict 5-stage pipeline:

```text
 1. PRE-APPROVAL GUARD
    │ (Validates static policy, dynamic risk, schema, and creates EffectAuthorization draft)
    ▼
 2. TAMPER-RESISTANT USER INTERRUPT (HITL)
    │ (Renders deterministic, isolated Control-Plane diff & payload; defends against OWASP "Lies-in-the-Loop")
    ▼
 3. POST-APPROVAL REVALIDATION
    │ (Re-evaluates authorization record, verifies SHA256 argument hash, workspace version,
    │  and re-runs tool guardrails immediately prior to execution)
    ▼
 4. COMMIT-TIME AUTHORIZATION
    │ (Binds authorization token directly to current target witness ETag/state hash immediately before durable effect)
    ▼
 5. ACTION BROKER DISPATCH
```

* **OWASP "Lies-in-the-Loop" / Dialog Forging Defense:**  
  Attacker-controlled content (e.g., indirect prompt injection from web pages, PDFs, or emails) can attempt to manipulate what the human thinks they are approving by crafting deceptive natural-language explanations (e.g., claiming a destructive file deletion is a routine temporary cache purge).  
  To defeat **HITL Dialog Forging**, the approval UI renders structured payload parameters, file diffs, resource URIs, and risk scores extracted **directly from the deterministic Control Plane**, strictly isolating and deprioritizing any model-generated explanatory prose.

* **Commit-Time Authorization Record:**
  ```python
  class EffectAuthorization(BaseModel):
      proposal_id: UUID
      task_id: UUID
      user_id: str
      session_id: UUID
      agent_id: str
      tool_id: str
      tool_version: str
      canonical_arguments_hash: str  # SHA256 of normalized argument JSON
      target_resource: str
      target_witness_hash: Optional[str] = (
          None  # ETag or state hash observed immediately before commit
      )
      data_scope: str
      policy_version: str
      workspace_version: str
      approval_timestamp: datetime
      expires_at: datetime  # Short-lived TTL (e.g., 5 minutes)
      nonce: str
  ```

### Layer 14: Action Broker, Idempotency & Saga Compensation
* **Side-Effect Taxonomy:**
  * `IDEMPOTENT`: Safe to retry unconditionally (e.g., `GET`, file read).
  * `IDEMPOTENT_WITH_KEY`: Safe to retry when passing identical `logical_effect_id`.
  * `NON_IDEMPOTENT`: Ambiguous outcome on failure; blind retries forbidden.
  * `TRANSACTIONAL`: Requires distributed rollback or compensation logic.
  * `UNKNOWN`: Default fail-safe; requires state verification before retry.
* **Saga Compensation Workflows:** Heterogeneous external operations cannot achieve atomic multi-system rollback. When a multi-step task fails mid-execution, the Action Broker invokes registered compensation handlers (e.g., delete created calendar event, unpublish draft, release cloud lease).
* **Irreversible Effects:** Actions without compensation mechanisms (e.g., financial transactions, permanent file deletion) are classified as `CRITICAL` risk, requiring mandatory Level 5 approval.
* **Circuit Breakers:** Centralized circuit breakers for all external tools tracking states: `CLOSED` (healthy), `OPEN` (threshold exceeded; fail fast), `HALF_OPEN` (testing recovery probe).

### Layer 15: Execution Fabric & Multi-Tier Sandbox
* **Defense-in-Depth Isolation:**
  * **Tier 1 (Baseline):** Ephemeral Docker containers with non-root execution and resource limits.
  * **Tier 2 (Untrusted / Hostile Code):** Userspace kernel isolation (**gVisor**) or lightweight hardware-virtualized microVMs (**Kata Containers**).
* **Egress Firewall:** Default-deny network egress; granular allowlisting specifying destination IP/FQDN, port, and protocol. Sensitive credentials are never exposed directly inside the execution sandbox.

### Layer 16: External-State Verifier & Temporal Effect Receipts
* **Verification Loop:** Rather than trusting tool return values, the verifier queries the external system directly after execution (e.g., querying GitHub API to verify issue creation).
* **Temporal Effect Receipt:**
  ```python
  class EffectReceipt(BaseModel):
      receipt_id: UUID
      task_id: UUID
      logical_effect_id: str
      verification_method: str
      verified_at: datetime
      observed_state_hash: str
      external_version_etag: str | None
      verification_source: str
      verified: bool
  ```

### Layer 17: Memory Plane with Governance & Optimistic Concurrency
* **Memory Categorization & Epistemic Trust (Decoupling Category from Truth):**
  A memory record's category defines its operational domain, **not its epistemic veracity**. Storing an assertion under `FACT` does not automatically confer ground-truth status:
  * **Memory Categories:**
    * `FACT`: Factual assertions about the user, environment, or domain.
    * `PREFERENCE`: User preferences (e.g., theme, tone).
    * `EPISODE`: Log of past interactions and tasks.
    * `GOAL`: Long-term user objectives.
    * `PROJECT_STATE`: Architectural or codebase states.
    * `PROCEDURE`: Validated operational workflows.
    * `POLICY`: Security authorization rules (**read-only to models; writable only by administrators/users**).
  * **Epistemic Status (`epistemic_status`):** Every memory record carries an orthogonal epistemic status:
    * `ASSERTED`: Claim made by user or agent without verified external evidence.
    * `OBSERVED`: Direct sensory or tool observation (e.g., active process list, file existence).
    * `VERIFIED_EXTERNAL`: Corroborated by independent cryptographic hash or external API check.
    * `DISPUTED`: Contradicted by newer observations; marked for active reconciliation.
  * **Provenance & FIDES Ingestion Binding:** Every memory entry records `source_trust_level: TrustLevel` (from Layer 3) and `source_uri`. Claims originating from `EXTERNAL_UNTRUSTED` can **never** be persisted as `VERIFIED_EXTERNAL` without independent deterministic verification.
* **Optimistic Concurrency Control:** Every memory entry carries an integer `version`. Concurrent updates must match the existing version:
  $$\text{UPDATE memory SET val = :val, version = version + 1 WHERE id = :id AND version = :version}$$
  If the version has advanced, a reconciliation handler is invoked.

### Layer 18: Proactive Event Bus & Dead Letter Queue (DLQ)
* **Distributed Leases:** Scheduled tasks must acquire a distributed lease (`lease_id`, `worker_id`, `lease_expiry`) to guarantee single-worker execution.
* **Causal Loop Defense:** Events carry distributed causation headers (`event_id`, `causation_id`, `correlation_id`, `depth`). Causal graph cycle detection prevents proactive feedback loops.
* **Dead Letter Queue (DLQ) / Quarantine:** Tasks that exhaust retry budgets or exhibit unrecoverable anomalies are moved to the DLQ with full diagnostic context, checkpoints, and traces for operator inspection.

### Layer 19: Dual-Layer Observability
* **Operational Telemetry:** OpenTelemetry spans tracking model latency, tool execution, DB queries, and worker queue depths.
* **Reasoning / Evaluation Trace:** Structured decision logs tracking capability selection, argument synthesis, and verifier scorecards.
* **Privacy & Secret Redaction:** Automated regex and entropy-based redaction filters out API keys, tokens, passwords, and PII prior to log export. Private chain-of-thought traces are never exposed over public telemetry channels.

### Layer 20: Continuous Evaluation, Trajectory Scoring & Chaos Suite
* **Trajectory-Level Scoring with Non-Compensable Hard Security Gates:** Every test run is evaluated across 8 sequential checkpoints. Rather than a naive arithmetic average where security breaches could be masked by high syntactic scores, the evaluation separates hard binary security gates from capability execution quality:
  $$\text{Score} = (\text{Gate}_{\text{Policy}} \times \text{Gate}_{\text{Approval}} \times \text{Gate}_{\text{Verify}}) \times \text{Avg}(\text{Route}, \text{Tool}, \text{Args}, \text{Execution}, \text{Response})$$
  * **Hard Security Gates ($\{0, 1\}$):** $\text{Gate}_{\text{Policy}}$ (zero tolerance for unauthorized tool invocation or privilege escalation), $\text{Gate}_{\text{Approval}}$ (zero tolerance for unapproved side-effects or TOCTOU violations), and $\text{Gate}_{\text{Verify}}$ (zero tolerance for unverified state mutations or false positives). A failure at any gate instantly zeroes out the entire trajectory ($\text{Score} = 0.0$).
  * **Execution Quality Component ($[0, 1]$):** Evaluates semantic route selection, tool choice appropriateness, argument correctness, execution fidelity, and user response quality.
* **Chaos Failure Injection:** Nightly CI/CD pipelines inject network partitions, tool schema changes, and model rate limits to verify automated recovery and circuit breaker behavior.

---

## 7. The Complete 48-Point Production Failure Register

| # | Failure Mode | Root Cause | JARVIS v1.0.0 Architectural Defense |
|---|---|---|---|
| **1** | **Infinite Graph Loop** | Cyclical routing or non-terminating retry loops | Hard `max_graph_steps` limit and cycle budgets in LangGraph Control Plane |
| **2** | **Doom Loop (Subagent Stagnation)** | Agent attempts alternate approaches without making progress | Progress Detector measuring real-world state delta $\Delta S$; terminates if $\Delta S = 0$ over $N$ turns |
| **3** | **Duplicate Side Effect** | Network timeout on tool call causes blind retry | Action Broker with `logical_effect_id` and pre-execution effect ledger |
| **4** | **False Positive Execution** | Tool returns success code, but external operation failed | External-State Verifier performs independent read-back query before issuing Receipt |
| **5** | **Direct Prompt Injection** | Malicious user input attempting to override system rules | Input trust normalization and immutable platform policy layer |
| **6** | **Indirect Prompt Injection** | Malicious payload embedded in web page, PDF, or email | Input Trust labeling (`EXTERNAL_UNTRUSTED`), `instruction_authority = NONE`, IFC firewall |
| **7** | **MCP Tool Poisoning** | Compromised MCP server injects exploits via tool descriptions | Tool Description Sanitizer, schema validators, and cryptographic definition pinning |
| **8** | **Memory Poisoning** | Adversary tricks agent into persisting false security rules | Memory Governance: `POLICY` memories writable only by users; confidence & provenance tracking |
| **9** | **Privilege Escalation** | Agent attempts to invoke administrative or destructive tools | Pre-execution Policy Engine enforcing capability manifests and dynamic invocation risk |
| **10** | **Confused Deputy Attack** | Trusted agent tricked into exfiltrating data via benign tools | Information-Flow Control (FIDES dual labels); data-flow tracking between trust domains |
| **11** | **Context Token Explosion** | Massive scraped web pages or terminal logs fill context | Dynamic artifact offloading to workspace filesystem based on token count and data type |
| **12** | **Context Contamination** | Subagents pollute global conversation with noisy traces | Isolated Subagent Contexts; only structured `SpecialistResult` returned to parent |
| **13** | **TOCTOU Approval Bypass** | Parameters or target resource state altered between human confirmation and execution | 5-stage Commit-Time Authorization flow: Pre-commit target state witness (`target_witness_hash`) re-verification and non-delegable `EffectAuthorization` token validation |
| **14** | **Stale Approval Exploitation** | Delayed approval executed long after environment changed | Short-lived TTL on approval tokens (Default: 5 minutes) |
| **15** | **Credential Leakage** | API keys or secrets dumped in logs, traces, or responses | Secret scrubbing middleware, regex redaction, and in-memory vault management |
| **16** | **Cross-User Data Leakage** | Shared memory cache leaking state between sessions | Strict session/tenant isolation namespaces across Vector DB and SQLite stores |
| **17** | **Browser Stale DOM Failure** | Web page structure changes mid-automation | Observe-Plan-Act-Verify loop; DOM mutation observers and visual screenshot diffs |
| **18** | **CAPTCHA / Bot Detection** | Cloudflare/reCAPTCHA blocking headless browser | Automatic detection; pauses task and notifies human via Blue HUD modal |
| **19** | **Sandbox Escape** | Arbitrary code breaks out of execution container | Multi-tier isolation (Docker $\rightarrow$ gVisor / Kata microVMs) with non-root execution |
| **20** | **Network Data Exfiltration** | Malicious script attempts to phone home secrets | Default-deny egress firewall with granular FQDN/port allowlisting and proxy inspection |
| **21** | **Primary Model Outage** | Primary LLM provider returns 5xx errors or outages | Model Gateway automatic fallback hierarchy validated by semantic compatibility contracts |
| **22** | **Tool / API Rate Limiting** | Rapid-fire requests triggering HTTP 429 Too Many Requests | Adaptive exponential backoff with full jitter and circuit breakers |
| **23** | **Retry Storms** | Multiple parallel subagents retrying simultaneously | Centralized circuit breakers (`CLOSED`, `OPEN`, `HALF_OPEN`) and token-bucket pacing |
| **24** | **Proactive Event Loop** | Scheduled event triggers action that re-triggers the event | Causal tracing headers (`causation_id`, `correlation_id`) and cycle detection graph |
| **25** | **Queue Worker Starvation** | Long-running deep tasks block immediate voice requests | Dedicated queue priority partitions (Priority 0: Voice/HUD vs Priority 1: Background) |
| **26** | **Lost Task on Crash** | Host machine powers off during multi-step operation | LangGraph PostgreSQL/SQLite checkpointer resumes exactly from last valid node |
| **27** | **State Schema Version Drift** | System upgraded while long-running task is in flight | Schema version headers and forward-compatible state migration handlers |
| **28** | **Concurrent Resource Conflict**| Parallel subagents mutate the same file or resource | Resource leasing and write-set locks managed by Workspace Manager |
| **29** | **Memory Race Condition** | Concurrent agents write conflicting facts to memory | Optimistic Concurrency Control with integer versioning and conflict resolvers |
| **30** | **Artifact Corruption** | Incomplete write or disk full error during artifact save | Atomic file writes (write to temp file, fsync, atomic rename) |
| **31** | **Capability Misrouting** | Simple math routed to expensive coding specialist | Router capability scoring based on intent embeddings and rule-based fast paths |
| **32** | **Over-Delegation Cascade** | Deep Agent spawns dozens of unnecessary subagents | Strict delegation budget per task (`max_subagents = 4`) |
| **33** | **Under-Delegation Monolith** | Specialist attempts to solve multi-domain problem alone | Task complexity classifier forcing decomposition when domain boundary > 1 |
| **34** | **Hallucinated Citations** | Model invents research URLs or claims false evidence | Full evidence verification: claim $\rightarrow$ source retrieval $\rightarrow$ passage match $\rightarrow$ citation |
| **35** | **Temporal Contradiction** | Outdated facts conflict with newer user instructions | Temporal supersession (`valid_until` set, `superseded_by` pointer populated) |
| **36** | **Cost & Token Runaway** | Recursive reasoning chain consumes massive API credits | Task-level dollar spend caps with automatic circuit breaker trips |
| **37** | **Voice Latency Bottleneck** | Waiting for full graph completion before speaking | Streaming audio chunks (`astream_events`) via Edge-TTS pipeline |
| **38** | **Silent Failure** | Background task fails with no user feedback | Mandatory `TaskReceipt` broadcasted over WebSocket to Blue HUD |
| **39** | **Telemetry Data Leak** | PII and auth tokens saved in trace spans | Field-level redaction filters on OpenTelemetry span exports |
| **40** | **Trajectory Evaluation Blindness**| Measuring only final answer instead of steps | Trajectory-level scoring (route, tool, args, policy, approval, execution, verify) |
| **41** | **Human Approval Fatigue** | Too many alerts cause user to approve blindly | Adaptive policy matrix: true low-risk actions auto-approved; only red-tier prompts |
| **42** | **Tool Schema Drift** | Third-party API changes its JSON payload format | Pydantic response parsing with contract tests run during nightly CI/CD |
| **43** | **Policy Regression Drift** | Security policy modified accidentally during refactoring | Policy drift regression test suite running in CI/CD pipeline |
| **44** | **Model Behavior Drift** | Model vendor updates weights silently, breaking code | Model version pinning (e.g., `gemini-1.5-pro-002`) instead of generic alias |
| **45** | **Duplicate Scheduled Runs** | Multiple worker instances fire the same cron job | Distributed scheduler locking with short-lived leases and heartbeats |
| **46** | **Non-Idempotent Tool Ambiguity**| Blind retry on non-idempotent tool after timeout | Tool classified as `NON_IDEMPOTENT`; halts and demands verification before retry |
| **47** | **Incompatible Model Fallback**| Fallback model fails due to differing tool syntax | Semantic Model Contract validation checking tool-calling compatibility |
| **48** | **Unbounded Memory Bloat** | Semantic memory fills with trivial chat banter | Importance and retention scoring filter discarding dialogue without persistent value |

---

## 8. The 20 Concrete Implementation Contracts & Schemas

To transition from architectural design into implementation, all subsequent engineering must conform to these 20 formal contracts:

```text
 01. LangGraph State Schema (JarvisState TypedDict)
 02. Task Lifecycle Schema (JarvisTask Pydantic Model with 14 States)
 03. Capability Manifest Schema (CapabilityManifest YAML/Pydantic)
 04. Agent Contract Interface (BaseSpecialist with isolated scratchpad)
 05. Tool Contract Interface (BaseTool with static & dynamic risk hooks)
 06. IFC Label Algebra (Integrity & Confidentiality propagation logic)
 07. Policy Engine DSL (Declarative YAML rules & risk scoring functions)
 08. EffectAuthorization Record Schema (Immutable TOCTOU token)
 09. ActionBroker Protocol (Idempotency ledger & execution interface)
 10. Idempotency Semantics (Logical effect ID vs attempt ID generation)
 11. Verification Contract (Semantic, evidence, and execution verifiers)
 12. Memory Schema & Optimistic Concurrency Protocol (Versioned SQL schema)
 13. Event Schema & Causation Header Specification (Event headers & tracing)
 14. Worker Lease Protocol (Redis/Postgres distributed locking contracts)
 15. Sandbox Contract (Docker/gVisor container execution interface)
 16. Component Lifecycle State Machine (Health probe & quarantine contracts)
 17. Telemetry & Trace Schema (OpenTelemetry span definitions & redaction)
 18. Trajectory Evaluation Schema (8-checkpoint scoring rubric)
 19. Failure-Injection Chaos Matrix (Fault injection harness contracts)
 20. Cancellation & Saga Compensation Protocol (Compensation plan interfaces)
```

---

## 9. The Master Implementation Roadmap (14 Stages)

Implementation proceeds strictly in order of foundational dependencies.

```text
 Stage 1: Core LangGraph State Machine, Task Models & Correlation IDs
    │
 Stage 2: Input Trust Classification, IFC Firewall & FIDES Dual-Labels
    │
 Stage 3: Capability Registry, Capability Firewall & Tool Manifests (MCP/A2A)
    │
 Stage 4: Centralized Policy Engine, Dynamic Risk Scoring & Autonomy Levels (0-5)
    │
 Stage 5: Action Broker, Idempotency Ledger, Sagas & 3-State Circuit Breakers
    │
 Stage 6: The 5 Default Capability Specialists & Component Lifecycle Manager
    │
 Stage 7: Deep Agents Harness Integration & Sandboxed Workspaces (gVisor/Kata)
    │
 Stage 8: Context Management & Dynamic Artifact Offloading
    │
 Stage 9: Governed Memory Plane with Optimistic Concurrency & Supersession
    │
 Stage 10: External-State Verification & Temporal Effect Receipt Minting
    │
 Stage 11: Proactive Event Bus with Distributed Leases, Cycle Graphs & DLQ
    │
 Stage 12: Dual-Layer Observability & Privacy-Preserving Telemetry
    │
 Stage 13: Continuous Evals, Trajectory Scoring & Chaos Failure Injection
    │
 Stage 14: Edge-TTS Voice Pipeline & Blue Holographic HUD
```

---
*End of Master Architecture Specification v1.0.0-CANONICAL. Frozen as the permanent engineering baseline for JARVIS-AI.*
