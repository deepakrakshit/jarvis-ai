/**
 * WhatsApp VoIP call session wrapper.
 *
 * Encapsulates the ActiveCall lifecycle, telemetry, realtime audio injection,
 * and audio reception.
 */

import { EventEmitter } from "node:events";
import { type ActiveCall, CallState } from "../voip/index.mjs";
import { computeAudioStats, type AudioStats } from "./audio.js";

export interface CallMetrics {
  callId: string;
  targetNumber: string;
  state: string;
  ringingAt?: number;
  connectedAt?: number;
  endedAt?: number;
  durationMs: number;
  endReason?: string;
  inboundFramesCount: number;
  inboundSamplesCount: number;
  inboundNonZeroCount: number;
  lastInboundRms: number;
  peakInboundRms: number;
  outboundFramesPushed: number;
  outboundSamplesPushed: number;
  interruptionDiscards: number;
}

export class CallSession extends EventEmitter {
  readonly #activeCall: ActiveCall;
  readonly #targetNumber: string;
  readonly #startTime = Date.now();
  #ringingAt?: number;
  #connectedAt?: number;
  #endedAt?: number;
  #endReason?: string;

  #inboundFramesCount = 0;
  #inboundSamplesCount = 0;
  #inboundNonZeroCount = 0;
  #lastInboundRms = 0;
  #peakInboundRms = 0;
  #outboundFramesPushed = 0;
  #outboundSamplesPushed = 0;
  #interruptionDiscards = 0;

  constructor(activeCall: ActiveCall, targetNumber: string) {
    super();
    this.#activeCall = activeCall;
    this.#targetNumber = targetNumber;

    this.#activeCall.on("ringing", () => {
      this.#ringingAt = Date.now();
      this.emit("ringing");
    });

    this.#activeCall.on("connected", () => {
      this.#connectedAt = Date.now();
      this.emit("connected");
    });

    this.#activeCall.on("audio", (pcm: Float32Array) => {
      this.#inboundFramesCount++;
      this.#inboundSamplesCount += pcm.length;

      const stats = computeAudioStats(pcm);
      this.#lastInboundRms = stats.rms;
      if (stats.rms > this.#peakInboundRms) {
        this.#peakInboundRms = stats.rms;
      }
      if (stats.nonZeroCount > 0) {
        this.#inboundNonZeroCount += stats.nonZeroCount;
      }

      this.emit("audio", pcm, stats);
    });

    this.#activeCall.on("ended", (reason: string) => {
      this.#endedAt = Date.now();
      this.#endReason = reason;
      this.emit("ended", reason);
    });

    this.#activeCall.on("error", (err: Error) => {
      this.emit("error", err);
    });
  }

  get callId(): string {
    return this.#activeCall.callId;
  }

  get state(): CallState {
    return this.#activeCall.state;
  }

  get targetNumber(): string {
    return this.#targetNumber;
  }

  /**
   * Inject 16kHz mono Float32Array PCM frames into the active WhatsApp call.
   */
  pushAudio(pcm: Float32Array): void {
    if (!pcm || pcm.length === 0) return;
    this.#outboundFramesPushed++;
    this.#outboundSamplesPushed += pcm.length;
    this.#activeCall.pushAudio(pcm);
  }

  /**
   * Instantly purges any queued outbound speech frames for conversational interruption.
   */
  clearAudioQueue(): void {
    const previousQueue = this.#activeCall.getAudioQueueLength();
    if (previousQueue > 0) {
      this.#interruptionDiscards++;
    }
    this.#activeCall.clearAudioQueue();
  }

  /**
   * End the call cleanly.
   */
  end(): void {
    this.#activeCall.end();
  }

  /**
   * Await call conclusion.
   */
  async waitForEnd(): Promise<string> {
    return this.#activeCall.waitForEnd();
  }

  /**
   * Collect metrics and health diagnostic snapshot.
   */
  getMetrics(): CallMetrics {
    const now = Date.now();
    const durationMs = this.#connectedAt ? (this.#endedAt ?? now) - this.#connectedAt : 0;

    let stateStr = "idle";
    switch (this.#activeCall.state) {
      case CallState.Calling:
        stateStr = "calling";
        break;
      case CallState.PreacceptReceived:
        stateStr = "ringing";
        break;
      case CallState.Active:
        stateStr = "connected";
        break;
      case CallState.Ending:
      case CallState.Idle:
        stateStr = "ended";
        break;
    }

    return {
      callId: this.callId,
      targetNumber: this.#targetNumber,
      state: stateStr,
      ringingAt: this.#ringingAt,
      connectedAt: this.#connectedAt,
      endedAt: this.#endedAt,
      durationMs,
      endReason: this.#endReason,
      inboundFramesCount: this.#inboundFramesCount,
      inboundSamplesCount: this.#inboundSamplesCount,
      inboundNonZeroCount: this.#inboundNonZeroCount,
      lastInboundRms: this.#lastInboundRms,
      peakInboundRms: this.#peakInboundRms,
      outboundFramesPushed: this.#outboundFramesPushed,
      outboundSamplesPushed: this.#outboundSamplesPushed,
      interruptionDiscards: this.#interruptionDiscards,
    };
  }
}
