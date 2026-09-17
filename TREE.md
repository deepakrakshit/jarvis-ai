# JARVIS AI Architecture & Repository Tree

```
====================================================================================================
JARVIS v1.0.0: Stateful Personal AI Operating System
Zero-Trust Architecture | Realtime Voice Plane | Centralized Governance | Decoupled Background Fabric
====================================================================================================
```

This document provides a comprehensive structural inventory and architectural description of every relevant file in the JARVIS-AI codebase.

---

## 1. Project Root Configuration & Documentation

```
.
├── .env.example                        # Configuration template documenting all required environment variables and API keys
├── .gitignore                          # Strict gitignore protecting private session logs, cache directories, and local databases
├── .pre-commit-config.yaml             # Pre-commit hook definitions enforcing ruff linting, ruff formatting, and mypy type checks
├── IMPLEMENTATION_ROADMAP.md           # Engineering implementation roadmap defining architecture layers and functional capabilities
├── pyproject.toml                      # Project metadata, dependencies, build system, and tool configs (ruff, mypy, pytest)
├── README.md                           # Project overview, core principles, runtime modes, and setup instructions
├── requirements.txt                    # Pinned Python package dependencies organized by functional domain
├── run.bat                             # Windows launcher script activating the virtual environment and booting the JARVIS CLI
├── TREE.md                             # Comprehensive repository structural tree and file-by-file architectural descriptions
```

---

## 2. Canonical Architecture Documentation (`docs/`)

```
docs/
├── ARCHITECTURE.md                     # Authoritative baseline architecture document detailing the zero-trust operating model
└── MODEL_ROSTER.md                     # Model routing registry defining primary, specialist, and fallback model tier assignments
```

---

## 3. Core JARVIS Operating System (`jarvis/`)

### 3.1 Top-Level Entry Points & Core Infrastructure

```
jarvis/
├── __init__.py                         # Root package initialization exporting version metadata
├── cli.py                              # Interactive Terminal Text CLI with runtime mode selector (Realtime Voice vs Text HUD)
├── orchestrator.py                     # Central LangGraph orchestrator coordinating stateful graph execution and agent delegation
├── voice_cli.py                        # Standalone Realtime Voice Plane interactive CLI connecting mic/speaker to Gemini Live
├── core/
│   ├── __init__.py                     # Core infrastructure package initialization
│   ├── config.py                       # Dynamic configuration manager driven by Pydantic Settings and environment variables
│   ├── exceptions.py                   # Domain exception hierarchy (GovernanceError, PolicyViolationError, ExecutionError)
│   ├── logging.py                      # Structured JSON logging via structlog with security redaction and correlation IDs
│   └── workflow.py                     # Deterministic LangGraph state machine builder with node transitions and checkpoint hooks
```

---

### 3.2 Specialist Agents Subsystem (`jarvis/agents/`)

Specialist worker agents executing domain-specific reasoning and task execution under decoupled orchestrator governance.

```
jarvis/agents/
├── __init__.py                         # Specialist agent package exports
├── base.py                             # Abstract BaseSpecialist defining lifecycle, system instructions, and state transformation
├── analysis.py                         # Data analysis and numerical reasoning specialist agent
├── coding.py                           # Software engineering, code generation, refactoring, and AST inspection specialist agent
├── computer.py                         # Windows OS automation, process control, and native UI automation specialist agent
├── personal.py                         # User profile, scheduling, calendar, and personal context specialist agent
├── research.py                         # Web research, document ingestion, factual synthesis, and search specialist agent
└── router.py                           # Deterministic intent router classifying requests to the optimal specialist agent node
```

---

### 3.3 Application Mounts & Chaos Engineering (`jarvis/apps/`, `jarvis/chaos/`)

Extension points for external interfaces, background workers, and resilience testing.

```
jarvis/apps/
├── __init__.py                         # Applications package initializer
├── api/
│   └── __init__.py                     # FastAPI REST/WebSocket server package for remote integration
├── hud/
│   └── __init__.py                     # Terminal Heads-Up Display rich formatting package
└── worker/
    └── __init__.py                     # Background asynchronous queue worker package

jarvis/chaos/
└── __init__.py                         # Chaos engineering failure-injection and resilience testing primitives
```

---

### 3.4 Action Broker Subsystem (`jarvis/core/broker/`)

Transactional execution engine enforcing commit-time authorizations, idempotency, leases, and compensations.

```
jarvis/core/broker/
├── __init__.py                         # Action Broker package exports
├── broker.py                           # Central ActionBroker verifying tokens, checking idempotency, and executing effects
├── circuit_breaker.py                  # Circuit breaker pattern preventing cascading failures to unstable downstream tools
├── lease.py                            # Mutual-exclusion lease manager preventing concurrent mutations on shared resources
├── ledger.py                           # Append-only transactional audit ledger recording all tool attempts, nonces, and results
├── retry.py                            # Exponential backoff with jitter retry strategy for transient external tool failures
├── saga.py                             # Distributed saga orchestrator coordinating rollback compensations on workflow failure
└── types.py                            # Pydantic models for action requests, execution records, risk types, and statuses
```

---

### 3.5 Capability Firewall & Registry (`jarvis/core/capabilities/`)

Dynamic capability management, trust scoping, and runtime manifest projection.

```
jarvis/core/capabilities/
├── __init__.py                         # Capabilities package exports
├── builtin.py                          # Declarative manifests for native capabilities (filesystem, clock, shell, web fetch)
├── firewall.py                         # CapabilityFirewall scoping and filtering tools based on caller trust and session permissions
├── loader.py                           # Manifest loader discovering tools from native modules, plugins, and MCP endpoints
├── manifest.py                         # CapabilityManifest schema defining risk classes, side effects, schemas, and trust levels
└── registry.py                         # CapabilityRegistry indexing active capabilities with cryptographic digest verification
```

---

### 3.6 Gateway, Model Adapters & Quota Management (`jarvis/core/gateway/`)

Multi-provider LLM gateway providing quota tracking, resilient failover, and bidirectional streaming.

```
jarvis/core/gateway/
├── __init__.py                         # Gateway package exports
├── google_adapter.py                   # Google GenAI adapter for standard text, structured outputs, and embeddings
├── google_realtime.py                  # High-performance WebSocket adapter for Gemini Live streaming audio, text, and tool calls
├── groq_adapter.py                     # Ultra-low-latency inference adapter for Groq Cloud (Qwen, Llama models)
├── interfaces.py                       # Abstract protocol contracts defining ModelAdapter and ModelGateway interfaces
├── mock_realtime.py                    # Mock realtime adapter for deterministic offline voice plane testing and CI pipelines
├── quota.py                            # Multi-tier quota tracker enforcing TPM, RPM, and daily token budgets with sliding windows
├── realtime.py                         # Realtime event types (LiveEvent, LiveEventType, LiveToolCall, LiveToolResponse, LiveAudioChunk)
└── router.py                           # Intelligent model router selecting models based on domain, latency, and quota health
```

---

### 3.7 Information Flow Control Subsystem (`jarvis/core/ifc/`)

Fine-grained security tracking data confidentiality, integrity, and provenance across agent boundaries.

```
jarvis/core/ifc/
├── __init__.py                         # Information Flow Control package exports
├── labels.py                           # Security lattice labels defining Confidentiality and Integrity classifications
├── provenance.py                       # Provenance tracker recording origin, trust history, and transformations of data
├── rules.py                            # Information flow rules enforcing non-elevation and sink constraints
├── sanitizer.py                        # Text sanitizer neutralizing prompt injection attacks and redacting sensitive data
├── sinks.py                            # Sink definitions (filesystem write, shell execute, LLM prompt) with security constraints
└── taint.py                            # Dynamic taint tracker tagging untrusted inputs and detecting dangerous data flows
```

---

### 3.8 Task Lifecycle Subsystem (`jarvis/core/lifecycle/`)

State machine managing task progression, lifecycle transitions, and execution timeouts.

```
jarvis/core/lifecycle/
├── __init__.py                         # Task lifecycle package exports
├── manager.py                          # LifecycleManager tracking task state transitions and enforcing execution timeouts
└── types.py                            # Task state models and state transition enums (PENDING, RUNNING, COMPLETED, FAILED, CANCELLED)
```

---

### 3.9 Centralized Policy Engine & Governance (`jarvis/core/policy/`)

Central security authority evaluating all tool invocations, autonomy boundaries, and Human-in-the-Loop requirements.

```
jarvis/core/policy/
├── __init__.py                         # Policy subsystem exports
├── decision.py                         # Decision schemas (PolicyDecision, PolicyDecisionType, AutonomyLevel, EffectAuthorization)
├── dsl.py                              # Domain Specific Language parser compiling declarative policy rules
├── engine.py                           # Central PolicyEngine evaluating capability invocations against autonomy and risk
├── hitl.py                             # Human-In-The-Loop pipeline managing interactive approval requests and cryptographic tokens
├── risk.py                             # Dynamic risk scoring engine calculating composite risk scores based on resource sensitivity
└── simulator.py                        # Policy simulator dry-running hypothetical tool calls to verify policy correctness
```

---

### 3.10 Queue, Secrets & Session Management (`jarvis/core/queue/`, `jarvis/core/secrets/`, `jarvis/core/session/`)

Asynchronous event queuing, credential encryption, and conversation session persistence.

```
jarvis/core/queue/
├── __init__.py                         # Queue package exports
└── in_memory.py                        # In-memory async priority queue with backpressure and task deduplication

jarvis/core/secrets/
├── __init__.py                         # Secrets package exports
└── vault.py                            # In-memory secure credential vault masking secrets and protecting API tokens

jarvis/core/session/
├── __init__.py                         # Session management exports
├── models.py                           # Session state schemas, turn history models, and checkpoint metadata
└── session_manager.py                  # Session orchestrator persisting conversations, task states, and checkpoint restoration
```

---

### 3.11 State, Telemetry & Zero-Trust Taxonomy (`jarvis/core/state/`, `jarvis/core/trust/`)

Immutable agent state structures, execution budgets, and trust classification models.

```
jarvis/core/state/
├── __init__.py                         # State management exports
├── budget.py                           # Computational and token budget tracker preventing unbounded recursive execution loops
├── cancellation.py                     # Thread-safe cancellation token managing cooperative task aborts across async boundaries
├── correlation.py                      # Distributed correlation ID injector ensuring end-to-end telemetry traceability
├── exceptions.py                       # State-specific exception classes
├── graph.py                            # LangGraph state schema, channels, and reducer functions for the orchestrator
├── logging.py                          # State transition audit loggers recording graph node traversals
├── state.py                            # Primary immutable agent state dictionary definition
├── status.py                           # Runtime operational status indicators and progress metrics
└── task.py                             # Internal task representation and execution metadata models

jarvis/core/trust/
├── __init__.py                         # Zero-trust taxonomy exports
├── classifier.py                       # Real-time trust classifier evaluating provenance of input sources
├── delimiters.py                       # Cryptographic and semantic boundary delimiters neutralizing prompt injection attacks
└── taxonomy.py                         # TrustLevel hierarchy (USER_INPUT, SYSTEM_POLICY, VERIFIED_INTERNAL, EXTERNAL_UNTRUSTED)
```

---

### 3.12 Realtime Voice Plane Subsystem (`jarvis/core/voice/`)

Bidirectional 16kHz audio streaming, transparent GoAway connection rotation, and decoupled background task coexistence.

```
jarvis/core/voice/
├── __init__.py                         # Realtime Voice Plane package exports
├── audio_io.py                         # Hardware audio capture (mic) and playback (speaker) interface via sounddevice
├── session_manager.py                  # LiveSessionManager handling session resumption tokens, GoAway rotation, and task attachment
├── task_manager.py                     # BackgroundTaskManager enabling long-running specialist tasks without blocking voice dialogue
├── telemetry.py                        # Realtime voice telemetry recording latency, token budgets, audio duration, and reconnections
├── tool_bridge.py                      # LiveToolBridge providing zero-trust tool projection, intent boundary gating, and Action Broker execution
└── voice_agent.py                      # LiveVoiceAgent coordinating Gemini Live WebSocket events, speech synthesis, and barge-in interruptions
```

---

### 3.13 Security Policies, Sandbox & Storage (`jarvis/policies/`, `jarvis/sandbox/`, `jarvis/storage/`)

Baseline security rules, subprocess containment, and SQLite database storage.

```
jarvis/policies/
├── __init__.py                         # Policy files package initializer
└── default_policies.yaml               # Declarative baseline security policies, risk thresholds, and mandatory HITL rules

jarvis/sandbox/
├── __init__.py                         # Sandbox package exports
└── runner.py                           # Local subprocess sandbox isolating untrusted commands in constrained execution environments

jarvis/storage/
├── __init__.py                         # Storage package exports
├── db.py                               # Async SQLite database connection manager and schema initialization
└── task_store.py                       # Persistent task repository saving task records, execution logs, and statuses
```

---

### 3.14 Native Capabilities Implementation (`jarvis/tools/native/`)

High-trust built-in tools providing safe execution for filesystem, clock, math, shell, system, and web access.

```
jarvis/tools/native/
├── __init__.py                         # Native tool exports and dispatch_native_tool central dispatcher
├── calculator.py                       # Safe AST-based mathematical expression evaluator
├── citation.py                         # Native citation verification tool executing multi-provider bibliographic validation
├── clock.py                            # Truthful system clock provider returning local, UTC, and timezone timestamps
├── code.py                             # Governed host Python test execution tool with workspace validation, secret scrubbing, and non-idempotent HITL policy
├── filesystem.py                       # Confined filesystem tools (read_file, write_file, list_dir, delete_file) enforcing workspace root boundaries
├── shell.py                            # Async shell command execution tool bounded by timeout and policy gating
├── system.py                           # Diagnostic tool reporting CPU, RAM, disk, and process metrics via psutil
└── web.py                              # Safe async web scraper and web search integration tool
```

---

### 3.15 Verification & Citation Integrity Subsystem (`jarvis/core/verification/`)

General data-driven bibliographic verification, multi-source metadata reconciliation, and evidence-grounded claim calibration engine with zero hardcoded publication facts.

```
jarvis/core/verification/
├── __init__.py                         # Verification package exports and public validation facade
├── citation.py                         # Citation integrity and claim calibration verifier routing to dynamic providers
├── evidence.py                         # Claim-evidence support auditor verifying factual entailment and quantitative assertions
├── reconciler.py                       # Multi-source metadata reconciliation engine detecting conflicts across providers
├── records.py                          # Canonical data contracts (CitationRecord, CitationAuthor, ValidationState, CitationValidationResult)
├── validator.py                        # Authoritative CitationValidator orchestrating concurrent provider lookups and verification rules
└── providers/
    ├── __init__.py                     # Authoritative provider package exports
    ├── base.py                         # Abstract BaseCitationProvider, normalize_doi parser, and compute_metadata_digest
    ├── crossref.py                     # Crossref REST API authoritative provider adapter
    ├── doi_negotiation.py              # DOI HTTP Content Negotiation (CSL-JSON) authoritative provider adapter
    ├── openalex.py                     # OpenAlex Scholarly Graph API authoritative provider adapter
    ├── publisher_html.py               # Publisher HTML metadata extractor with interstitial/redirect filtering
    └── pubmed.py                       # NCBI Entrez E-utilities (esearch/esummary) biomedical provider adapter
```

---

## 4. Verification Test Suite (`tests/`)

Complete automated test suite enforcing 100% test pass rates and strict type safety across all architectural domains.

```
tests/
├── conftest.py                         # Global pytest fixtures (mock gateway, sample policies, isolated workspace directories)
├── __init__.py                         # Test suite root initializer
├── fixtures/
│   └── citation_cases/
│       ├── landmark_fixtures.json      # Verified publication test fixtures for dynamic resolution and negative edge cases
│       └── regression_cases.json       # Regression cases for author fabrication (Rajkomar, Liu) and year-upgrading detection
├── contract/
│   ├── __init__.py                     # Contract test package initializer
│   └── model_adapters/
│       └── test_gateway_interfaces.py  # Contract tests verifying all model adapters adhere to the ModelAdapter protocol
├── integration/
│   ├── __init__.py                     # Integration test package initializer
│   ├── broker/
│   │   ├── test_action_broker.py       # Integration tests for Action Broker execution, tokens, and ledger commits
│   │   ├── test_ambiguous_outcome_defense.py # Integration tests verifying resilience against ambiguous tool outcomes
│   │   └── test_cancellation_race.py   # Integration tests verifying race condition resilience during mid-action task cancellations
│   ├── database/
│   │   ├── test_sqlite_db.py           # Integration tests for SQLite database lifecycle and schema integrity
│   │   └── test_task_store.py          # Integration tests for persistent task creation, status updates, and retrieval
│   ├── gateway/
│   │   └── test_live_api_keys.py       # Integration tests verifying provider API connectivity and fallback paths
│   ├── ifc/
│   │   └── test_adversarial_fixtures.py # Integration tests evaluating taint propagation through malicious input payloads
│   ├── langgraph/
│   │   ├── test_budget_exhaustion.py   # Integration tests verifying graph halt upon token budget depletion
│   │   ├── test_cancellation_flow.py   # Integration tests for cooperative task cancellation across graph nodes
│   │   ├── test_checkpoint_resume.py   # Integration tests verifying checkpoint persistence and session restoration
│   │   ├── test_empty_graph.py         # Edge-case tests verifying resilient handling of empty or uninitialized graphs
│   │   └── test_task_graph.py          # Full multi-node execution tests of the primary LangGraph orchestration graph
│   ├── policy/
│   │   ├── test_centralized_policy_gate.py # Integration tests ensuring all tool paths pass through the Centralized Policy Engine
│   │   └── test_dialog_forging_defense.py # Security tests defending against synthesized conversational turns attempting privilege escalation
│   ├── session/
│   │   └── test_orchestrator_real_tasks.py # End-to-end integration tests of orchestrator executing real background workflows
│   └── voice/
│       ├── __init__.py                 # Voice integration test package initializer
│       └── test_live_real_api.py       # Integration tests connecting directly to the real Gemini Live WebSocket API endpoint
└── unit/
    ├── __init__.py                     # Unit test package initializer
    ├── agents/
    │   └── test_specialists.py         # Unit tests for specialist agent system prompts and execution boundaries
    ├── broker/
    │   ├── test_circuit_breaker.py     # Unit tests for circuit breaker trip, reset, and half-open states
    │   ├── test_lease.py               # Unit tests for mutual-exclusion lease acquisition, expiration, and renewal
    │   ├── test_ledger.py              # Unit tests for transactional ledger append, query, and cryptographic hash verification
    │   ├── test_retry.py               # Unit tests for exponential backoff and retry policy behavior
    │   ├── test_saga.py                # Unit tests for distributed saga execution and compensating rollback sequences
    │   └── test_types_and_state_machine.py # Unit tests for action request models and state machine transitions
    ├── capabilities/
    │   ├── test_firewall.py            # Unit tests for CapabilityFirewall trust-based projection and scoping
    │   ├── test_loader.py              # Unit tests for dynamic capability manifest loading and validation
    │   ├── test_manifest.py            # Unit tests for CapabilityManifest schemas, risk classes, and side effects
    │   ├── test_registry.py            # Unit tests for CapabilityRegistry indexing and digest verification
    │   └── test_specialist_stubs.py    # Unit tests verifying specialist tool schemas and stub handlers
    ├── config/
    │   └── test_config.py              # Unit tests for environment variable loading, validation, and defaults
    ├── gateway/
    │   ├── test_adapters_mocked.py     # Unit tests for Google GenAI, Groq, and OpenAI adapters with mock responses
    │   └── test_quota.py               # Unit tests for sliding-window token and request rate limiters
    ├── ifc/
    │   ├── test_labels.py              # Unit tests for security label comparison and lattice ordering
    │   ├── test_non_elevation.py       # Unit tests verifying that untrusted inputs cannot elevate privilege
    │   ├── test_provenance.py          # Unit tests for data origin tracking and transformation history
    │   ├── test_sanitizer.py           # Unit tests for prompt injection neutralization and sensitive data scrubbing
    │   ├── test_sinks.py               # Unit tests for sink constraints and security policy enforcement
    │   └── test_taint.py               # Unit tests for dynamic taint propagation across operations
    ├── lifecycle/
    │   └── test_lifecycle.py           # Unit tests for task state machine transitions, timeouts, and cancellation
    ├── policy/
    │   ├── test_dsl.py                 # Unit tests for policy domain-specific language parsing and evaluation
    │   ├── test_engine.py              # Unit tests for PolicyEngine autonomy evaluation and decision outcomes
    │   ├── test_hitl.py                # Unit tests for Human-in-the-Loop pipeline, approval tokens, and expirations
    │   ├── test_risk.py                # Unit tests for dynamic risk calculation and sensitive target weighting
    │   └── test_simulator.py           # Unit tests for policy simulator dry-runs and impact analysis
    ├── queue/
    │   └── test_in_memory_queue.py     # Unit tests for in-memory priority queue capacity, ordering, and deduplication
    ├── sandbox/
    │   └── test_local_sandbox.py       # Unit tests for subprocess isolation and command execution containment
    ├── secrets/
    │   └── test_vault.py               # Unit tests for in-memory secret vault encryption, retrieval, and key masking
    ├── session/
    │   └── test_session_manager.py     # Unit tests for session context serialization, persistence, and resumption
    ├── state/
    │   ├── test_budget.py              # Unit tests for token budget decrementing and depletion detection
    │   ├── test_cancellation.py        # Unit tests for asynchronous cancellation token propagation
    │   ├── test_exceptions.py          # Unit tests for state exception serialization and error hierarchies
    │   ├── test_logging.py             # Unit tests for state transition structured audit logging
    │   ├── test_status.py              # Unit tests for runtime health indicators and status reporting
    │   └── test_task.py                # Unit tests for internal task representations and metadata tracking
    ├── tools/
    │   ├── test_citation_integrity.py  # Unit tests for zero-fabrication, anti-year-upgrading rules, and epistemic claim calibration
    │   ├── test_code_execution_governance.py # Unit tests for governed Python test runner, path confinement, traversal prevention, argument sanitization, secret scrubbing, and blocked shell recovery loops
    │   ├── test_filesystem_size_semantics.py # Unit tests verifying exact on-disk byte counting and CRLF/LF normalization
    │   ├── test_native_tools_resilience.py # Unit tests for native filesystem, clock, and calculator resilience
    │   └── test_shell_command_preservation.py # Unit tests for safe cross-platform shell execution and parameter safety
    ├── trust/
    │   ├── test_classifier.py          # Unit tests for input source trust classification
    │   ├── test_delimiters.py          # Unit tests for semantic delimiter enforcement against prompt injection
    │   └── test_taxonomy.py            # Unit tests for trust taxonomy ordering and inheritance
    └── voice/
        ├── test_canonical_tool_projection.py # Unit tests for dynamic tool projection, least-privilege scoping, and workspace containment
        ├── test_continuous_conversation_e2e.py # End-to-end unit tests verifying conversational flow during background task execution
        ├── test_flash_pool_protection.py # Unit tests verifying that voice turns never exhaust primary text inference quotas
        ├── test_live_session_manager.py # Unit tests for session resumption handles, task attachment, and GoAway rotation
        ├── test_live_tool_bridge.py    # Unit tests for tool bridge manifest registration, policy evaluation, and deduplication
        ├── test_realtime_adapter.py    # Unit tests for RealtimeAdapter contracts, event streaming, and mock adapter behavior
        ├── test_security_chain_authority.py # Unit tests verifying that natural language intent cannot override the zero-trust chain
        ├── test_user_intent_guard.py   # Unit tests verifying that read-only user requests strictly prohibit unsolicited file mutations
        ├── test_voice_chaos.py         # Chaos tests for tool call floods, policy blocks, mid-session GoAway rotation, and worker exceptions
        └── test_voice_task_coexistence.py # Unit tests for concurrent dialogue, milestone progress throttling, and task cancellation
```

---

## 5. Private Runtime, Scratch & Ignored Artifacts (Excluded from Version Control)

The following files and directories are generated at runtime, contain private session information or cached compilation artifacts, and are strictly excluded from version control per Core Invariant Rule 3:

* `scratch/` — Local temporary audit runners, live telemetry JSON, and exploratory experiment files (strictly gitignored)
* `.git/` — Git version control internal data and history
* `.venv/` — Virtual environment containing third-party Python packages and binaries
* `__pycache__/` — Python bytecode cache files (`*.pyc`)
* `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` — Test runner and static analysis caches
* `.coverage`, `coverage.xml` — Code coverage reporting artifacts
* `.env` — Private environment configuration containing live API keys and credentials
* `conversations.json`, `sessions.json` — Local runtime user conversation checkpoints and session state
* `*.log`, `*.jsonl` — Local execution logs and streaming telemetry dumps
* `jarvis.egg-info/` — Local editable package installation metadata generated by pip
