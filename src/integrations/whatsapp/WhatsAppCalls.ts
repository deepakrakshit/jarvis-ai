/**
 * Call state machine, event models, and call lifecycle representations.
 *
 * Implements states:
 * IDLE -> RINGING -> ANSWERING -> CONNECTING -> CONNECTED -> DEGRADED -> ENDING -> ENDED / FAILED
 */

import { EventEmitter } from "node:events";
import type { CallState, WhatsAppCall } from "./types.js";

export interface CallMetrics {
  durationMs: number;
  inboundFramesCount: number;
  outboundFramesPushed: number;
  interruptionDiscards: number;
  endReason?: string;
}

export class WhatsAppCallSession extends EventEmitter {
  public readonly callId: string;
  public readonly peer: string;
  public readonly callCreator?: string;
  public readonly callerName?: string;
  public readonly isGroup: boolean;
  public readonly isVideo: boolean;
  public readonly direction: "inbound" | "outbound";
  public state: CallState = "IDLE";
  public startTime: number = 0;
  public connectTime?: number;
  public endTime?: number;
  public endReason?: string;

  private inboundFrames: number = 0;
  private outboundFrames: number = 0;
  private discards: number = 0;

  constructor(params: {
    callId: string;
    peer: string;
    callCreator?: string;
    callerName?: string;
    isGroup?: boolean;
    isVideo?: boolean;
    direction: "inbound" | "outbound";
  }) {
    super();
    this.callId = params.callId;
    this.peer = params.peer;
    this.callCreator = params.callCreator;
    this.callerName = params.callerName;
    this.isGroup = params.isGroup ?? false;
    this.isVideo = params.isVideo ?? false;
    this.direction = params.direction;
    this.startTime = Date.now();
  }

  public setState(nextState: CallState): void {
    const prevState = this.state;
    if (prevState === nextState) return;

    this.state = nextState;

    if (nextState === "CONNECTED" && !this.connectTime) {
      this.connectTime = Date.now();
      this.emit("connected");
    } else if (nextState === "RINGING") {
      this.emit("ringing");
    } else if (nextState === "ENDED" || nextState === "FAILED") {
      if (!this.endTime) this.endTime = Date.now();
      this.emit("ended", this.endReason);
    }

    this.emit("stateChange", { from: prevState, to: nextState });
  }

  public recordInboundFrame(): void {
    this.inboundFrames++;
  }

  public recordOutboundFrame(): void {
    this.outboundFrames++;
  }

  public recordInterruptionDiscard(): void {
    this.discards++;
  }

  public markEnded(reason: string): void {
    this.endReason = reason;
    this.endTime = Date.now();
    this.setState("ENDED");
  }

  public markFailed(reason: string): void {
    this.endReason = reason;
    this.endTime = Date.now();
    this.setState("FAILED");
  }

  public toWhatsAppCall(): WhatsAppCall {
    const durationSec = this.connectTime
      ? Math.max(0, Math.floor(((this.endTime || Date.now()) - this.connectTime) / 1000))
      : 0;

    return {
      callId: this.callId,
      peer: this.peer,
      callCreator: this.callCreator,
      callerName: this.callerName,
      isGroup: this.isGroup,
      isVideo: this.isVideo,
      state: this.state,
      direction: this.direction,
      startTime: this.startTime,
      connectTime: this.connectTime,
      endTime: this.endTime,
      durationSec,
      endReason: this.endReason,
    };
  }

  public getMetrics(): CallMetrics {
    const durationMs = this.connectTime
      ? (this.endTime || Date.now()) - this.connectTime
      : 0;

    return {
      durationMs,
      inboundFramesCount: this.inboundFrames,
      outboundFramesPushed: this.outboundFrames,
      interruptionDiscards: this.discards,
      endReason: this.endReason,
    };
  }

  public waitForEnd(): Promise<string> {
    if (this.state === "ENDED" || this.state === "FAILED") {
      return Promise.resolve(this.endReason || "Completed");
    }
    return new Promise((resolve) => {
      this.once("ended", (reason) => resolve(reason || "Completed"));
    });
  }
}
