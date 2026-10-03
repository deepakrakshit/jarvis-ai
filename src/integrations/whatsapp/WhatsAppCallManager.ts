/**
 * High-level call orchestrator managing call offers, auto-answer policies,
 * rejection, termination, and state tracking.
 */

import { EventEmitter } from "node:events";
import { WhatsAppCallSession } from "./WhatsAppCalls.js";
import type { WhatsAppContacts } from "./WhatsAppContacts.js";
import type { WhatsAppCall } from "./types.js";

export interface CallManagerOptions {
  getSocket: () => any;
  contacts: WhatsAppContacts;
  autoAnswerWhitelist?: string[];
  autoRejectUnknown?: boolean;
}

export class WhatsAppCallManager extends EventEmitter {
  private readonly getSocket: () => any;
  private readonly contacts: WhatsAppContacts;
  private readonly autoAnswerWhitelist: Set<string>;
  private readonly autoRejectUnknown: boolean;
  private readonly activeCalls: Map<string, WhatsAppCallSession> = new Map();

  constructor(options: CallManagerOptions) {
    super();
    this.getSocket = options.getSocket;
    this.contacts = options.contacts;
    this.autoAnswerWhitelist = new Set(
      (options.autoAnswerWhitelist || []).map((x) => x.replace(/\D/g, ""))
    );
    this.autoRejectUnknown = options.autoRejectUnknown ?? false;
  }

  /**
   * Bind event listener for incoming call signals from the socket.
   */
  public attachSocketListeners(socket: any): void {
    if (!socket?.ev) return;

    socket.ev.on("call", async (calls: any[]) => {
      for (const callEvent of calls) {
        await this.handleIncomingCallEvent(callEvent);
      }
    });
  }

  private async handleIncomingCallEvent(callEvent: any): Promise<void> {
    const callId = callEvent.id || callEvent.callId;
    const peer = callEvent.from || callEvent.chatId || callEvent.peer;
    const status = callEvent.status;
    const isGroup = !!callEvent.isGroup;
    const isVideo = !!callEvent.isVideo;

    let session = this.activeCalls.get(callId);

    if (status === "offer") {
      let callerName: string | undefined;
      try {
        const resolved = this.contacts.resolveRecipient(peer);
        callerName = resolved.name;
      } catch {}

      session = new WhatsAppCallSession({
        callId,
        peer,
        callCreator: callEvent.offline ? undefined : peer,
        callerName,
        isGroup,
        isVideo,
        direction: "inbound",
      });
      session.setState("RINGING");
      this.activeCalls.set(callId, session);

      this.emit("incomingCall", session);

      const cleanPeerPhone = peer.replace(/\D/g, "");
      const isWhitelisted = this.autoAnswerWhitelist.has(cleanPeerPhone);

      if (isWhitelisted) {
        this.emit("autoAnswerRequested", session);
      } else if (this.autoRejectUnknown) {
        await this.rejectCall(callId);
      }
    } else if (status === "ringing" && session) {
      session.setState("RINGING");
    } else if (status === "accept" && session) {
      session.setState("CONNECTED");
    } else if ((status === "terminate" || status === "reject" || status === "timeout") && session) {
      session.markEnded(status);
      this.activeCalls.delete(callId);
      this.emit("callEnded", session);
    }
  }

  /**
   * Reject an incoming call via upstream WASM rejectCall.
   */
  public async rejectCall(callId: string): Promise<boolean> {
    const session = this.activeCalls.get(callId);
    const sock = this.getSocket();

    if (sock && typeof sock.rejectCall === "function" && session) {
      try {
        await sock.rejectCall(session.callId, session.peer, session.callCreator || session.peer);
        session.markEnded("rejected_by_local");
        this.activeCalls.delete(callId);
        return true;
      } catch (err) {
        console.error(`[WhatsAppCallManager] Error rejecting call ${callId}:`, err);
      }
    }

    if (session) {
      session.markEnded("rejected_locally");
      this.activeCalls.delete(callId);
      return true;
    }

    return false;
  }

  /**
   * Mark a call as answered.
   */
  public answerCall(callId: string): WhatsAppCallSession | null {
    const session = this.activeCalls.get(callId);
    if (!session) return null;

    session.setState("ANSWERING");
    session.setState("CONNECTED");
    return session;
  }

  /**
   * Terminate an active call.
   */
  public async terminateCall(callId: string, reason: string = "hung_up_locally"): Promise<boolean> {
    const session = this.activeCalls.get(callId);
    if (!session) return false;

    session.setState("ENDING");
    session.markEnded(reason);
    this.activeCalls.delete(callId);
    this.emit("callEnded", session);
    return true;
  }

  /**
   * Create an outbound call session.
   */
  public createOutboundCall(target: string, isVideo: boolean = false): WhatsAppCallSession {
    const recipient = this.contacts.resolveRecipient(target);
    const callId = `outbound_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;

    const session = new WhatsAppCallSession({
      callId,
      peer: recipient.jid,
      callerName: recipient.name,
      isVideo,
      direction: "outbound",
    });

    this.activeCalls.set(callId, session);
    return session;
  }

  public getCall(callId: string): WhatsAppCallSession | undefined {
    return this.activeCalls.get(callId);
  }

  public getActiveCalls(): WhatsAppCall[] {
    return Array.from(this.activeCalls.values()).map((s) => s.toWhatsAppCall());
  }

  public get activeCallCount(): number {
    return this.activeCalls.size;
  }
}
