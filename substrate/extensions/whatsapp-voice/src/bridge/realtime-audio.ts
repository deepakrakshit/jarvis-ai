/**
 * Full-duplex realtime audio bridge connecting WhatsApp VoIP and Gemini 3.8 Live.
 *
 * Implements:
 * - Concurrent asynchronous uplink and downlink
 * - In-memory format conversion (Float32 <-> Int16)
 * - 24kHz -> 16kHz resampling
 * - Instantaneous barge-in / interruption clearance
 * - Dual-phase farewell policy with cancellable grace period
 * - Bounded audio queues and interruption metrics
 * - Chronological transcript aggregation and summary logging
 */

import { writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { type CallSession } from "../whatsapp/call.js";
import { type GeminiLiveSession } from "../gemini/live.js";
import { float32ToInt16, int16ToFloat32, resample24kTo16k } from "../whatsapp/audio.js";

export interface TranscriptEntry {
  role: "user" | "assistant" | "system";
  text: string;
  timestamp: number;
}

export interface BridgeMetrics {
  callId: string;
  uplinkFramesSentToGemini: number;
  uplinkBytesSentToGemini: number;
  downlinkChunksReceivedFromGemini: number;
  downlinkBytesReceivedFromGemini: number;
  downlinkSamplesPushedToWhatsApp: number;
  interruptionCount: number;
  bargeInCount: number;
  userTurnCount: number;
  generationInterruptedCount: number;
  postFarewellReengagementCount: number;
  audioQueueMaxDepth: number;
  transcriptEntriesCount: number;
}

export interface RealtimeAudioBridgeOptions {
  logsDir?: string;
  farewellGracePeriodMs?: number;
  maxAudioQueueDepth?: number;
}

const FAREWELL_REGEX = /(?:alvida|bye\b|goodbye\b|take care\b|call rakhta hoon|main chal raha hoon|have a great day)/i;

export class RealtimeAudioBridge {
  readonly #call: CallSession;
  readonly #gemini: GeminiLiveSession;
  readonly #logsDir: string;
  readonly #farewellGracePeriodMs: number;
  readonly #maxAudioQueueDepth: number;

  #active = false;
  #uplinkFramesSent = 0;
  #uplinkBytesSent = 0;
  #downlinkChunksReceived = 0;
  #downlinkBytesReceived = 0;
  #downlinkSamplesPushed = 0;
  #interruptionCount = 0;
  #userTurnCount = 0;
  #generationInterruptedCount = 0;
  #postFarewellReengagementCount = 0;
  #audioQueueMaxDepth = 0;

  #farewellTimer: NodeJS.Timeout | null = null;
  #inFarewellGracePeriod = false;

  readonly #transcript: TranscriptEntry[] = [];
  #currentInputText = "";
  #currentOutputText = "";

  constructor(
    call: CallSession,
    gemini: GeminiLiveSession,
    options: RealtimeAudioBridgeOptions | string = "./logs"
  ) {
    this.#call = call;
    this.#gemini = gemini;
    if (typeof options === "string") {
      this.#logsDir = options;
      this.#farewellGracePeriodMs = 2500;
      this.#maxAudioQueueDepth = 50;
    } else {
      this.#logsDir = options.logsDir || "./logs";
      this.#farewellGracePeriodMs = options.farewellGracePeriodMs ?? 2500;
      this.#maxAudioQueueDepth = options.maxAudioQueueDepth ?? 50;
    }

    try {
      mkdirSync(this.#logsDir, { recursive: true });
    } catch {}
  }

  /**
   * Start the bidirectional full-duplex bridge.
   */
  start(): void {
    if (this.#active) return;
    this.#active = true;

    // ──────────────── UPLINK (WhatsApp -> Gemini 3.8 Live) ────────────────
    this.#call.on("audio", (pcmFloat32: Float32Array) => {
      if (!this.#active || !this.#gemini.isConnected) return;

      try {
        const i16 = float32ToInt16(pcmFloat32);
        const buffer = Buffer.from(i16.buffer, i16.byteOffset, i16.byteLength);

        this.#gemini.sendRealtimeAudio(buffer);
        this.#uplinkFramesSent++;
        this.#uplinkBytesSent += buffer.length;
      } catch (err) {
        console.error("[Bridge Uplink Error]", err);
      }
    });

    // ──────────────── DOWNLINK (Gemini 3.8 Live -> WhatsApp) ─────────────
    this.#gemini.on("audio", (rawPcm24k: Buffer) => {
      if (!this.#active) return;

      try {
        this.#downlinkChunksReceived++;
        this.#downlinkBytesReceived += rawPcm24k.length;

        // Convert Buffer to Int16Array
        const numSamples24k = Math.floor(rawPcm24k.length / 2);
        const i16 = new Int16Array(
          rawPcm24k.buffer,
          rawPcm24k.byteOffset,
          numSamples24k
        );

        // Convert Int16Array to Float32Array [-1.0, 1.0]
        const f32_24k = int16ToFloat32(i16);

        // Resample from 24,000 Hz to 16,000 Hz
        const f32_16k = resample24kTo16k(f32_24k);

        // Backpressure monitoring
        const currentQueue = (this.#call as any)._activeCall?.getAudioQueueLength?.() ?? 0;
        if (currentQueue > this.#audioQueueMaxDepth) {
          this.#audioQueueMaxDepth = currentQueue;
        }

        // Inject into WhatsApp VoIP outbound stream
        this.#call.pushAudio(f32_16k);
        this.#downlinkSamplesPushed += f32_16k.length;
      } catch (err) {
        console.error("[Bridge Downlink Error]", err);
      }
    });

    // ──────────────── BARGE-IN / INTERRUPTION ────────────────────────────
    this.#gemini.on("interrupted", () => {
      this.#interruptionCount++;
      this.#generationInterruptedCount++;
      this.#cancelFarewellIfActive();
      console.log(`[Bridge Barge-in] Model speech interrupted by user (#${this.#interruptionCount}). Flushing outbound queue.`);
      this.#call.clearAudioQueue();
      this.#commitAssistantTurn();
    });

    // ──────────────── TRANSCRIPTION AGGREGATION ──────────────────────────
    this.#gemini.on("interimInputTranscription", (_text: string) => {
      // User is actively speaking interim chunks, cancel any pending farewell immediately
      this.#cancelFarewellIfActive();
      this.#commitAssistantTurn();
    });

    this.#gemini.on("inputTranscription", (text: string, finished: boolean) => {
      // User is speaking, so cancel any pending farewell hangup immediately
      this.#cancelFarewellIfActive();
      this.#commitAssistantTurn();

      const clean = text.trim();
      if (clean) {
        this.#currentInputText += (this.#currentInputText ? " " : "") + clean;
      }
      if (finished) {
        this.#commitUserTurn();
      }
    });

    this.#gemini.on("outputTranscription", (text: string, finished: boolean) => {
      this.#commitUserTurn();
      const clean = text.trim();
      if (clean) {
        this.#currentOutputText += (this.#currentOutputText ? " " : "") + clean;
      }
      if (finished) {
        const fullText = this.#currentOutputText;
        this.#commitAssistantTurn();
        this.#checkFarewell(fullText);
      }
    });

    this.#gemini.on("text", (text: string) => {
      this.#commitUserTurn();
      const clean = text.trim();
      if (clean) {
        this.#currentOutputText += (this.#currentOutputText ? " " : "") + clean;
      }
    });

    this.#gemini.on("turnComplete", () => {
      const fullText = this.#currentOutputText;
      this.#commitAssistantTurn();
      this.#commitUserTurn();
      this.#checkFarewell(fullText);
    });
  }

  #checkFarewell(text: string): void {
    if (!text || this.#inFarewellGracePeriod) return;
    if (FAREWELL_REGEX.test(text)) {
      this.#enterFarewellGracePeriod();
    }
  }

  #enterFarewellGracePeriod(): void {
    if (this.#inFarewellGracePeriod || !this.#active) return;
    this.#inFarewellGracePeriod = true;
    console.log(`[Bridge] Assistant signaled farewell. Initiating grace period (${this.#farewellGracePeriodMs}ms)...`);

    this.#farewellTimer = setTimeout(() => {
      this.#farewellTimer = null;
      if (this.#active) {
        console.log(`[Bridge] Farewell grace period concluded cleanly. Ending WhatsApp call.`);
        this.#call.end();
      }
    }, this.#farewellGracePeriodMs);
  }

  #cancelFarewellIfActive(): void {
    if (this.#farewellTimer) {
      clearTimeout(this.#farewellTimer);
      this.#farewellTimer = null;
      this.#inFarewellGracePeriod = false;
      this.#postFarewellReengagementCount++;
      console.log(`[Bridge] Recipient re-engaged during farewell grace period! Continuing call.`);
    }
  }

  #commitUserTurn(): void {
    const text = this.#currentInputText.trim();
    if (text) {
      this.#userTurnCount++;
      this.#transcript.push({
        role: "user",
        text,
        timestamp: Date.now(),
      });
      console.log(`[Transcript - User] "${text}"`);
      this.#currentInputText = "";
    }
  }

  #commitAssistantTurn(): void {
    const text = this.#currentOutputText.trim();
    if (text) {
      this.#transcript.push({
        role: "assistant",
        text,
        timestamp: Date.now(),
      });
      console.log(`[Transcript - AI] "${text}"`);
      this.#currentOutputText = "";
    }
  }

  /**
   * Stop the bridge and record final logs and transcripts.
   */
  stop(): void {
    if (!this.#active) return;
    this.#active = false;

    if (this.#farewellTimer) {
      clearTimeout(this.#farewellTimer);
      this.#farewellTimer = null;
    }

    // Flush any pending text
    this.#commitAssistantTurn();
    this.#commitUserTurn();

    this.saveTranscript();
  }

  getMetrics(): BridgeMetrics {
    return {
      callId: this.#call.callId,
      uplinkFramesSentToGemini: this.#uplinkFramesSent,
      uplinkBytesSentToGemini: this.#uplinkBytesSent,
      downlinkChunksReceivedFromGemini: this.#downlinkChunksReceived,
      downlinkBytesReceivedFromGemini: this.#downlinkBytesReceived,
      downlinkSamplesPushedToWhatsApp: this.#downlinkSamplesPushed,
      interruptionCount: this.#interruptionCount,
      bargeInCount: this.#interruptionCount,
      userTurnCount: this.#userTurnCount,
      generationInterruptedCount: this.#generationInterruptedCount,
      postFarewellReengagementCount: this.#postFarewellReengagementCount,
      audioQueueMaxDepth: this.#audioQueueMaxDepth,
      transcriptEntriesCount: this.#transcript.length,
    };
  }

  getTranscript(): TranscriptEntry[] {
    return [...this.#transcript];
  }

  /**
   * Save chronological transcript and call summary to disk.
   */
  saveTranscript(options?: {
    summary?: string;
    recipientReply?: string;
    report?: unknown;
  }): { jsonPath: string; textPath: string; summary: string; recipientReply: string } {
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    const callId = this.#call.callId || "call";
    const jsonPath = join(this.#logsDir, `transcript-${callId}-${timestamp}.json`);
    const textPath = join(this.#logsDir, `transcript-${callId}-${timestamp}.txt`);

    const callMetrics = this.#call.getMetrics();
    const bridgeMetrics = this.getMetrics();

    // Extract raw user utterances directly for strict provenance
    const userUtterances = this.#transcript
      .filter((t) => t.role === "user")
      .map((t) => t.text.trim())
      .filter(Boolean);
    const recipientReply =
      options?.recipientReply ||
      (userUtterances.length > 0 ? userUtterances.join("; ") : "No verbal reply detected in call.");
    const summary =
      options?.summary ||
      `Call to ${callMetrics.targetNumber} completed after ${(callMetrics.durationMs / 1000).toFixed(1)}s. Recipient replied: "${recipientReply}"`;

    const payload = {
      callId,
      timestamp: new Date().toISOString(),
      targetNumber: callMetrics.targetNumber,
      summary,
      recipientReply,
      report: options?.report ?? null,
      metrics: {
        call: callMetrics,
        bridge: bridgeMetrics,
      },
      transcript: this.#transcript,
    };

    try {
      writeFileSync(jsonPath, JSON.stringify(payload, null, 2), "utf8");

      let textContent = `=== WHATSAPP - GEMINI 3.8 LIVE CALL TRANSCRIPT ===\n`;
      textContent += `Call ID: ${callId}\n`;
      textContent += `Target: ${callMetrics.targetNumber}\n`;
      textContent += `Duration: ${(callMetrics.durationMs / 1000).toFixed(1)}s\n`;
      textContent += `Recipient Reply: ${recipientReply}\n`;
      textContent += `Summary: ${summary}\n`;
      textContent += `Interruptions: ${bridgeMetrics.interruptionCount}\n`;
      textContent += `User Turns: ${bridgeMetrics.userTurnCount}\n`;
      textContent += `Post-Farewell Re-engagements: ${bridgeMetrics.postFarewellReengagementCount}\n\n`;
      textContent += `--- CONVERSATION ---\n`;

      for (const entry of this.#transcript) {
        const time = new Date(entry.timestamp).toLocaleTimeString();
        textContent += `[${time}] ${entry.role.toUpperCase()}: ${entry.text}\n`;
      }

      writeFileSync(textPath, textContent, "utf8");
      console.log(`[Bridge] Transcript and summary saved to:\n  ${jsonPath}\n  ${textPath}`);
    } catch (err) {
      console.error("[Bridge] Failed to save transcript:", err);
    }

    return { jsonPath, textPath, summary, recipientReply };
  }
}
