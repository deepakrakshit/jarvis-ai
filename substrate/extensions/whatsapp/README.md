# WhatsApp-Gemini-Voice

Full-duplex autonomous AI voice calling on WhatsApp powered directly by WhatsApp's official Web VoIP WebAssembly engine and Google's Gemini 3.8 Live API.

---

## Architecture Overview

```
[ Objective Command / CLI ]
           │
           ▼
[ Contact & Phone Resolver ]
           │
           ▼
 [ WhatsApp VoIP Manager ]
           │
           ▼
  [ WhatsApp VoIP Call ] (baileys-caller via WebRTC & WebAssembly)
           │
           │  16 kHz mono Float32Array
           ▼
[ Realtime Duplex Audio Bridge ]
     │                        ▲
     │ 16 kHz 16-bit PCM      │ 24 kHz -> 16 kHz Resampled PCM
     ▼                        │
[ Gemini 3.8 Live API ] ──────┘ (Bidirectional WebSocket)
     │
     ├─► Realtime Speech Synthesis
     ├─► Voice Activity & Server-Side Barge-in Interruption
     └─► Input / Output Live Transcription
```

The system operates strictly in memory:
* No Windows audio loopback devices (no VB-CABLE or Virtual Audio Cable).
* No physical microphone/speaker routing.
* No browser automation (Playwright/Puppeteer).
* No cloud telephony providers (Twilio, Exotel, Plivo, SIP trunks, PSTN).
* Pure WhatsApp Web VoIP transport running in-process via WebAssembly (`whatsapp.wasm`) and WebRTC data channels (`@roamhq/wrtc`) connected to WhatsApp edge relays.

---

## Prerequisites

* **OS:** Windows 10/11 (or Linux/macOS)
* **Node.js:** `>= 20.0.0` (tested on Node v24.13.1)
* **npm:** `>= 10.0.0` (tested on npm v11.8.0)
* **FFmpeg:** Installed and accessible on system `PATH`
* **Git:** Installed and on `PATH`
* **Google Gemini API Key:** Configured in `.env` (`GEMINI_API_KEY=...`)

---

## Configuration

The application automatically loads variables from `.env` in the project root:

```env
# Google Gemini Configuration
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.8-live

# WhatsApp Calling Configuration
WHATSAPP_AUTH_DIR=./auth
CALL_DURATION_MS=120000

# Optional test recipient (E.164 digits, e.g. 919876543210)
TEST_WHATSAPP_NUMBER=
```

> **Security Note:** `.env`, `./auth`, and all conversation logs are strictly gitignored to safeguard credentials.

---

## Installation & Build

1. Clone and install root dependencies:
   ```bash
   npm install
   ```

2. Compile TypeScript:
   ```bash
   npm run build
   ```

---

## Authentication (Link WhatsApp Device)

To link your WhatsApp account using the official linked-device multi-device flow:

```bash
npm run auth
```

1. A QR code will be displayed directly in your terminal.
2. Open WhatsApp on your phone.
3. Navigate to: **Settings -> Linked Devices -> Link a Device**.
4. Scan the terminal QR code.
5. The session keys will be saved in `./auth`. All subsequent calls will reuse this authenticated session without needing another scan.

---

## Verification & Testing Suite

Run tests step-by-step:

### 1. WhatsApp Transport Test
Validates environment prerequisites, audio conversions, 24kHz-to-16kHz resampler, tone generation, and auth status:
```bash
npm run test:transport
```

### 2. Gemini 3.8 Live API Test
Verifies live bidirectional WebSocket connection to `gemini-3.8-live`, audio transmission, audio reception, and transcription:
```bash
npm run test:gemini
```

### 3. Full 14-Point End-to-End Test Matrix
Executes the comprehensive verification matrix:
```bash
npm run test:e2e
```

---

## Interactive JARVIS Voice & Text Console (Deepak)

You can launch the live interactive console where Deepak directly commands JARVIS to make calls:

### Using the Windows Batch Launcher:
Simply double-click [`run.bat`](file:///C:/Users/deepa/OneDrive/Desktop/WhatsApp-Gemini-Voice/run.bat) or run:
```cmd
run.bat
```

### Or using npm:
```bash
npm run chat
```

### In-Console Commands:
Once the console is open, Deepak can directly issue natural language commands:
```text
Deepak > call Rahul with number 9876543210 on whatsapp and tell him i might miss tomorrow
```
or:
```text
Deepak > call user-x with number 9876543210 on whatsapp and tell him i might miss tomorrow
```

### How the Call Works:
1. **Identification:** JARVIS dials the recipient over WhatsApp VoIP.
2. **Hinglish Persona:** JARVIS greets the recipient in conversational Hinglish:
   *"Namaste! Main Deepak ki taraf se call kar raha hoon, unka AI assistant. Deepak ne bola tha ki woh kal miss kar sakte hain..."*
3. **Conversational Duplex & Barge-in:** Listens to the recipient's reply and responds dynamically with native Gemini 3.8 Live audio. If the recipient speaks while JARVIS is speaking, JARVIS yields immediately.
4. **Post-Call Debrief:** Once the call terminates, JARVIS summarizes what the recipient replied and presents an executive debriefing directly in the same console for Deepak.

---

## Direct CLI Call Mode

To place a single call directly from the terminal without entering interactive chat:

```bash
npm run call -- --target "+919876543210" --objective "Tell Rahul that I might miss tomorrow."
```

### What Happens During the Call:
1. Contact resolution validates the target phone number.
2. WhatsApp establishes the VoIP session and dials the recipient.
3. The remote phone rings.
4. When answered, the media session transitions to active state.
5. Gemini 3.8 Live starts speaking based on the custom phone-agent prompt.
6. The remote user speaks: their audio is streamed live into Gemini.
7. Gemini generates realtime conversational replies streamed back to the call.
8. **Barge-in / Interruption:** If the remote user begins talking while Gemini is speaking, Gemini signals interruption, the outbound audio queue is purged instantly, and the AI listens attentively to the user.
9. When the objective is met or the recipient hangs up, the session terminates cleanly.
10. Full chronological transcripts and call summaries are saved in `./logs/`.

---

## Upstream Modifications to `baileys-caller`

Upstream repository: [SheIITear/baileys-caller](https://github.com/SheIITear/baileys-caller)  
Base commit: `36c6e0a03d7d80723b4f203a45d4cb9e1a4b86f6`  
Enhanced commit: `55964e9`

### Why Modifications Were Required:
The upstream `baileys-caller` library only supported static file playback (`audioSource: "./hello.mp3"`) by spawning an external `ffmpeg` subprocess. It did not expose a mechanism to inject live dynamic PCM frames generated in realtime by an AI model, nor did it support clearing queued audio when the remote party interrupts (barge-in).

### Enhancements Implemented:
1. **Realtime Audio Source Mode:** Added `audioSource: "realtime"` to `AudioFeeder` which meters out 20ms chunks (320 samples @ 16kHz) on an exact cadence without spawning FFmpeg.
2. **Dynamic PCM Injection:** Implemented `pushChunk(data: Float32Array)` in `AudioFeeder` and exposed `pushAudio(pcm: Float32Array)` on `ActiveCall`.
3. **Conversational Barge-in / Queue Clearance:** Implemented `clearQueue()` in `AudioFeeder` and exposed `clearAudioQueue()` on `ActiveCall` to instantaneously purge queued frames when the user interrupts.
4. **Underflow Protection:** When no AI speech is queued, `AudioFeeder` automatically emits zeroed comfort frames to ensure WhatsApp RTP stream continuity without timeouts or disconnection.

---

## Known Limitations & Upstream WhatsApp VoIP Risks

1. **Experimental Unofficial VoIP:** This implementation uses WhatsApp Web's reverse-engineered WebAssembly VoIP stack. It is intended for controlled technical evaluation.
2. **Account Safety:** Avoid placing high volumes of automated calls or calling unfamiliar numbers, as WhatsApp may flag rapid unsolicited calling patterns.
3. **Inbound Calls:** The underlying `baileys-caller` library currently only supports outbound 1:1 calls; inbound call answering is not supported by upstream.
4. **Group Calls:** WhatsApp group calls use an alternative protocol not supported by the 1:1 VoIP client.
