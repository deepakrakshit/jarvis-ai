/**
 * Event bus and gateway adapter bridging WhatsApp runtime events
 * into the JARVIS cognitive architecture.
 */

import { EventEmitter } from "node:events";
import { WHATSAPP_EVENTS, type WhatsAppEventPayload, type WhatsAppEventTopic } from "./WhatsAppEvents.js";
import type { WhatsAppController } from "./WhatsAppController.js";
import type { WhatsAppCallManager } from "./WhatsAppCallManager.js";

export class WhatsAppGateway extends EventEmitter {
  private readonly controller: WhatsAppController;
  private readonly callManager: WhatsAppCallManager;

  constructor(controller: WhatsAppController, callManager: WhatsAppCallManager) {
    super();
    this.controller = controller;
    this.callManager = callManager;
    this.bindEvents();
  }

  private bindEvents(): void {
    this.callManager.on("incomingCall", (session) => {
      this.publish(WHATSAPP_EVENTS.CALL_INCOMING, session.toWhatsAppCall());
    });

    this.callManager.on("callEnded", (session) => {
      this.publish(WHATSAPP_EVENTS.CALL_ENDED, session.toWhatsAppCall());
    });
  }

  public publish<T>(topic: WhatsAppEventTopic, data: T): void {
    const payload: WhatsAppEventPayload<T> = {
      topic,
      timestamp: Date.now(),
      data,
    };
    this.emit(topic, payload);
    this.emit("event", payload);
  }
}
