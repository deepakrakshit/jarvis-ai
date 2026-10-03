# 🛠️ Contributing to JARVIS

Thank you for your interest in contributing to **JARVIS — Personal AI Operating System for Windows**!

JARVIS is built as an open, stateful, multimodal cognitive control plane. To maintain architectural purity, performance, and reliability, all contributions must strictly adhere to the standards and invariants outlined below.

---

## 🎯 Where to Start

You do not need to understand the entire architecture to make meaningful contributions:
* 🟢 **Bite-Sized & Accessible Tasks:** Browse open **[Good First Issues](https://github.com/deepakrakshit/jarvis-ai/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)**.
* 🙋 **Domain-Specific Challenges:** Explore **[Help Wanted](https://github.com/deepakrakshit/jarvis-ai/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22)** issues for DSP audio, WebRTC, UIA automation, WebGL HUD, and memory subsystems.
* 💡 **Proposals & RFCs:** Discuss architectural enhancements in **[GitHub Discussions](https://github.com/deepakrakshit/jarvis-ai/discussions)** or file an **[Issue](https://github.com/deepakrakshit/jarvis-ai/issues)**.

---

## 🏛️ Core Engineering Invariants

Every commit, pull request, and contribution must uphold the project's non-negotiable core invariants:

### 1. Top-Most Invariant: Never Hardcode Anything
* **Dynamic Configuration:** Never hardcode credentials, tokens, API endpoints, fixed filesystem paths, timeouts, environment flags, models, or policies.
* **Extensibility & Discovery:** Everything must be parameterized, discoverable, and driven by environment variables, configuration schemas, or runtime context.
* **Platform Portability:** Avoid OS-specific hardcoding (e.g. hardcoded Unix shell utilities like `rm` on Windows, or hardcoded path separators); always use cross-platform standards such as `pathlib.Path`.

### 2. Strict Communication & Commit Standards
* **Conventional Commits:** All Git commit messages must follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:
  * `feat(subsystem): add new capability`
  * `fix(voice): resolve audio buffer drain race condition`
  * `refactor(gateway): decouple connection state management`
  * `test(telephony): add WebRTC barge-in simulation tests`
  * `docs(architecture): update audio pipeline blueprint`
* **Domain-Driven Progression:** Document engineering progress by functional capability and architectural domain. Never reference progression milestones with the prohibited progression term.

### 3. Data Safety & Privacy Invariant
* Never delete or modify user files, databases, or project assets without explicit operator authorization.
* Keep private runtime session files (`sessions.json`, `conversations.json`, `*.log`, `*.jsonl`, `.env`, `coverage.xml`) strictly gitignored and never committed into version control.

### 4. Mandatory Quality Gates
A pull request must pass all configured quality gates before merge:
1. **Tests:** `pytest` passes with zero failures.
2. **Strict Type Safety:** `mypy src tests` passes with zero errors.
3. **Linting & Formatting:** `ruff check .` and `ruff format --check .` pass with zero violations.

---

## 💻 Development Environment Setup

### Prerequisites
- **Operating System:** Windows 10 or Windows 11 (64-bit)
- **Python:** Version 3.10 or higher
- **Node.js:** Version 18 LTS or higher (with npm)
- **Git:** Git for Windows (configured with LF/CRLF awareness)

### 1. Clone the Repository
```powershell
git clone https://github.com/deepakrakshit/jarvis-ai.git
cd jarvis-ai
```

### 2. Configure Python Virtual Environment
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

### 3. Install Substrate Node Dependencies
```powershell
cd substrate\extensions\whatsapp
npm install
npm run build
cd ..\..\..
```

### 4. Configure Environment
Copy the sample environment file and configure required keys:
```powershell
Copy-Item .env.example .env
```
Populate `.env` with your `GEMINI_API_KEY` and optional provider tokens.

---

## 🧪 Running Quality Gates

Before submitting any code or creating a pull request, run the local quality verification pipeline:

```powershell
# 1. Run Python linter
ruff check .

# 2. Check code formatting
ruff format --check .

# 3. Verify strict type safety
mypy src tests

# 4. Run automated test suite
pytest
```

To auto-format Python code according to project style:
```powershell
ruff format .
```

---

## 🧭 Contributor Workflow

A typical contribution should stay small and easy to review:

1. Pick one open issue and comment that you are working on it.
2. Create a focused branch from `main`.
3. Make the smallest coherent change that solves the issue.
4. Run the local quality gates.
5. Open a pull request and include verification evidence.
6. Respond to review feedback and keep the PR focused.

For questions, design proposals, or subsystem discussions, use GitHub Discussions once enabled for the repository.

## 📂 Repository Architecture Overview

```text
├── frontend/                     # Neural Web UI (WebGL Orbs, Realtime Audio & Typography)
│   ├── css/                      # Responsive CSS & viewport styling
│   ├── js/                       # Core frontend logic (Gateway client, Web Audio player, Orbs)
│   └── index.html                # Dedicated desktop Neural UI entry point
├── src/jarvis/                   # Python Cognitive Kernel & Control Plane
│   ├── cli/                      # Command-line interface & interactive live host
│   ├── cognition/                # Gemini 3.8 Live bridge, Model Router, Specialist Subagents
│   ├── core/                     # Control Plane, Action Broker, Cron Scheduler, Interfaces
│   ├── execution/                # Multi-Provider Execution Fabric (Windows COM UIA, Browser, WhatsApp)
│   ├── gateway/                  # WebSocket Daemon (ws://127.0.0.1:8765) & Protocol
│   ├── memory/                   # SQLite FTS5 Memory Engine, Standing Intents, Context Engine
│   ├── policy/                   # Policy Engine, Capability Firewall & Approval Manager
│   ├── storage/                  # SQLite WAL Database Engine & Schema Migrations
│   ├── telegram/                 # Native Telegram Remote Control Daemon & Live Session
│   └── voice/                    # WASAPI Loopback DSP Echo Canceller (PBFDAF/AEC) & Audio Engine
├── substrate/                    # High-Performance Node.js Execution Substrate
│   └── extensions/whatsapp/      # Autonomous WhatsApp VoIP WebRTC Telephony Node
├── tests/                        # Comprehensive Pytest Suite
└── docs/                         # Specialized Architectural Deep-Dive Manuals
```

---

## 🔀 Pull Request Process

1. **Branch Naming:** Create a focused feature branch from `main`:
   ```bash
   git checkout -b feat/audio-barge-in-refinement
   ```
2. **Commit Hygiene:** Maintain atomic, well-described Conventional Commits.
3. **PR Description:** Fill out the [Pull Request Template](.github/PULL_REQUEST_TEMPLATE.md) completely, including verification evidence and test output.
4. **Review & Merge:** Address review feedback promptly. Keep discussions technical, objective, and solution-focused.
