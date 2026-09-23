/**
 * Gemini 3.8 Live API bidirectional session.
 *
 * Implements native realtime audio-to-audio streaming with:
 * - Direct raw PCM input (16 kHz mono)
 * - Direct raw PCM output (24 kHz mono)
 * - Native server-side barge-in / interruption detection
 * - Realtime input and output speech transcription
 */

import { EventEmitter } from "node:events";
import { GoogleGenAI, Modality, type LiveServerMessage } from "@google/genai";

export interface GeminiLiveOptions {
  apiKey: string;
  model?: string;
  voiceName?: string;
  systemInstruction?: string;
}

export class GeminiLiveSession extends EventEmitter {
  readonly #apiKey: string;
  readonly #model: string;
  readonly #voiceName: string;
  readonly #systemInstruction?: string;

  #ai: GoogleGenAI | null = null;
  #session: any = null;
  #connected = false;

  constructor(options: GeminiLiveOptions) {
    super();
    this.#apiKey = options.apiKey;
    this.#model = options.model || "gemini-3.8-live";
    this.#voiceName = options.voiceName || "Algenib";
    this.#systemInstruction = options.systemInstruction;
  }

  get isConnected(): boolean {
    return this.#connected;
  }

  get model(): string {
    return this.#model;
  }

  get voiceName(): string {
    return this.#voiceName;
  }

  /**
   * Connect to Gemini 3.8 Live WebSocket session.
   */
  async connect(): Promise<void> {
    if (this.#connected) return;

    this.#ai = new GoogleGenAI({ apiKey: this.#apiKey });

    const liveConfig: any = {
      responseModalities: [Modality.AUDIO],
      inputAudioTranscription: {},
      outputAudioTranscription: {},
      speechConfig: {
        voiceConfig: {
          prebuiltVoiceConfig: {
            voiceName: this.#voiceName,
          },
        },
      },
    };

    if (this.#systemInstruction) {
      liveConfig.systemInstruction = {
        parts: [{ text: this.#systemInstruction }],
      };
    }

    try {
      this.#session = await this.#ai.live.connect({
        model: this.#model,
        config: liveConfig,
        callbacks: {
          onopen: () => {
            this.#connected = true;
            this.emit("open");
          },
          onmessage: (message: LiveServerMessage) => {
            this.#handleServerMessage(message);
          },
          onerror: (err: any) => {
            this.emit("error", err);
          },
          onclose: (e: any) => {
            this.#connected = false;
            this.emit("close", e);
          },
        },
      });

      this.#connected = true;
    } catch (err: any) {
      this.#connected = false;
      throw new Error(`Failed to connect to Gemini Live (${this.#model}): ${err?.message ?? err}`);
    }
  }

  /**
   * Send realtime 16kHz 16-bit mono PCM audio chunk to Gemini.
   */
  sendRealtimeAudio(pcm16Buffer: Buffer): void {
    if (!this.#session || !this.#connected) return;

    try {
      const base64Data = pcm16Buffer.toString("base64");
      this.#session.sendRealtimeInput({
        media: {
          data: base64Data,
          mimeType: "audio/pcm;rate=16000",
        },
        audio: {
          data: base64Data,
          mimeType: "audio/pcm;rate=16000",
        },
      });
    } catch (err) {
      this.emit("error", err);
    }
  }

  /**
   * Send a text message turn to Gemini Live session.
   */
  sendTextMessage(text: string, turnComplete = true): void {
    if (!this.#session || !this.#connected) return;

    try {
      this.#session.sendClientContent({
        turns: [
          {
            role: "user",
            parts: [{ text }],
          },
        ],
        turnComplete,
      });
    } catch (err) {
      this.emit("error", err);
    }
  }

  /**
   * Disconnect and clean up live session.
   */
  close(): void {
    if (this.#session) {
      try {
        this.#session.close();
      } catch {}
      this.#session = null;
    }
    this.#connected = false;
  }

  #handleServerMessage(message: LiveServerMessage): void {
    const serverContent = message.serverContent;
    if (!serverContent) return;

    // 1. Interruption detection
    if (serverContent.interrupted) {
      this.emit("interrupted");
    }

    // 2. Turn completion
    if (serverContent.turnComplete) {
      this.emit("turnComplete");
    }

    // 3. Inbound user audio transcription
    if (serverContent.inputTranscription?.text) {
      this.emit("inputTranscription", serverContent.inputTranscription.text, Boolean(serverContent.inputTranscription.finished));
    }
    if (serverContent.interimInputTranscription?.text) {
      this.emit("interimInputTranscription", serverContent.interimInputTranscription.text);
    }

    // 4. Outbound AI speech transcription
    if (serverContent.outputTranscription?.text) {
      this.emit("outputTranscription", serverContent.outputTranscription.text, Boolean(serverContent.outputTranscription.finished));
    }

    // 5. Model audio parts
    const parts = serverContent.modelTurn?.parts;
    if (Array.isArray(parts)) {
      for (const part of parts) {
        if (part.inlineData?.data && part.inlineData.mimeType?.startsWith("audio/pcm")) {
          const audioBuffer = Buffer.from(part.inlineData.data, "base64");
          this.emit("audio", audioBuffer);
        } else if (part.text) {
          this.emit("text", part.text);
        }
      }
    }
  }
}
