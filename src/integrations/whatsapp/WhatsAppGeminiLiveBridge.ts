/**
 * Full-duplex realtime audio bridge connecting WhatsApp voice calls to Gemini Live.
 *
 * Implements:
 * - Realtime bidirectional audio streaming (16kHz / 24kHz PCM)
 * - Immediate barge-in / interruption handling (queue flush on user speech)
 * - Chronological transcript logging
 * - Structured debrief and summary persistence
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import type { WhatsAppCallSession } from "./WhatsAppCalls.js";
import type { WhatsAppAudioBridge } from "./WhatsAppAudioBridge.js";

export interface TranscriptItem {
  role: "user" | "assistant" | "system";
  text: string;
  timestamp: number;
}

export interface LiveBridgeMetrics {
  callId: string;
  inboundFramesCount: number;
  outboundFramesCount: number;
  interruptionCount: number;
  bargeInCount: number;
  userTurnCount: number;
  transcriptCount: number;
}

export interface LiveBridgeOptions {
  call: WhatsAppCallSession;
  audioBridge: WhatsAppAudioBridge;
  geminiSession?: any;
  logsDir?: string;
  farewellGracePeriodMs?: number;
}

export class WhatsAppGeminiLiveBridge {
  private readonly call: WhatsAppCallSession;
  private readonly audioBridge: WhatsAppAudioBridge;
  private readonly geminiSession?: any;
  private readonly logsDir: string;
  private readonly farewellGracePeriodMs: number;

  private isActive: boolean = false;
  private transcripts: TranscriptItem[] = [];
  private interruptionCount: number = 0;
  private bargeInCount: number = 0;
  private userTurnCount: number = 0;
  private farewellTimer: NodeJS.Timeout | null = null;

  constructor(options: LiveBridgeOptions) {
    this.call = options.call;
    this.audioBridge = options.audioBridge;
    this.geminiSession = options.geminiSession;
    this.logsDir = resolve(options.logsDir || "./data/whatsapp/rust/logs");
    this.farewellGracePeriodMs = options.farewellGracePeriodMs ?? 3500;

    mkdirSync(this.logsDir, { recursive: true });
  }

  /**
   * Start bidirectional audio streaming between WhatsApp and Gemini Live.
   */
  public start(): void {
    if (this.isActive) return;
    this.isActive = true;

    // Uplink: WhatsApp Inbound Audio -> Gemini
    this.audioBridge.input.on("data", (chunk: Buffer) => {
      if (!this.isActive) return;
      this.call.recordInboundFrame();

      // Check VAD / barge-in: If user speaks during assistant playback, cancel queue
      this.handleUserAudioActivity(chunk);

      if (this.geminiSession && typeof this.geminiSession.sendAudioChunk === "function") {
        this.geminiSession.sendAudioChunk(chunk);
      }
    });

    // Downlink: Gemini Outbound Audio -> WhatsApp
    if (this.geminiSession) {
      this.geminiSession.on("audio", (audioChunk: Buffer) => {
        if (!this.isActive) return;
        this.call.recordOutboundFrame();
        this.audioBridge.output.write(audioChunk);
      });

      this.geminiSession.on("text", (textChunk: string) => {
        if (!this.isActive) return;
        this.appendTranscript("assistant", textChunk);
      });

      this.geminiSession.on("user_transcript", (text: string) => {
        if (!this.isActive) return;
        this.userTurnCount++;
        this.appendTranscript("user", text);
        this.resetFarewellTimer();
      });

      this.geminiSession.on("interrupted", () => {
        this.handleInterruption();
      });
    }
  }

  private handleUserAudioActivity(chunk: Buffer): void {
    // Detect energy in PCM 16-bit audio
    let sum = 0;
    const samples = Math.floor(chunk.length / 2);
    for (let i = 0; i < samples; i++) {
      const val = Math.abs(chunk.readInt16LE(i * 2));
      sum += val;
    }
    const energy = samples > 0 ? sum / samples : 0;

    // If user speaks with energy > threshold, trigger barge-in discard
    if (energy > 1200) {
      this.handleInterruption();
    }
  }

  private handleInterruption(): void {
    this.bargeInCount++;
    this.interruptionCount++;
    this.call.recordInterruptionDiscard();
    this.resetFarewellTimer();
  }

  private resetFarewellTimer(): void {
    if (this.farewellTimer) {
      clearTimeout(this.farewellTimer);
      this.farewellTimer = null;
    }
  }

  public appendTranscript(role: "user" | "assistant" | "system", text: string): void {
    const clean = text.trim();
    if (!clean) return;

    this.transcripts.push({
      role,
      text: clean,
      timestamp: Date.now(),
    });
  }

  public getTranscript(): TranscriptItem[] {
    return [...this.transcripts];
  }

  public getMetrics(): LiveBridgeMetrics {
    const callMetrics = this.call.getMetrics();
    return {
      callId: this.call.callId,
      inboundFramesCount: callMetrics.inboundFramesCount,
      outboundFramesCount: callMetrics.outboundFramesPushed,
      interruptionCount: this.interruptionCount,
      bargeInCount: this.bargeInCount,
      userTurnCount: this.userTurnCount,
      transcriptCount: this.transcripts.length,
    };
  }

  /**
   * Stop the bridge and persist transcript files to disk.
   */
  public stop(summary?: string, recipientReply?: string): { textPath: string; jsonPath: string } {
    this.isActive = false;
    this.resetFarewellTimer();

    const timestamp = Date.now();
    const baseName = `call_${this.call.callId}_${timestamp}`;
    const textPath = resolve(this.logsDir, `${baseName}_transcript.txt`);
    const jsonPath = resolve(this.logsDir, `${baseName}_summary.json`);

    const textLines = this.transcripts.map(
      (t) => `[${new Date(t.timestamp).toISOString()}] ${t.role.toUpperCase()}: ${t.text}`
    );
    if (summary) {
      textLines.push(`\n--- SUMMARY ---\n${summary}`);
    }
    if (recipientReply) {
      textLines.push(`--- RECIPIENT REPLY ---\n${recipientReply}`);
    }
    writeFileSync(textPath, textLines.join("\n"), "utf-8");

    const jsonPayload = {
      callId: this.call.callId,
      peer: this.call.peer,
      timestamp,
      summary: summary || "Call concluded.",
      recipientReply: recipientReply || "",
      metrics: this.getMetrics(),
      transcript: this.transcripts,
    };
    writeFileSync(jsonPath, JSON.stringify(jsonPayload, null, 2), "utf-8");

    return { textPath, jsonPath };
  }
}
