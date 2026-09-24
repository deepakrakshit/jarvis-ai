# Native Telegram Remote Control Architecture & User Guide

JARVIS includes a native, host-resident Telegram Remote Control Subsystem that allows the primary operator to securely interact with, supervise, and control the Windows JARVIS host from any mobile device running Telegram.

---

## 1. Architectural Overview & Security Boundary

The Telegram subsystem runs as a trusted in-process daemon inside the core JARVIS operating system runtime:

```mermaid
graph TD
    UserPhone(["📱 Operator Phone / Telegram App"]) <-->|Encrypted Telegram Cloud| TG_API["☁️ Telegram Bot API"]
    
    subgraph LOCAL_JARVIS_HOST ["JARVIS Windows Host Machine"]
        TG_API <-->|Outbound Long Polling (getUpdates)| TelegramDaemon["🤖 Native Telegram Daemon\n(aiogram 3.x)"]
        
        TelegramDaemon --> AuthGate{"🛡️ Whitelist Auth\n& Challenge Pairing"}
        AuthGate -->|Authorized| DirectControl["🧠 Control Plane Dispatch\n(control_plane.submit_intent)"]
        AuthGate -->|Privileged Callback| ApprovalGate["⚖️ Action Approval Manager\n(approval_manager.resolve)"]
        AuthGate -->|Screenshot| DesktopCapture["🖥️ Windows Desktop Engine\n(pyautogui / COM)"]
        
        DirectControl --> CoreKernel["⚙️ Action Broker & Capability Firewall"]
        CoreKernel --> ExecutionNodes["🖥️ Windows Host | 🌐 Browser | 📞 WhatsApp VoIP"]
        
        CoreKernel -.->|Task Updates| CardManager["🎛️ Rate-Limited Single Card Manager"]
        CardManager -.->|HTML Edit In-Place| TelegramDaemon
    end
```

### Core Security & Invariant Guarantees

1. **Zero Inbound Attack Surface:**
   - Operates strictly via outbound HTTPS long-polling (`getUpdates`).
   - No public IP, DNS domain, port forwarding, reverse proxies, ngrok, or Cloudflare tunnels required.
2. **Fail-Closed Boundary:**
   - Unauthenticated users receive an immediate access denial.
   - Internal system paths, prompt context, and machine diagnostics are completely shielded.
3. **Cryptographic Single-Use Onboarding:**
   - Pairing requires a 256-bit entropy challenge nonce generated directly on the host.
   - Nonces have a strict time-to-live (default: 300 seconds) and are permanently invalidated upon consumption.
4. **64-Byte Telegram Callback Constraint:**
   - Inline approval action payloads (`[ ✅ AUTHORIZE ]` / `[ ❌ DENY ]`) use compact, opaque tokens (`appr:a:<token>`, `appr:d:<token>`) guaranteed to stay well within Telegram's strict 64-byte payload limit.
5. **Rate-Limited Single Live Status Cards:**
   - Tasks and VoIP calls maintain a single editable status message updated in place.
   - Prevents chat flooding and respects Telegram API 429 rate limits.
6. **Automatic Sensitive Data Redaction:**
   - Outbound cards and summaries automatically mask bot tokens, API keys, private keys, and bearer tokens before transmission.

---

## 2. Configuration & Setup

### Step 1: Create Your Bot via BotFather

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts to choose a display name and username (e.g., `MyPersonalJarvisBot`).
3. Copy the HTTP API token provided by BotFather (e.g., `1234567890:ABCdefGhIJKlmNoPQRstuVWxYz_123456`).
4. **Keep this token strictly secret.** Never share or commit this token.

### Step 2: Configure `.env`

Add the token to your local `.env` file:

```bash
# Telegram Remote Control Bot Settings
TELEGRAM_BOT_TOKEN="1234567890:ABCdefGhIJKlmNoPQRstuVWxYz_123456"
TELEGRAM_BOT_ENABLED=true
TELEGRAM_PAIRING_TTL_SECONDS=300
TELEGRAM_APPROVAL_TTL_SECONDS=300
TELEGRAM_RATE_LIMIT_EDIT_INTERVAL=1.0
TELEGRAM_MAX_UPLOAD_BYTES=52428800
```

---

## 3. Pairing Your Mobile Device

1. On your Windows machine, run:
   ```powershell
   jarvis telegram pair
   ```
2. The CLI will generate a secure one-time pairing challenge and deep-link:
   ```text
   === JARVIS TELEGRAM PAIRING CHALLENGE ===
   Pairing Code: 7f8b9c... (256-bit nonce)
   Pairing Link: https://t.me/MyPersonalJarvisBot?start=7f8b9c...
   This link expires in 300 seconds.
   Open this link on your phone in Telegram and tap 'Start'.
   ```
3. Open the link on your phone. When the Telegram chat opens, tap **Start**.
4. The bot will respond:
   ```text
   ✅ JARVIS PAIRED SUCCESSFULLY
   Welcome, Operator. You are now authorized to remotely control JARVIS.
   ```
5. Your numeric Telegram ID is now securely whitelisted in the local JARVIS SQLite database.

---

## 4. Usage & Commands

### Running with JARVIS

When running the unified daemon:
```powershell
jarvis serve
```
JARVIS boots the WebSocket Gateway, the Heartbeat Monitor, and the Telegram Daemon concurrently. If no operator has been paired yet, JARVIS logs a pairing URL directly to the terminal output.

To run the Telegram daemon independently:
```powershell
jarvis telegram daemon
```

### Checking Status & Revoking Access

- **Inspect Telegram bot and operator status:**
  ```powershell
  jarvis telegram status
  ```
- **Output JSON status:**
  ```powershell
  jarvis telegram status --json
  ```
- **Revoke all paired operators:**
  ```powershell
  jarvis telegram unpair
  ```

---

## 5. In-Chat Capabilities

| Interaction | Description |
| :--- | :--- |
| **Natural Language Instruction** | Send any message (e.g., `"Open Chrome and check today's tech news"`, `"Set system volume to 40%"`). JARVIS executes it through the Control Plane and reports progress via an in-place status card. |
| **`/screenshot`** | Captures the active Windows desktop and transmits the image back to Telegram immediately. |
| **`/status`** | Displays connectivity, operator authorization count, and bot health. |
| **`/help`** | Displays command overview and capability guidance. |
| **`/unpair`** | Revokes your mobile device's operator authorization from the phone. |
| **Interactive Approvals** | When JARVIS triggers a privileged action (file deletion, shell execution, etc.), an approval card is sent with `[ ✅ AUTHORIZE ]` and `[ ❌ DENY ]` inline buttons. Tapping either button resolves the ticket in real time. |
| **Document / Media Upload** | Send photos or documents (< 50MB) to deposit them directly into the JARVIS artifacts workspace. |
