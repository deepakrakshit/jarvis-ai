---
name: Bug Report
about: Report a defect, unexpected behavior, or regression in JARVIS
title: "[BUG]: "
labels: ["bug", "triage"]
assignees: []
---

## 🐛 Bug Description

<!-- A clear and concise description of what the bug is. -->

## 🖥️ Operating Environment

- **OS:** Windows 10 / Windows 11 (64-bit) (Version / Build: e.g. 23H2 / 26100)
- **Python Version:** 3.10+ (`python --version`)
- **Node.js Version:** 18+ (`node --version`)
- **Execution Target:** Native Host / Headless / Terminal / Neural Web UI

## 🧩 Affected Subsystems

- [ ] Realtime Voice & Gemini 3.8 Live Bridge (`cognition/gemini_live.py`)
- [ ] Hardware Acoustic Echo Cancellation (WASAPI Loopback DSP) (`voice/audio_aec.py`)
- [ ] Windows COM UI Automation (`execution/windows/uia.py`)
- [ ] Substrate IPC Engine (`execution/substrate_bridge.py`)
- [ ] Autonomous WhatsApp Telephony (`substrate/extensions/whatsapp`)
- [ ] Native Telegram Remote Control (`telegram/service.py`)
- [ ] Gateway WebSocket Daemon & Neural Web UI (`gateway/server.py`, `frontend/`)
- [ ] Multi-Model Router & Cognitive Delegation (`cognition/model_router.py`)
- [ ] Memory Engine & SQLite FTS5 (`memory/manager.py`)
- [ ] Policy Engine & Capability Firewall (`policy/`)

## 🔁 Reproduction Steps

Steps to reproduce the behavior:
1. Start the system via `...`
2. Issue the command or voice prompt `...`
3. Observe the behavior or error output

## 🎯 Expected Behavior

<!-- A clear and concise description of what you expected to happen. -->

## 📋 Actual Behavior & Telemetry Logs

<!-- Paste relevant console logs, stack traces, or terminal output here. -->
```text
[Paste terminal logs or error tracebacks here]
```

## 📸 Visual Telemetry / Screenshots

<!-- If applicable, add screenshots or recordings to explain your problem. -->

## ℹ️ Additional Context

<!-- Add any other context about the problem here (e.g. connected audio peripherals, active application windows). -->
