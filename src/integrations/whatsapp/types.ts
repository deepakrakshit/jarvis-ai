/**
 * Type definitions for the JARVIS WhatsApp subsystem powered by oxidezap/whatsapp-rust.
 */

export type CallState =
  | "IDLE"
  | "RINGING"
  | "ANSWERING"
  | "CONNECTING"
  | "CONNECTED"
  | "DEGRADED"
  | "ENDING"
  | "ENDED"
  | "FAILED";

export interface WhatsAppStatus {
  connected: boolean;
  authenticated: boolean;
  userJid: string | null;
  userPhone: string | null;
  pushName: string | null;
  platform: string;
  uptimeSec: number;
  lastSyncTimestamp: number | null;
  authDir: string;
  activeCallsCount: number;
}

export interface WhatsAppRecipient {
  raw: string;
  jid: string;
  phone: string;
  lid?: string;
  name?: string;
  isGroup: boolean;
}

export interface WhatsAppMessageResult {
  success: boolean;
  messageId?: string;
  chatJid: string;
  timestamp: number;
  error?: string;
}

export interface WhatsAppCall {
  callId: string;
  peer: string;
  callCreator?: string;
  callerName?: string;
  isGroup: boolean;
  isVideo: boolean;
  state: CallState;
  direction: "inbound" | "outbound";
  startTime: number;
  connectTime?: number;
  endTime?: number;
  durationSec: number;
  endReason?: string;
}

export interface WhatsAppCallOptions {
  target: string;
  objective: string;
  mode?: string;
  durationMs?: number;
  autoAnswer?: boolean;
  geminiApiKey?: string;
  geminiModel?: string;
  voiceName?: string;
}

export interface WhatsAppCallResult {
  success: boolean;
  callId: string;
  targetNumber: string;
  durationMs: number;
  transcriptPath?: string;
  summaryPath?: string;
  summary?: string;
  recipientReply?: string;
  error?: string;
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

export interface AudioLoopbackDiagnostics {
  success: boolean;
  packetsSent: number;
  packetsReceived: number;
  roundtripLatencyMs: number;
  jitterMs: number;
  packetLossPercent: number;
}

export interface WhatsAppConfig {
  workspaceDir: string;
  authDir: string;
  lockFilePath: string;
  dbPath: string;
  contactsFilePath?: string;
  defaultCountryCode: string;
  geminiApiKey?: string;
  geminiModel: string;
  voiceName: string;
  callTimeoutMs: number;
  callLanguage: string;
  conversationMode: string;
  logsDir: string;
}
