/**
 * Event topic definitions and types for WhatsApp subsystem.
 */

export const WHATSAPP_EVENTS = {
  CONNECTED: "whatsapp.connected",
  DISCONNECTED: "whatsapp.disconnected",
  QR: "whatsapp.qr",
  MESSAGE_RECEIVED: "whatsapp.message_received",
  MESSAGE_SENT: "whatsapp.message_sent",
  CALL_INCOMING: "whatsapp.call_incoming",
  CALL_RINGING: "whatsapp.call_ringing",
  CALL_CONNECTED: "whatsapp.call_connected",
  CALL_ENDED: "whatsapp.call_ended",
} as const;

export type WhatsAppEventTopic = typeof WHATSAPP_EVENTS[keyof typeof WHATSAPP_EVENTS];

export interface WhatsAppEventPayload<T = any> {
  topic: WhatsAppEventTopic;
  timestamp: number;
  data: T;
}
