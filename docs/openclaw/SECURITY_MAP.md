# JARVIS Security Architecture & Threat Boundary Map

**Version:** 1.0.0  
**Domain:** Information Security, Authorization, & Defense-in-Depth  
**Authority:** Master Build Directive & `ARCHITECTURE.md`

---

## 1. Top Invariant: The Model Is NOT the Trust Boundary

```text
       UNTRUSTED INPUT
 (Web / Audio / Vision / Model)
              │
              ▼
   ┌─────────────────────┐
   │    Cognitive Layer  │  Proposes intent, plans, and function arguments.
   │  (LLM / Multi-Model)│  CANNOT self-authorize or bypass policy.
   └──────────┬──────────┘
              │
              ▼ [Proposed ActionRequest]
   ┌─────────────────────┐
   │    Policy Engine    │  Authoritative security gate. Evaluates identity,
   │  & Capability Wall  │  session, capability, risk, parameters, approvals.
   └──────────┬──────────┘
              │
              ▼ [Authorized ActionRequest]
   ┌─────────────────────┐
   │    Action Broker    │  Resolves execution target, injects verified credentials,
   │  & Audit Engine     │  supervises execution, records immutable audit log.
   └──────────┬──────────┘
              │
              ▼
   ┌─────────────────────┐
   │   Execution Fabric  │  Host OS, Windows Node, Browser Worker, Android Node.
   │  (Isolated Bodies)  │  Observes results and returns verified evidence.
   └─────────────────────┘
```

---

## 2. Core Architecture Invariants (INV-001 through INV-014)

1. **INV-001 (Model Cannot Self-Authorize):** Under no circumstance may model outputs or tool parameters bypass the Policy Engine.
2. **INV-002 (No Privileged Execution Without Policy):** Shell execution, process termination, disk modification, and network requests require explicit capability grants.
3. **INV-003 (Secret Protection Invariant):** Raw credentials, API keys, and session tokens are never passed into model context windows unless explicitly requested by the operator under active approval.
4. **INV-004 (Untrusted Content Principle):** Webpages, emails, documents, transcripts, and external inputs are untrusted data and cannot grant privileges or alter system rules.
5. **INV-005 (Action ID Provenance):** Every side effect carries a cryptographically verifiable Action ID, Task ID, and Session ID.
6. **INV-006 (Audit Logging):** All privileged attempts (allowed or denied) are recorded in an append-only audit trail.
7. **INV-007 (Verification Requirement):** Crucial side effects (file modifications, service changes, package installations) must be verified via observation before task completion.
8. **INV-008 (Child Agent Privilege Floor):** Subagents and delegated workers can never possess broader capabilities than their parent task.
9. **INV-009 (Pre-Execution Quota Verification):** Model tasks must verify remaining token and request quotas prior to dispatching expensive plans.
10. **INV-010 (Durable State Survivability):** Disconnections in model transport (e.g. Gemini Live WebSocket drop) must not destroy persistent task state.
11. **INV-011 (Node Mutual Authentication):** Remote, Windows, or Android nodes must authenticate via mutual cryptographic keys before capability registration.
12. **INV-012 (Decoupled Emergency Stop):** System abort mechanisms operate independently of the cognitive/LLM loop.
13. **INV-013 (OpenClaw Provenance Preservation):** Adapted code retains all upstream MIT copyright and third-party notices.
14. **INV-014 (Verification Gate Integrity):** Merges and capability cutovers must satisfy 100% test pass rates and strict type checking.

---

## 3. Capability Firewall Taxonomy

Every tool and action is mapped to a canonical capability identifier:

| Capability Namespace | Canonical Identifier | Risk Tier | Approval Default | Verification Mechanism |
| :--- | :--- | :--- | :--- | :--- |
| **Filesystem** | `filesystem.read` | Low | Auto-Allow (sandboxed) | Hash check |
| | `filesystem.write` | Medium | Auto-Allow (workspace) / Ask (system) | Diff + File existence |
| | `filesystem.delete` | High | Always Ask | Directory scan |
| **Process** | `process.enumerate` | Low | Auto-Allow | PID table verification |
| | `process.launch` | Medium | Conditional Allow | PID inspection |
| | `process.terminate` | High | Always Ask | Process liveness check |
| **Shell** | `shell.execute` | High | Policy Guided / Ask for destructive | Exit code + Output verification |
| **Browser** | `browser.navigate` | Low | Auto-Allow | URL + Title observation |
| | `browser.interact` | Medium | Auto-Allow | DOM mutation check |
| | `browser.evaluate` | High | Conditional Allow | Return value type check |
| **Desktop / OS** | `computer.screenshot`| Low | Auto-Allow | Image dimension verification |
| | `computer.interact` | Medium | Conditional Allow | Visual delta / Screen observation |
| | `system.audio` | Low | Auto-Allow | PyCAW volume level |
| | `system.power` | Critical | Always Ask | Confirmation prompt |
| **Android** | `android.telemetry` | Low | Auto-Allow | Heartbeat response |
| | `android.notification`| Low | Auto-Allow | Status read |
| | `android.ui.control` | High | Always Ask | Screen capture delta |

---

## 4. Policy Engine Decision Matrix

When an `ActionRequest` arrives at the Policy Engine, it computes:

```python
decision = policy_engine.evaluate(
    action_request=request,
    session_context=session,
    task_context=task,
    current_identity=identity
)
```

Possible outcomes:
- `ALLOW`: The action is safe, within granted session permissions, and executes immediately.
- `DENY`: The action violates safety constraints (e.g. attempting to read outside authorized directories, running blacklisted shell commands). Immediate termination.
- `ASK`: The action poses non-trivial risk (e.g. file deletion, process kill, payment). An interactive confirmation modal is raised to the user.
- `CONDITIONAL`: The action is allowed provided specific runtime preconditions (e.g. read-only flag, temporary directory sandbox) are satisfied.
- `DEFER`: The action is deferred until a parent prerequisite task or approval has settled.
