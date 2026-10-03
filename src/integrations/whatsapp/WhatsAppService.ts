/**
 * Unified WhatsApp Subsystem Service Facade for JARVIS.
 *
 * Integrates:
 * - Upstream oxidezap/whatsapp-rust WASM engine
 * - Durable authentication and credentials lifecycle
 * - Single-instance session locking
 * - Strict PN / LID identity resolution
 * - Rich messaging (text, files, voice notes, reactions)
 * - Call orchestration (signaling, auto-answer, rejection, full-duplex VoIP audio bridge)
 * - Diagnostics and health checks
 */

import { resolve } from "node:path";
import { WhatsAppAuth } from "./WhatsAppAuth.js";
import { WhatsAppController } from "./WhatsAppController.js";
import { WhatsAppContacts } from "./WhatsAppContacts.js";
import { WhatsAppMessages } from "./WhatsAppMessages.js";
import { WhatsAppCallManager } from "./WhatsAppCallManager.js";
import { WhatsAppAudioBridge } from "./WhatsAppAudioBridge.js";
import { WhatsAppGateway } from "./WhatsAppGateway.js";
import type {
  WhatsAppConfig,
  WhatsAppStatus,
  WhatsAppMessageResult,
  WhatsAppCallResult,
  WhatsAppCall,
  AudioLoopbackDiagnostics,
} from "./types.js";

export class WhatsAppService {
  public readonly config: WhatsAppConfig;
  public readonly auth: WhatsAppAuth;
  public readonly controller: WhatsAppController;
  public readonly contacts: WhatsAppContacts;
  public readonly messages: WhatsAppMessages;
  public readonly calls: WhatsAppCallManager;
  public readonly audio: WhatsAppAudioBridge;
  public readonly gateway: WhatsAppGateway;

  constructor(partialConfig?: Partial<WhatsAppConfig>, silent: boolean = false) {
    const workspaceDir = resolve(
      partialConfig?.workspaceDir || process.env.WORKSPACE_DIR || process.cwd()
    );
    const authDir = resolve(
      partialConfig?.authDir ||
      process.env.WHATSAPP_RUST_AUTH_DIR ||
      process.env.WHATSAPP_AUTH_DIR ||
      resolve(workspaceDir, "data", "whatsapp", "rust", "auth")
    );
    const lockFilePath = resolve(
      partialConfig?.lockFilePath ||
      process.env.WHATSAPP_RUST_LOCK_PATH ||
      resolve(workspaceDir, "data", "whatsapp", "rust", "lock", "whatsapp.lock")
    );
    const dbPath = resolve(
      partialConfig?.dbPath ||
      process.env.WHATSAPP_DB_PATH ||
      resolve(workspaceDir, "data", "whatsapp", "whatsapp.db")
    );
    const logsDir = resolve(
      partialConfig?.logsDir ||
      resolve(workspaceDir, "data", "whatsapp", "rust", "logs")
    );

    this.config = {
      workspaceDir,
      authDir,
      lockFilePath,
      dbPath,
      contactsFilePath: partialConfig?.contactsFilePath,
      defaultCountryCode: partialConfig?.defaultCountryCode || process.env.DEFAULT_COUNTRY_CODE || "91",
      geminiApiKey: partialConfig?.geminiApiKey || process.env.GEMINI_API_KEY,
      geminiModel: partialConfig?.geminiModel || process.env.GEMINI_MODEL || "gemini-3.8-live",
      voiceName: partialConfig?.voiceName || "Aoede",
      callTimeoutMs: partialConfig?.callTimeoutMs || 120000,
      callLanguage: partialConfig?.callLanguage || "hinglish",
      conversationMode: partialConfig?.conversationMode || "MESSAGE_DELIVERY",
      logsDir,
    };

    this.auth = new WhatsAppAuth({
      authDir: this.config.authDir,
      workspaceDir: this.config.workspaceDir,
    });

    this.controller = new WhatsAppController({
      config: this.config,
      auth: this.auth,
      silent,
    });

    this.contacts = new WhatsAppContacts({
      contactsFilePath: this.config.contactsFilePath,
      defaultCountryCode: this.config.defaultCountryCode,
      dbPath: this.config.dbPath,
    });

    this.messages = new WhatsAppMessages(
      () => this.controller.getSocket(),
      this.contacts
    );

    this.calls = new WhatsAppCallManager({
      getSocket: () => this.controller.getSocket(),
      contacts: this.contacts,
    });

    this.audio = new WhatsAppAudioBridge();
    this.gateway = new WhatsAppGateway(this.controller, this.calls);
  }

  public async connect(): Promise<void> {
    const socket = await this.controller.connect();
    this.calls.attachSocketListeners(socket);
  }

  public disconnect(): void {
    this.controller.disconnect();
  }

  public getStatus(): WhatsAppStatus {
    return this.controller.getStatus(this.calls.activeCallCount);
  }

  public hasPersistedSession(): boolean {
    return this.auth.hasPersistedCredentials();
  }

  public logout(): void {
    this.controller.disconnect();
    this.auth.clearSession();
  }

  public async sendText(
    target: string,
    text: string,
    replyToId?: string
  ): Promise<WhatsAppMessageResult> {
    return await this.messages.sendText(target, text, replyToId);
  }

  public async sendFile(
    target: string,
    filePath: string,
    caption?: string,
    fileAs?: string
  ): Promise<WhatsAppMessageResult> {
    return await this.messages.sendFile(target, filePath, caption, fileAs);
  }

  public async sendVoice(target: string, filePath: string): Promise<WhatsAppMessageResult> {
    return await this.messages.sendVoice(target, filePath);
  }

  public async sendReaction(
    target: string,
    messageId: string,
    emoji: string
  ): Promise<WhatsAppMessageResult> {
    return await this.messages.sendReaction(target, messageId, emoji);
  }

  public async markRead(chatJid: string, messageId?: string): Promise<WhatsAppMessageResult> {
    return await this.messages.markRead(chatJid, messageId);
  }

  public async checkNumber(phone: string): Promise<{ phone: string; exists: boolean; jid?: string }> {
    return await this.messages.checkNumber(phone);
  }

  public getActiveCalls(): WhatsAppCall[] {
    return this.calls.getActiveCalls();
  }

  public async rejectCall(callId: string): Promise<boolean> {
    return await this.calls.rejectCall(callId);
  }

  public answerCall(callId: string) {
    return this.calls.answerCall(callId);
  }

  public async terminateCall(callId: string, reason?: string): Promise<boolean> {
    return await this.calls.terminateCall(callId, reason);
  }

  public async runAudioDiagnostics(): Promise<AudioLoopbackDiagnostics> {
    return await this.audio.runLoopbackDiagnostics();
  }
}
