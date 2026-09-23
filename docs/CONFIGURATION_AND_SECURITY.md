# JARVIS Configuration, Governance & Security Architecture

**Status:** Technical Architecture & Security Policy  
**Module:** `jarvis.config` & `jarvis.security`  

---

## 1. Core Invariant: Zero Hardcoding

JARVIS enforces a strict architectural invariant across all codebases and modules:
**Never hardcode paths, credentials, models, timeouts, or policy flags.**

### 1.1 Dynamic Discovery & Parameterization
- **Filesystem Paths:** All file and directory locations are dynamically resolved using `pathlib.Path` relative to project workspaces or configurable runtime settings.
- **Model Endpoints & Keys:** Model families and API keys are read from environment variables or secure credential storage (`.env`, Windows Credential Vault).
- **Timeouts & Gating Metrics:** Latency thresholds, audio buffer sizes, and polling intervals are configurable attributes within `jarvis.config.settings.Settings`.

---

## 2. Dynamic Settings Architecture

The configuration subsystem (`jarvis/src/jarvis/config/settings.py`) provides validated, typed configuration using environment variable overrides with sensible defaults:

```python
class Settings:
    # Model Configurations
    GEMINI_API_KEY: str = Field(default="", env="GEMINI_API_KEY")
    LIVE_MODEL_NAME: str = Field(default="gemini-2.0-flash-exp", env="LIVE_MODEL_NAME")
    FAST_MODEL_NAME: str = Field(default="gemini-2.0-flash", env="FAST_MODEL_NAME")

    # Gateway Networking
    GATEWAY_HOST: str = Field(default="127.0.0.1", env="GATEWAY_HOST")
    GATEWAY_PORT: int = Field(default=8765, env="GATEWAY_PORT")

    # Audio AEC & DSP Parameters
    VOICE_DRAIN_HOLD_MS: int = Field(default=250, env="VOICE_DRAIN_HOLD_MS")
    AEC_FRAME_SIZE: int = Field(default=256, env="AEC_FRAME_SIZE")
    AEC_STEP_SIZE: float = Field(default=0.08, env="AEC_STEP_SIZE")
```

---

## 3. Security Boundaries & Capability Firewall

### 3.1 Trust Zones
JARVIS categorizes operations into three risk tiers:
1. **Read-Only / Safe:** Screen inspection, window enumeration, reading public documentation, non-destructive queries. Executed autonomously without approval.
2. **Standard Side Effects:** Launching known applications, moving cursor, typing text, switching windows. Logged and monitored.
3. **High-Risk Operations:** File deletion, filesystem overwrites, running administrative shell scripts, modifying system configurations. Enforces explicit user confirmation or elevated approval tokens.

### 3.2 Private Runtime File Hygiene
Runtime session data, logs, database files, and credentials must never be committed to source control:
- `.env` and `*.key` files are strictly gitignored.
- `sessions.json`, `conversations.json`, `*.log`, and `*.sqlite` are isolated to untracked runtime directories.
- Unit tests utilize ephemeral mocks or in-memory fixtures to prevent filesystem pollution.
