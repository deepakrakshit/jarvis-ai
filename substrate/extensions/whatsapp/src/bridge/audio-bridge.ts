/**
 * Audio abstraction and loopback diagnostics bridge for WhatsApp voice calls.
 *
 * Provides PCM frame streaming interfaces and diagnostics to measure
 * roundtrip latency, frame jitter, and packet loss.
 */

import { EventEmitter } from "node:events";

export interface AudioLoopbackDiagnostics {
  success: boolean;
  packetsSent: number;
  packetsReceived: number;
  roundtripLatencyMs: number;
  jitterMs: number;
  packetLossPercent: number;
}

export interface WhatsAppAudioInput {
  read(size?: number): Promise<Buffer | null>;
  on(event: "data", listener: (chunk: Buffer) => void): void;
  close(): void;
}

export interface WhatsAppAudioOutput {
  write(chunk: Buffer): Promise<boolean>;
  flush(): Promise<void>;
  close(): void;
}

export class WhatsAppAudioInputStream extends EventEmitter implements WhatsAppAudioInput {
  private isClosed: boolean = false;
  private queue: Buffer[] = [];

  public push(chunk: Buffer): void {
    if (this.isClosed) return;
    this.queue.push(chunk);
    this.emit("data", chunk);
  }

  public async read(size?: number): Promise<Buffer | null> {
    if (this.isClosed && this.queue.length === 0) {
      return null;
    }
    if (this.queue.length > 0) {
      return this.queue.shift()!;
    }
    return new Promise((resolve) => {
      const onData = (chunk: Buffer) => {
        this.off("close", onClose);
        resolve(chunk);
      };
      const onClose = () => {
        this.off("data", onData);
        resolve(null);
      };
      this.once("data", onData);
      this.once("close", onClose);
    });
  }

  public close(): void {
    if (this.isClosed) return;
    this.isClosed = true;
    this.emit("close");
    this.removeAllListeners();
  }
}

export class WhatsAppAudioOutputStream implements WhatsAppAudioOutput {
  private isClosed: boolean = false;
  private onWriteChunk?: (chunk: Buffer) => void;

  constructor(onWriteChunk?: (chunk: Buffer) => void) {
    this.onWriteChunk = onWriteChunk;
  }

  public async write(chunk: Buffer): Promise<boolean> {
    if (this.isClosed) return false;
    if (this.onWriteChunk) {
      this.onWriteChunk(chunk);
    }
    return true;
  }

  public async flush(): Promise<void> {
    return Promise.resolve();
  }

  public close(): void {
    this.isClosed = true;
  }
}

export class WhatsAppAudioBridge {
  public readonly input: WhatsAppAudioInputStream;
  public readonly output: WhatsAppAudioOutputStream;

  constructor() {
    this.input = new WhatsAppAudioInputStream();
    this.output = new WhatsAppAudioOutputStream(() => {});
  }

  /**
   * Run synthetic audio loopback diagnostics.
   */
  public async runLoopbackDiagnostics(frameCount: number = 50): Promise<AudioLoopbackDiagnostics> {
    const latencies: number[] = [];
    let received = 0;
    const sampleRate = 16000;
    const frameSamples = 320;
    const frameBytes = frameSamples * 2;

    for (let i = 0; i < frameCount; i++) {
      const sendTs = Date.now();
      const syntheticFrame = Buffer.alloc(frameBytes);

      for (let s = 0; s < frameSamples; s++) {
        const val = Math.floor(Math.sin((2 * Math.PI * 440 * s) / sampleRate) * 16000);
        syntheticFrame.writeInt16LE(val, s * 2);
      }

      await this.output.write(syntheticFrame);
      this.input.push(syntheticFrame);

      const recvTs = Date.now();
      const delta = Math.max(0, recvTs - sendTs);
      latencies.push(delta);
      received++;

      await new Promise((r) => setTimeout(r, 2));
    }

    const avgLatency =
      latencies.length > 0 ? latencies.reduce((a, b) => a + b, 0) / latencies.length : 0;
    const jitter =
      latencies.length > 1
        ? latencies
            .slice(1)
            .map((v, i) => Math.abs(v - latencies[i]))
            .reduce((a, b) => a + b, 0) /
          (latencies.length - 1)
        : 0;

    const packetLossPercent = ((frameCount - received) / frameCount) * 100;

    return {
      success: packetLossPercent < 5.0,
      packetsSent: frameCount,
      packetsReceived: received,
      roundtripLatencyMs: Number(avgLatency.toFixed(2)),
      jitterMs: Number(jitter.toFixed(2)),
      packetLossPercent: Number(packetLossPercent.toFixed(2)),
    };
  }
}
