# Testing and Continuous Integration (CI)

This document outlines the testing strategy, test suites, quality gates, and Continuous Integration pipeline enforcing JARVIS software reliability.

---

## 1. Quality Gates and Standards

The reproducible local checks are driven by the committed runtime pins and lockfiles. Code changes should satisfy these quality gates before being merged:

1. **Offline regression suite**: `make test` must pass without network access, provider credentials, browser binaries, or audio hardware. It excludes tests marked `browser`, `audio`, or `live`.
2. **Strict Type Safety**: `make typecheck` runs `mypy src tests` under strict mode.
3. **Clean Linting and Formatting**: `make lint` and `make format-check` must report zero warnings or reformatting suggestions.
4. **Frontend Syntax Verification**: `make js-check` checks every committed JavaScript client file.
5. **Optional host suites**: `make test-all` covers browser/audio and live host behavior when the matching runtime is installed.

---

## 2. Test Architecture

The JARVIS test suite is organized into distinct functional layers:

```
tests/
├── unit/                    # Fast, isolated tests of pure logic and algorithms
│   ├── test_config.py       # Configuration schema, environment variable overrides, defaults
│   ├── test_aec.py          # AEC DSP mathematical correctness, filter stability, DTD
│   ├── test_voice.py        # Voice state machine transitions, barge-in logic, VAD
│   ├── test_router.py       # Model router complexity scoring, provider candidate chains
│   ├── test_memory.py       # SQLite FTS5 search queries, temporal decay ranking formula
│   └── test_policy.py       # Capability firewall bitmasks, risk tier evaluation
├── integration/             # Multi-subsystem tests with mocked network endpoints
│   ├── test_control_plane.py# Full turn execution cycle: turn input -> model -> tool -> turn output
│   ├── test_gateway.py      # WebSocket client-server handshake, frame serialization, RPC
│   ├── test_substrate.py    # Python-to-TypeScript stdio JSON-RPC IPC bridge
│   └── test_database.py     # SQLite schema migrations and concurrent WAL read/writes
└── security/                # Penetration and boundary enforcement tests
    ├── test_firewall.py     # Forbidden path access, unauthorized capability elevation
    └── test_sanitization.py # Prompt injection defense and Markdown/HTML entity escaping
```

---

## 3. Running Local Verification Checks

### 3.1. Running the Offline Regression Suite

```bash
# Create the local environment from the committed lockfiles first.
make install

# Headless/offline-capable regression suite.
make test

# Run all tests, including browser and audio suites.
make test-all
```

### 3.2. Code Quality and Type Checking

```bash
# Check code formatting (dry run)
make format-check

# Auto-format all Python code
ruff format .

# Run linter checks
make lint

# Run linter checks with automated safe fixes
ruff check --fix .

# Run strict static type checking
make typecheck
```

### 3.3. Frontend Client Syntax Checks

```bash
# Verify JavaScript syntax across all client modules
make js-check
```

---

## 4. Continuous Integration Pipeline

JARVIS uses GitHub Actions (`.github/workflows/ci.yml`) on Ubuntu and Windows. Both runners read `.python-version` and `.nvmrc`, install from `uv.lock` and `substrate/package-lock.json`, then run the same headless regression suite and JavaScript syntax checks.

The workflow intentionally leaves browser, audio, and credential-backed live checks out of the offline gate. Run those suites on a host with the corresponding runtime and credentials:

```bash
make test-all
```

---

## 5. Guidelines for Writing Tests

### 5.1. Determinism and Zero External Network Calls
Unit and integration tests must never depend on live external network endpoints or live cloud APIs (such as live Gemini or Groq endpoints).
- Use `unittest.mock` or `pytest-mock` to stub HTTP and WebSocket client responses.
- Test failure modes, unexpected network disconnects, and JSON decoding errors explicitly.

### 5.2. Async Test Conventions
JARVIS relies heavily on `asyncio`. Tests targeting coroutines must be marked with `@pytest.mark.asyncio`:

```python
import pytest
from jarvis.memory.working import WorkingMemory
from jarvis.contracts.memory import TurnRecord


@pytest.mark.asyncio
async def test_working_memory_append_and_truncate():
    memory = WorkingMemory(capacity=5)
    for i in range(10):
        await memory.add_turn(TurnRecord(role="user", content=f"msg {i}"))

    turns = await memory.get_recent_turns()
    assert len(turns) == 5
    assert turns[-1].content == "msg 9"
```

### 5.3. Cross-Platform Path Handling
Always use `pathlib.Path` or `tmp_path` fixtures rather than hardcoded path strings with backslashes or forward slashes to ensure tests pass on both Windows and Linux environments.
