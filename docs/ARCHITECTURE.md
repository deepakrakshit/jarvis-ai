# JARVIS End-to-End System Architecture

**Status:** Technical Architecture Specification  
**System:** JARVIS Personal AI Operating System  
**Primary Realtime Engine:** Gemini 3.8 Live API  
**Primary Control Plane:** Python AsyncIO + LangGraph + Node.js Execution Substrate  

---

## 1. Architectural Overview

JARVIS is built as a stateful, multimodal personal operating system that integrates real-time cognitive reasoning with operating system execution capabilities.

```
                              ┌─────────────────────────┐
                              │          HUMAN          │
                              │  Voice / Video / Vision │
                              │   Screen / Chat / Files │
                              └────────────┬────────────┘
                                           │
                                           ▼
                              ┌─────────────────────────┐
                              │     GEMINI 3.8 LIVE     │
                              │                         │
                              │ Real-time voice stream  │
                              │ Visual perception       │
                              │ Tool call proposals     │
                              └────────────┬────────────┘
                                           │
                                           ▼
                     ╔══════════════════════════════════════════╗
                     ║              JARVIS CORE                 ║
                     ║                                          ║
                     ║ Gateway Daemon ── Session State Machine  ║
                     ║ Policy Engine ── Capability Firewall     ║
                     ║ Tool Action Broker ── Memory Fabric      ║
                     ╚═════════════════════╤════════════════════╝
                                           │
                     ┌─────────────────────┼─────────────────────┐
                     ▼                     ▼                     ▼
             Specialist Models     Specialist Agents     Execution Fabric
             GPT-OSS / Qwen /      Research / Code /     Native Windows /
             Gemma / Flash-Lite    Browser / Vision      Substrate Daemon
                     │                     │                     │
                     └─────────────────────┼─────────────────────┘
                                           ▼
                                ┌────────────────────────┐
                                │   EXECUTION DRIVERS    │
                                └────────────┬───────────┘
                                             │
                   ┌─────────────────────────┼─────────────────────────┐
                   ▼                         ▼                         ▼
           ┌──────────────┐          ┌──────────────┐          ┌──────────────┐
           │ Windows Node │          │ Browser Node │          │ Substrate    │
           │              │          │              │          │              │
           │ COM UIA Tree │          │ Playwright   │          │ JSON-RPC     │
           │ Win32 API    │          │ CDP Session  │          │ CUA Daemon   │
           │ Process Mgr  │          │ DOM Actions  │          │ Coordinate   │
           └──────────────┘          └──────────────┘          └──────────────┘
```

---

## 2. Core Subsystems

### 2.1 Real-Time Multimodal Cognitive Core
- **Gemini 3.8 Live Client (`gemini_live.py`):** Establishes low-latency bidirectional WebSockets sending 16kHz PCM mono audio and JPEG video frames while receiving synthesized assistant audio.
- **Acoustic Echo Cancellation Subsystem (`audio_aec.py`):** Runs a dedicated WASAPI loopback capture worker. Subtracts speaker playback from physical microphone audio using Partitioned Block Frequency Domain Adaptive Filtering (PBFDAF) and Magnitude Squared Coherence (MSC) spectral suppression.
- **Playback Activity Tracker (`output_tracker.py`):** Monitors physical hardware audio buffer drain to avoid voice barge-in collision while the assistant is speaking.

### 2.2 Control Plane & Gateway Protocol
- **Gateway Daemon (`gateway/`):** Runs a high-performance WebSocket server supporting typed wire contracts, connection authentication, ping/pong heartbeats, and JSON-RPC dispatch.
- **Cognitive Control Plane (`control_plane/`):** Coordinates multi-model task routing, state persistence across turns, conversation compaction, and tool dispatch.
- **Action Broker (`actions/broker.py`):** Decouples tool invocation requests from underlying driver implementations with strict parameter validation and timeout enforcement.

### 2.3 Execution Nodes & Automation
- **Windows Node (`execution/windows/`):** Native Windows automation integrating out-of-process COM Microsoft UI Automation (`UIAutomationCore.dll`), Win32 window management, and process lifecycle control.
- **Application Control Engine (`core/app_control_engine.py`):** Universal application coordinator implementing a deterministic provider hierarchy: Programmatic URI $\rightarrow$ COM UI Automation $\rightarrow$ Playwright DOM $\rightarrow$ Substrate CUA coordinate fallback.
- **Native Substrate Bridge (`execution/substrate_bridge.py`):** Manages a persistent Node.js daemon running `jarvis_substrate_runner.ts` over stdio JSON-RPC IPC to handle platform actions, screen snapshots, coordinate manipulation, and tool call repair.

---

## 3. Trust Boundary & Governance

JARVIS enforces the invariant that **the model is never the trust boundary**:
1. Cognitive models may generate tool calls, plans, and answers, but cannot execute side-effecting operations without passing through the local Capability Firewall.
2. Sensitive capabilities (e.g. file deletion, credential modification, external HTTP requests) require explicit policy elevation or user confirmation.
3. All operations run through structured observation and post-action verification before success is reported to the user or cognitive loop.
