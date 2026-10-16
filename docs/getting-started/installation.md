# Installation & System Requirements

This document provides a verified, step-by-step installation guide for setting up the **JARVIS Personal AI Operating System** on Linux or Windows.

---

## 1. System Requirements & Prerequisites

JARVIS provides a portable Python/server baseline and optional native desktop integrations. Use the following pinned runtime and host prerequisites:

| Requirement | Minimum Specification | Recommended Specification | Notes |
| :--- | :--- | :--- | :--- |
| **Operating System** | Linux x64 or Windows 10/11 x64 | Linux x64 or Windows 11 x64 | Windows is required for COM/UI Automation and PowerShell; the server and tests run on Linux. |
| **Python** | Python 3.14.4 | Python 3.14.4 | The exact version is recorded in [`.python-version`](../../.python-version). |
| **Node.js** | Node.js 22.14.0 | Node.js 22.14.0 | The exact version is recorded in [`.nvmrc`](../../.nvmrc). |
| **Audio Hardware** | Optional for server checks | Microphone and speakers for voice features | PortAudio and host audio support are required only for audio tests and live voice. |
| **Web Browser** | Optional for server checks | Chromium, Chrome, or Edge | Required only for Playwright browser tests and the dedicated Neural UI. |
| **Git** | Git 2.35+ | Git (latest) | Required for version control and submodule resolution. |

---

## 2. Environment Setup Steps

### Step 1: Clone the Repository
Clone `deepakrakshit/jarvis-ai` from a shell:

```bash
git clone https://github.com/deepakrakshit/jarvis-ai.git
cd jarvis-ai
```

### Step 2: Create and Activate Python Virtual Environment
Always install JARVIS in an isolated virtual environment to prevent package collisions:

```bash
# Python 3.14.4 must be available on PATH.
python3.14 -m venv .venv
source .venv/bin/activate
```

On Windows, use the pinned interpreter and the environment-local executable:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --requirement requirements.lock
```

> [!NOTE]
> If script execution is restricted on Windows, allow current-user execution via:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

### Step 3: Install the committed Python lock

```bash
python -m pip install --requirement requirements.lock
```

The lock contains core runtime libraries and quality tools, including the platform-conditional Windows packages where the host supports them.

### Step 4: Install the locked core substrate dependencies

```bash
npm --prefix substrate ci --ignore-scripts
```

### Step 5: Install Optional Substrate Extension Dependencies (WhatsApp VoIP)
If utilizing autonomous WhatsApp voice telephony or the Baileys WebRTC bridge:

```bash
npm --prefix substrate/extensions/whatsapp ci
npm --prefix substrate/extensions/whatsapp run build
```

### Step 6: Install Playwright Browser Binaries
If browser automation via Playwright is required:

```bash
python -m playwright install chromium
```

---

## 3. Verifying Installation

Verify that all CLI commands, dynamic paths, and execution nodes are properly recognized:

```bash
# Verify system diagnostics and registered nodes
python -m jarvis.cli status --json

# Run the offline, headless regression suite
make test
```

Expected output:
- Python environment: Active virtual environment.
- OS Architecture: host-specific (`Linux` or `Windows`).
- Core Modules: `jarvis.core.control_plane`, `jarvis.voice.audio_aec`, `jarvis.gateway.server`.
- Audio Devices: reported when host audio support is installed; optional for the server baseline.

Next, proceed to [Configuration](configuration.md) to set up your API keys and environment variables.
