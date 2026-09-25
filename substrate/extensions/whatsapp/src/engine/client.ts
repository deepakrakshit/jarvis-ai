/**
 * WhatsApp client manager.
 *
 * Coordinates initialization, QR login, session persistence,
 * outbound call creation, contact/chat/message synchronization,
 * and protocol-based messaging with verified delivery state tracking.
 */

import { VoipClient } from "../voip/index.mjs";
import { CallSession } from "./call.js";
import { existsSync, readdirSync, readFileSync, rmSync, mkdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { EventEmitter } from "node:events";
import { downloadMediaMessage } from "@whiskeysockets/baileys";
import { installSecuritySanitizer } from "../security/sanitizer.js";
import { loadConfig } from "../config.js";
import {
  WhatsAppStore,
  type ContactRecord,
  type ChatRecord,
  type MessageRecord,
  type StoreStats,
} from "../store/database.js";

// Ensure cryptographic sanitizer is active whenever WhatsApp client is initialized
installSecuritySanitizer();

export interface WhatsAppManagerOptions {
  authDir: string;
  dbPath?: string;
  onQrCode?: (qr: string) => void;
  silent?: boolean;
}

export interface SendResult {
  success: boolean;
  sent: boolean;
  messageId?: string;
  targetJid?: string;
  deliveryState?: "QUEUED" | "WHATSAPP_ACKNOWLEDGED" | "DELIVERED" | "READ" | "FAILED";
  statusMessage?: string;
  error?: string;
}

export interface SyncDiagnostics {
  connection_state: string;
  authenticated: boolean;
  auth_dir: string;
  database_path: string;
  contacts_count: number;
  chats_count: number;
  messages_count: number;
  unread_chats_count: number;
  unread_messages_count: number;
  last_sync_event?: Record<string, any>;
  last_message_event?: Record<string, any>;
}

export class WhatsAppManager {
  readonly #authDir: string;
  readonly #dbPath: string;
  readonly #onQrCode?: (qr: string) => void;
  readonly #silent: boolean;
  #client: VoipClient | null = null;
  #store: WhatsAppStore | null = null;
  #connected = false;
  #connectionState = "disconnected";
  #lastSyncEvent?: Record<string, any>;
  #lastMessageEvent?: Record<string, any>;
  readonly #deliveryEmitter = new EventEmitter();
  readonly #rawMessageCache = new Map<string, any>();

  constructor(options: WhatsAppManagerOptions) {
    this.#authDir = options.authDir;
    // Always default to canonical workspace data/whatsapp/whatsapp.db unless explicitly overridden
    this.#dbPath =
      options.dbPath ||
      process.env.WHATSAPP_DB_PATH ||
      loadConfig().whatsappDbPath;
    this.#onQrCode = options.onQrCode;
    this.#silent = options.silent ?? false;
  }

  get isConnected(): boolean {
    return this.#connected;
  }

  get connectionState(): string {
    return this.#connectionState;
  }

  get hasPersistedAuth(): boolean {
    if (!existsSync(this.#authDir)) return false;
    try {
      const files = readdirSync(this.#authDir);
      return files.some((f) => f.startsWith("creds.json") || f.endsWith(".json"));
    } catch {
      return false;
    }
  }

  get store(): WhatsAppStore {
    if (!this.#store) {
      this.#store = new WhatsAppStore(this.#dbPath);
    }
    return this.#store;
  }

  get socket(): any {
    return this.#client?.socket ?? null;
  }

  /**
   * Connect to WhatsApp with interactive or silent authentication.
   * Registers event handlers directly onto socket creation hook so NO initial sync events are lost.
   */
  async connect(): Promise<void> {
    if (this.#connected && this.#client) return;

    // Pre-sync any persisted LID reverse files from auth into SQLite
    try {
      this.store.syncAuthLidMappings(this.#authDir);
      this.store.migrateAllLids();
      this.store.reconcileUnreadChats();
    } catch {}

    this.#client = new VoipClient({
      authDir: this.#authDir,
      onQrCode: this.#onQrCode,
      silent: this.#silent,
      onSocketCreated: (sock: any) => this.#attachSocketListeners(sock),
    });

    await this.#client.connect();
    this.#connected = true;
    this.#connectionState = "connected";

    // Re-sync LID mappings once auth directory is fully verified
    try {
      this.store.syncAuthLidMappings(this.#authDir);
      this.store.migrateAllLids();
      this.store.reconcileUnreadChats();
    } catch {}

    // Ensure listeners are also attached to the current socket if created earlier
    const sock = this.#client.socket;
    if (sock) {
      this.#attachSocketListeners(sock);
    }
  }

  #attachSocketListeners(sock: any): void {
    if (!sock || !sock.ev) return;
    const store = this.store;

    // Track connection state
    sock.ev.on("connection.update", (update: any) => {
      if (update.connection) {
        this.#connectionState = update.connection;
        console.log(`[WhatsApp:connection.update] State: ${update.connection}`);
      }
    });

    // 1. messaging-history.set (Initial history sync & milestone syncs)
    sock.ev.on("messaging-history.set", (history: any) => {
      try {
        const { chats, contacts, messages, lidPnMappings, isLatest, syncType } = history;
        const chatsList = Array.isArray(chats) ? chats : [];
        const contactsList = Array.isArray(contacts) ? contacts : [];
        const messagesList = Array.isArray(messages) ? messages : [];
        const lidList = Array.isArray(lidPnMappings) ? lidPnMappings : [];

        console.log(
          `[WhatsApp:messaging-history.set] Arrived: ${chatsList.length} chats, ` +
          `${contactsList.length} contacts, ${messagesList.length} messages, ` +
          `${lidList.length} LID mappings (syncType: ${syncType}, isLatest: ${isLatest})`
        );

        store.upsertBatch((s) => {
          // Persist contacts
          for (const c of contactsList) {
            const jid = this.normalizeJid(c.id);
            if (!jid) continue;
            const phone = jid.endsWith("@s.whatsapp.net") ? jid.replace("@s.whatsapp.net", "") : undefined;
            s.upsertContact({
              jid,
              phone,
              push_name: c.notify || undefined,
              full_name: c.name || undefined,
              business_name: c.verifiedName || undefined,
            });
            if (c.lid) {
              const lidJid = this.normalizeJid(c.lid);
              if (lidJid && phone) {
                s.upsertLidMapping(lidJid, phone);
              }
            }
          }

          // Persist explicit LID mappings
          for (const m of lidList) {
            if (m && m.lid && m.pn) {
              s.upsertLidMapping(this.normalizeJid(m.lid), m.pn);
            }
          }

          // Persist chats with accurate unread counts
          for (const ch of chatsList) {
            const jid = this.normalizeJid(ch.id);
            if (!jid) continue;
            const kind = jid.endsWith("@g.us")
              ? "group"
              : jid.endsWith("@broadcast")
              ? "broadcast"
              : jid.endsWith("@newsletter")
              ? "newsletter"
              : "dm";
            const unreadCount = typeof ch.unreadCount === "number" ? ch.unreadCount : 0;
            s.upsertChat({
              jid,
              kind,
              name: ch.name || undefined,
              unread: unreadCount > 0 ? 1 : 0,
              unread_count: Math.max(0, unreadCount),
              archived: ch.archived ? 1 : 0,
              pinned: ch.pinned ? 1 : 0,
              last_message_ts: typeof ch.conversationTimestamp === "number" ? ch.conversationTimestamp : undefined,
            });
          }

          // Persist messages
          for (const m of messagesList) {
            const record = this.#parseMessage(m);
            if (record) {
              s.upsertMessage(record);
            }
          }
        });

        this.#lastSyncEvent = {
          type: "messaging-history.set",
          ts: Date.now(),
          chats: chatsList.length,
          contacts: contactsList.length,
          messages: messagesList.length,
          syncType,
        };
        console.log(`[WhatsApp:messaging-history.set] Successfully committed to database mirror.`);
      } catch (err) {
        console.error("[WhatsApp:messaging-history.set] Error processing history:", err);
      }
    });

    // 2. messaging-history.status
    sock.ev.on("messaging-history.status", (status: any) => {
      console.log(`[WhatsApp:messaging-history.status] syncType: ${status?.syncType}, status: ${status?.status}, explicit: ${status?.explicit}`);
      this.#lastSyncEvent = {
        type: "messaging-history.status",
        ts: Date.now(),
        status: status?.status,
        syncType: status?.syncType,
      };
    });

    // 3. contacts.upsert
    sock.ev.on("contacts.upsert", (contacts: any[]) => {
      try {
        console.log(`[WhatsApp:contacts.upsert] Received ${contacts.length} contacts.`);
        store.upsertBatch((s) => {
          for (const c of contacts) {
            const jid = this.normalizeJid(c.id);
            if (!jid) continue;
            const phone = jid.endsWith("@s.whatsapp.net") ? jid.replace("@s.whatsapp.net", "") : undefined;
            s.upsertContact({
              jid,
              phone,
              push_name: c.notify || undefined,
              full_name: c.name || undefined,
              business_name: c.verifiedName || undefined,
            });
            if (c.lid) {
              const lidJid = this.normalizeJid(c.lid);
              if (lidJid && phone) {
                s.upsertLidMapping(lidJid, phone);
              }
            }
          }
        });
      } catch (err) {
        console.error("[WhatsApp:contacts.upsert] Error:", err);
      }
    });

    // 4. contacts.update
    sock.ev.on("contacts.update", (updates: any[]) => {
      try {
        console.log(`[WhatsApp:contacts.update] Received ${updates.length} contact updates.`);
        store.upsertBatch((s) => {
          for (const u of updates) {
            const jid = this.normalizeJid(u.id);
            if (!jid) continue;
            const phone = jid.endsWith("@s.whatsapp.net") ? jid.replace("@s.whatsapp.net", "") : undefined;
            s.upsertContact({
              jid,
              phone,
              push_name: u.notify || undefined,
              full_name: u.name || undefined,
              business_name: u.verifiedName || undefined,
            });
          }
        });
      } catch (err) {
        console.error("[WhatsApp:contacts.update] Error:", err);
      }
    });

    // 5. lid-mapping.update
    sock.ev.on("lid-mapping.update", (mapping: any) => {
      try {
        console.log(`[WhatsApp:lid-mapping.update] Received LID mapping.`);
        const mappings = Array.isArray(mapping) ? mapping : [mapping];
        for (const m of mappings) {
          if (m && m.lid && m.pn) {
            store.upsertLidMapping(this.normalizeJid(m.lid), m.pn);
          }
        }
        store.migrateAllLids();
        store.reconcileUnreadChats();
      } catch (err) {
        console.error("[WhatsApp:lid-mapping.update] Error:", err);
      }
    });

    // 6. chats.upsert
    sock.ev.on("chats.upsert", (chats: any[]) => {
      try {
        console.log(`[WhatsApp:chats.upsert] Received ${chats.length} chats.`);
        store.upsertBatch((s) => {
          for (const ch of chats) {
            const jid = this.normalizeJid(ch.id);
            if (!jid || jid === "status@broadcast" || jid.endsWith("@broadcast")) continue;
            let kind: "dm" | "group" | "broadcast" | "newsletter" | "unknown" = "dm";
            if (jid.endsWith("@g.us")) kind = "group";
            else if (jid.endsWith("@newsletter")) kind = "newsletter";

            const unreadCount = typeof ch.unreadCount === "number" ? ch.unreadCount : 0;
            s.upsertChat({
              jid,
              kind,
              name: ch.name || undefined,
              unread: unreadCount > 0 ? 1 : 0,
              unread_count: Math.max(0, unreadCount),
              archived: ch.archived ? 1 : 0,
              pinned: ch.pinned ? 1 : 0,
              last_message_ts: typeof ch.conversationTimestamp === "number" ? ch.conversationTimestamp : undefined,
            });
          }
        });
      } catch (err) {
        console.error("[WhatsApp:chats.upsert] Error:", err);
      }
    });

    // 7. chats.update
    sock.ev.on("chats.update", (updates: any[]) => {
      try {
        console.log(`[WhatsApp:chats.update] Received ${updates.length} chat updates.`);
        store.upsertBatch((s) => {
          for (const u of updates) {
            const jid = this.normalizeJid(u.id);
            if (!jid || jid === "status@broadcast" || jid.endsWith("@broadcast")) continue;
            let kind: "dm" | "group" | "broadcast" | "newsletter" | "unknown" = "dm";
            if (jid.endsWith("@g.us")) kind = "group";
            else if (jid.endsWith("@newsletter")) kind = "newsletter";

            const unreadCount = typeof u.unreadCount === "number" ? u.unreadCount : undefined;
            s.upsertChat({
              jid,
              kind,
              name: u.name || undefined,
              unread: unreadCount !== undefined ? (unreadCount > 0 ? 1 : 0) : undefined,
              unread_count: unreadCount !== undefined ? Math.max(0, unreadCount) : undefined,
              archived: u.archived !== undefined ? (u.archived ? 1 : 0) : undefined,
              pinned: u.pinned !== undefined ? (u.pinned ? 1 : 0) : undefined,
              last_message_ts: typeof u.conversationTimestamp === "number" ? u.conversationTimestamp : undefined,
            });
          }
        });
      } catch (err) {
        console.error("[WhatsApp:chats.update] Error:", err);
      }
    });

    // 8. messages.upsert (Process EVERY message in the batch)
    sock.ev.on("messages.upsert", ({ messages, type }: { messages: any[]; type: string }) => {
      try {
        console.log(`[WhatsApp:messages.upsert] Processing ${messages.length} messages (type: ${type}).`);
        store.upsertBatch((s) => {
          for (const m of messages) {
            const record = this.#parseMessage(m);
            if (record) {
              const cacheKey = `${record.chat_jid}:${record.msg_id}`;
              this.#rawMessageCache.set(cacheKey, m);

              // Status stories belong in status_messages, never in personal chats
              if (record.chat_jid === "status@broadcast" || record.chat_jid.endsWith("@broadcast")) {
                s.upsertStatusMessage({
                  msg_id: record.msg_id,
                  ts: record.ts,
                  from_me: record.from_me,
                  sender_jid: record.sender_jid,
                  sender_name: record.sender_name,
                  text: record.text,
                  media_type: record.media_type,
                  media_caption: record.media_caption,
                  filename: record.filename,
                  mime_type: record.mime_type,
                  local_path: record.local_path,
                });
                continue;
              }

              const alreadyExists = s.hasMessage(record.chat_jid, record.msg_id);
              s.upsertMessage(record);

              if (record.from_me) {
                s.clearChatUnreadThrough(record.chat_jid, record.ts);
              }

              // Auto-download media for live notify events in background
              if (type === "notify" && (m.message?.imageMessage || m.message?.audioMessage)) {
                void this.downloadMedia(record.chat_jid, record.msg_id).catch(() => {});
              }

              if (
                type === "notify" &&
                !record.from_me &&
                !alreadyExists &&
                !record.reaction_to_id &&
                !record.reaction_emoji &&
                !record.revoked &&
                record.chat_jid !== "status@broadcast" &&
                !record.chat_jid.endsWith("@broadcast") &&
                !record.chat_jid.endsWith("@newsletter")
              ) {
                s.incrementChatUnread(record.chat_jid, record.ts, record.sender_name);
              }
            }
          }
        });
        this.#lastMessageEvent = {
          type: "messages.upsert",
          count: messages.length,
          subType: type,
          ts: Date.now(),
        };
      } catch (err) {
        console.error("[WhatsApp:messages.upsert] Error:", err);
      }
    });

    // 9. messages.update (Delivery acknowledgments: SERVER_ACK, DELIVERY_ACK, READ)
    sock.ev.on("messages.update", (updates: any[]) => {
      try {
        console.log(`[WhatsApp:messages.update] Received ${updates.length} message updates.`);
        for (const item of updates) {
          const key = item.key || {};
          const msgId = key.id;
          const chatJid = this.normalizeJid(key.remoteJid);
          if (!msgId || !chatJid) continue;

          const status = item.update?.status;
          if (status !== undefined) {
            let deliveryState: "WHATSAPP_ACKNOWLEDGED" | "DELIVERED" | "READ" = "WHATSAPP_ACKNOWLEDGED";
            if (status >= 4) {
              deliveryState = "READ";
            } else if (status === 3) {
              deliveryState = "DELIVERED";
            } else if (status === 2) {
              deliveryState = "WHATSAPP_ACKNOWLEDGED";
            }
            store.updateMessageDeliveryStatus(chatJid, msgId, deliveryState);
            this.#deliveryEmitter.emit(msgId, deliveryState);
          }
        }
      } catch (err) {
        console.error("[WhatsApp:messages.update] Error:", err);
      }
    });

    // 10. message-receipt.update (Delivery / Read receipt timestamps)
    sock.ev.on("message-receipt.update", (receipts: any[]) => {
      try {
        console.log(`[WhatsApp:message-receipt.update] Received ${receipts.length} receipt updates.`);
        for (const item of receipts) {
          const key = item.key || {};
          const msgId = key.id;
          const chatJid = this.normalizeJid(key.remoteJid);
          if (!msgId || !chatJid) continue;

          const r = item.receipt || {};
          let deliveryState: "WHATSAPP_ACKNOWLEDGED" | "DELIVERED" | "READ" = "WHATSAPP_ACKNOWLEDGED";
          let ts: number | undefined;

          if (r.readTimestamp) {
            deliveryState = "READ";
            ts = Number(r.readTimestamp);
          } else if (r.receiptTimestamp) {
            deliveryState = "DELIVERED";
            ts = Number(r.receiptTimestamp);
          }

          store.updateMessageDeliveryStatus(chatJid, msgId, deliveryState, ts);
          this.#deliveryEmitter.emit(msgId, deliveryState);
        }
      } catch (err) {
        console.error("[WhatsApp:message-receipt.update] Error:", err);
      }
    });

    // 11. call
    sock.ev.on("call", (calls: any[]) => {
      try {
        for (const c of calls) {
          store.insertCallEvent({
            chat_jid: this.normalizeJid(c.chatId || c.from || "") || "unknown",
            sender_jid: this.normalizeJid(c.from || "") || undefined,
            call_id: c.id,
            event_type: c.status || "call",
            direction: c.isGroup ? "group" : "1:1",
            media: c.isVideo ? "video" : "audio",
            ts: Math.floor(Date.now() / 1000),
          });
        }
      } catch (err) {
        console.error("[WhatsAppManager] Error on call event:", err);
      }
    });
  }

  normalizeJid(jid?: string): string {
    if (!jid) return "";
    const clean = jid.trim();
    if (clean.includes("@")) {
      const at = clean.indexOf("@");
      const user = clean.slice(0, at).split(":")[0];
      const domain = clean.slice(at + 1);
      const combined = `${user}@${domain}`;
      if (domain === "lid") {
        const pn = this.store.resolveLidToPn(combined);
        if (pn) {
          const digits = pn.replace(/\D/g, "");
          return `${digits}@s.whatsapp.net`;
        }
      }
      return combined;
    }
    const digits = clean.replace(/\D/g, "");
    if (!digits) return clean;
    return `${digits}@s.whatsapp.net`;
  }

  #parseMessage(m: any): MessageRecord | null {
    const key = m.key || {};
    const chatJid = this.normalizeJid(key.remoteJid);
    const msgId = key.id;
    if (!chatJid || !msgId) return null;

    const fromMe = key.fromMe ? 1 : 0;
    const senderJid = this.normalizeJid(key.participant) || (fromMe ? "me" : chatJid);
    const ts =
      typeof m.messageTimestamp === "number"
        ? m.messageTimestamp
        : Number(m.messageTimestamp) || Math.floor(Date.now() / 1000);
    const pushName = m.pushName || undefined;

    const msg = m.message || {};
    let text: string | undefined;
    let displayText: string | undefined;
    let mediaType: string | undefined;
    let mediaCaption: string | undefined;
    let filename: string | undefined;
    let mimeType: string | undefined;
    let quotedMsgId: string | undefined;
    let quotedSenderJid: string | undefined;
    let reactionToId: string | undefined;
    let reactionEmoji: string | undefined;

    if (msg.conversation) {
      text = msg.conversation;
      displayText = text;
    } else if (msg.extendedTextMessage) {
      text = msg.extendedTextMessage.text || "";
      displayText = text;
      const ctx = msg.extendedTextMessage.contextInfo;
      if (ctx) {
        quotedMsgId = ctx.stanzaId;
        quotedSenderJid = this.normalizeJid(ctx.participant);
      }
    } else if (msg.imageMessage) {
      mediaType = "image";
      mediaCaption = msg.imageMessage.caption;
      mimeType = msg.imageMessage.mimetype || "image/jpeg";
      text = mediaCaption || "[Image]";
      displayText = text;
    } else if (msg.documentMessage) {
      mediaType = "document";
      filename = msg.documentMessage.fileName;
      mediaCaption = msg.documentMessage.caption;
      mimeType = msg.documentMessage.mimetype;
      text = filename ? `[Document: ${filename}]` : mediaCaption || "[Document]";
      displayText = text;
    } else if (msg.audioMessage) {
      mediaType = msg.audioMessage.ptt ? "voice" : "audio";
      mimeType = msg.audioMessage.mimetype || "audio/ogg";
      text = msg.audioMessage.ptt ? "[Voice Note]" : "[Audio]";
      displayText = text;
    } else if (msg.videoMessage) {
      mediaType = "video";
      mediaCaption = msg.videoMessage.caption;
      mimeType = msg.videoMessage.mimetype;
      text = mediaCaption || "[Video]";
      displayText = text;
    } else if (msg.reactionMessage) {
      reactionToId = msg.reactionMessage.key?.id;
      reactionEmoji = msg.reactionMessage.text;
      text = `Reaction: ${reactionEmoji}`;
      displayText = text;
    }

    // Determine initial delivery status
    let deliveryStatus = fromMe ? "WHATSAPP_ACKNOWLEDGED" : "DELIVERED";
    if (m.status !== undefined) {
      if (m.status >= 4) deliveryStatus = "READ";
      else if (m.status === 3) deliveryStatus = "DELIVERED";
      else if (m.status === 2) deliveryStatus = "WHATSAPP_ACKNOWLEDGED";
    }

    return {
      chat_jid: chatJid,
      msg_id: msgId,
      sender_jid: senderJid,
      sender_name: pushName,
      ts,
      from_me: fromMe,
      text,
      display_text: displayText,
      quoted_msg_id: quotedMsgId,
      quoted_sender_jid: quotedSenderJid,
      reaction_to_id: reactionToId,
      reaction_emoji: reactionEmoji,
      media_type: mediaType,
      media_caption: mediaCaption,
      filename,
      mime_type: mimeType,
      delivery_status: deliveryStatus,
    };
  }

  /**
   * Diagnostic summary covering database counts, connection state, and event history.
   */
  getDiagnostics(): SyncDiagnostics {
    const stats = this.store.getStats();
    return {
      connection_state: this.#connectionState,
      authenticated: this.hasPersistedAuth,
      auth_dir: this.#authDir,
      database_path: this.store.dbPath,
      contacts_count: stats.contacts,
      chats_count: stats.chats,
      messages_count: stats.messages,
      unread_chats_count: stats.unread_chats,
      unread_messages_count: stats.unread_messages,
      last_sync_event: this.#lastSyncEvent,
      last_message_event: this.#lastMessageEvent,
    };
  }

  /**
   * Synchronize state by waiting for connection and recent messages/contacts.
   */
  async sync(settleMs = 5000): Promise<SyncDiagnostics> {
    await this.connect();
    // Allow history sync and background live events to populate
    await new Promise((r) => setTimeout(r, settleMs));
    return this.getDiagnostics();
  }

  /**
   * Send a text message with verified delivery state tracking.
   * Separates QUEUED -> WHATSAPP_ACKNOWLEDGED -> DELIVERED -> READ.
   */
  async sendText(
    to: string,
    message: string,
    replyTo?: string,
    waitForDeliveryMs = 2500
  ): Promise<SendResult> {
    await this.connect();
    const sock = this.#client?.socket;
    if (!sock) {
      return { success: false, sent: false, deliveryState: "FAILED", error: "Socket not available" };
    }

    const targetJid = this.normalizeJid(to);
    const content: any = { text: message };
    if (replyTo) {
      content.contextInfo = { stanzaId: replyTo };
    }

    let sentMsg: any;
    try {
      sentMsg = await sock.sendMessage(targetJid, content);
    } catch (err: any) {
      return {
        success: false,
        sent: false,
        deliveryState: "FAILED",
        error: `WhatsApp socket rejected send: ${err?.message || String(err)}`,
      };
    }

    const msgId = sentMsg?.key?.id || `msg-${Date.now()}`;

    // Record immediately in SQLite as WHATSAPP_ACKNOWLEDGED
    this.store.upsertMessage({
      chat_jid: targetJid,
      msg_id: msgId,
      sender_jid: "me",
      ts: Math.floor(Date.now() / 1000),
      from_me: 1,
      text: message,
      display_text: message,
      quoted_msg_id: replyTo,
      delivery_status: "WHATSAPP_ACKNOWLEDGED",
    });

    // Clear local unread status since operator has replied to this conversation
    this.store.markChatRead(targetJid);
    try {
      await sock.readMessages([{ remoteJid: targetJid, id: replyTo || msgId, fromMe: false }]);
    } catch {}

    // Wait for delivery receipt or update within grace window
    let currentDeliveryState: "WHATSAPP_ACKNOWLEDGED" | "DELIVERED" | "READ" = "WHATSAPP_ACKNOWLEDGED";

    if (waitForDeliveryMs > 0) {
      currentDeliveryState = await new Promise((resolveDelivery) => {
        const timer = setTimeout(() => {
          this.#deliveryEmitter.off(msgId, onDelivery);
          resolveDelivery("WHATSAPP_ACKNOWLEDGED");
        }, waitForDeliveryMs);

        const onDelivery = (state: "WHATSAPP_ACKNOWLEDGED" | "DELIVERED" | "READ") => {
          if (state === "DELIVERED" || state === "READ") {
            clearTimeout(timer);
            this.#deliveryEmitter.off(msgId, onDelivery);
            resolveDelivery(state);
          }
        };

        this.#deliveryEmitter.once(msgId, onDelivery);
      });
    }

    const statusMessage =
      currentDeliveryState === "DELIVERED" || currentDeliveryState === "READ"
        ? "Delivered."
        : "Message accepted by WhatsApp; delivery confirmation pending.";

    return {
      success: true,
      sent: true,
      messageId: msgId,
      targetJid,
      deliveryState: currentDeliveryState,
      statusMessage,
    };
  }

  /**
   * Send a file or document with verified delivery tracking.
   */
  async sendFile(
    to: string,
    filePath: string,
    caption?: string,
    fileAs?: string,
    waitForDeliveryMs = 2500
  ): Promise<SendResult> {
    await this.connect();
    const sock = this.#client?.socket;
    if (!sock) {
      return { success: false, sent: false, deliveryState: "FAILED", error: "Socket not available" };
    }

    const absPath = resolve(filePath);
    if (!existsSync(absPath)) {
      return { success: false, sent: false, deliveryState: "FAILED", error: `File not found: ${absPath}` };
    }

    const targetJid = this.normalizeJid(to);
    const buffer = readFileSync(absPath);
    const filename = absPath.split(/[\\/]/).pop() || "file";
    const lowerExt = filename.split(".").pop()?.toLowerCase() || "";

    let content: any;
    const kind = fileAs || (
      ["png", "jpg", "jpeg", "webp"].includes(lowerExt)
        ? "image"
        : ["mp4", "mov"].includes(lowerExt)
        ? "video"
        : ["mp3", "ogg", "wav", "m4a"].includes(lowerExt)
        ? "audio"
        : "document"
    );

    if (kind === "image") {
      content = { image: buffer, caption: caption || undefined };
    } else if (kind === "video") {
      content = { video: buffer, caption: caption || undefined };
    } else if (kind === "audio") {
      content = { audio: buffer, mimetype: `audio/${lowerExt}` };
    } else {
      content = {
        document: buffer,
        fileName: filename,
        caption: caption || undefined,
        mimetype: "application/octet-stream",
      };
    }

    let sentMsg: any;
    try {
      sentMsg = await sock.sendMessage(targetJid, content);
    } catch (err: any) {
      return {
        success: false,
        sent: false,
        deliveryState: "FAILED",
        error: `WhatsApp socket rejected send: ${err?.message || String(err)}`,
      };
    }

    const msgId = sentMsg?.key?.id || `file-${Date.now()}`;

    this.store.upsertMessage({
      chat_jid: targetJid,
      msg_id: msgId,
      sender_jid: "me",
      ts: Math.floor(Date.now() / 1000),
      from_me: 1,
      text: caption || `[${kind}: ${filename}]`,
      display_text: caption || `[${kind}: ${filename}]`,
      media_type: kind,
      filename,
      local_path: absPath,
      delivery_status: "WHATSAPP_ACKNOWLEDGED",
    });

    this.store.markChatRead(targetJid);
    try {
      await sock.readMessages([{ remoteJid: targetJid, id: msgId, fromMe: false }]);
    } catch {}

    let currentDeliveryState: "WHATSAPP_ACKNOWLEDGED" | "DELIVERED" | "READ" = "WHATSAPP_ACKNOWLEDGED";

    if (waitForDeliveryMs > 0) {
      currentDeliveryState = await new Promise((resolveDelivery) => {
        const timer = setTimeout(() => {
          this.#deliveryEmitter.off(msgId, onDelivery);
          resolveDelivery("WHATSAPP_ACKNOWLEDGED");
        }, waitForDeliveryMs);

        const onDelivery = (state: "WHATSAPP_ACKNOWLEDGED" | "DELIVERED" | "READ") => {
          if (state === "DELIVERED" || state === "READ") {
            clearTimeout(timer);
            this.#deliveryEmitter.off(msgId, onDelivery);
            resolveDelivery(state);
          }
        };

        this.#deliveryEmitter.once(msgId, onDelivery);
      });
    }

    const statusMessage =
      currentDeliveryState === "DELIVERED" || currentDeliveryState === "READ"
        ? "Delivered."
        : "Message accepted by WhatsApp; delivery confirmation pending.";

    return {
      success: true,
      sent: true,
      messageId: msgId,
      targetJid,
      deliveryState: currentDeliveryState,
      statusMessage,
    };
  }

  /**
   * Send a voice note (PTT) with verified delivery tracking.
   */
  async sendVoice(
    to: string,
    filePath: string,
    waitForDeliveryMs = 2500
  ): Promise<SendResult> {
    await this.connect();
    const sock = this.#client?.socket;
    if (!sock) {
      return { success: false, sent: false, deliveryState: "FAILED", error: "Socket not available" };
    }

    const absPath = resolve(filePath);
    if (!existsSync(absPath)) {
      return { success: false, sent: false, deliveryState: "FAILED", error: `File not found: ${absPath}` };
    }

    const targetJid = this.normalizeJid(to);
    const buffer = readFileSync(absPath);
    let sentMsg: any;
    try {
      sentMsg = await sock.sendMessage(targetJid, {
        audio: buffer,
        ptt: true,
        mimetype: "audio/ogg; codecs=opus",
      });
    } catch (err: any) {
      return {
        success: false,
        sent: false,
        deliveryState: "FAILED",
        error: `WhatsApp socket rejected send: ${err?.message || String(err)}`,
      };
    }

    const msgId = sentMsg?.key?.id || `ptt-${Date.now()}`;

    this.store.upsertMessage({
      chat_jid: targetJid,
      msg_id: msgId,
      sender_jid: "me",
      ts: Math.floor(Date.now() / 1000),
      from_me: 1,
      text: "[Voice Note]",
      display_text: "[Voice Note]",
      media_type: "voice",
      local_path: absPath,
      delivery_status: "WHATSAPP_ACKNOWLEDGED",
    });

    this.store.markChatRead(targetJid);
    try {
      await sock.readMessages([{ remoteJid: targetJid, id: msgId, fromMe: false }]);
    } catch {}

    let currentDeliveryState: "WHATSAPP_ACKNOWLEDGED" | "DELIVERED" | "READ" = "WHATSAPP_ACKNOWLEDGED";

    if (waitForDeliveryMs > 0) {
      currentDeliveryState = await new Promise((resolveDelivery) => {
        const timer = setTimeout(() => {
          this.#deliveryEmitter.off(msgId, onDelivery);
          resolveDelivery("WHATSAPP_ACKNOWLEDGED");
        }, waitForDeliveryMs);

        const onDelivery = (state: "WHATSAPP_ACKNOWLEDGED" | "DELIVERED" | "READ") => {
          if (state === "DELIVERED" || state === "READ") {
            clearTimeout(timer);
            this.#deliveryEmitter.off(msgId, onDelivery);
            resolveDelivery(state);
          }
        };

        this.#deliveryEmitter.once(msgId, onDelivery);
      });
    }

    const statusMessage =
      currentDeliveryState === "DELIVERED" || currentDeliveryState === "READ"
        ? "Delivered."
        : "Message accepted by WhatsApp; delivery confirmation pending.";

    return {
      success: true,
      sent: true,
      messageId: msgId,
      targetJid,
      deliveryState: currentDeliveryState,
      statusMessage,
    };
  }

  /**
   * Send a reaction emoji to a message.
   */
  async sendReaction(to: string, msgId: string, emoji: string): Promise<SendResult> {
    await this.connect();
    const sock = this.#client?.socket;
    if (!sock) {
      return { success: false, sent: false, error: "Socket not available" };
    }

    const targetJid = this.normalizeJid(to);
    await sock.sendMessage(targetJid, {
      react: {
        text: emoji,
        key: {
          id: msgId,
          remoteJid: targetJid,
          fromMe: false,
        },
      },
    });

    return {
      success: true,
      sent: true,
      messageId: msgId,
      targetJid,
      deliveryState: "WHATSAPP_ACKNOWLEDGED",
      statusMessage: "Reaction sent.",
    };
  }

  /**
   * Check whether a phone number is registered on WhatsApp.
   */
  async checkNumber(phone: string): Promise<{ phone: string; exists: boolean; jid?: string }> {
    await this.connect();
    const sock = this.#client?.socket;
    if (!sock) {
      return { phone, exists: false };
    }

    const clean = phone.replace(/\D/g, "");
    try {
      const results = await sock.onWhatsApp(clean);
      if (Array.isArray(results) && results.length > 0) {
        return {
          phone: clean,
          exists: Boolean(results[0].exists),
          jid: results[0].jid,
        };
      }
      return { phone: clean, exists: false };
    } catch {
      return { phone: clean, exists: false };
    }
  }

  /**
   * Mark all messages in a chat as read.
   */
  async markRead(chatJid: string): Promise<{ success: boolean; chat: string }> {
    await this.connect();
    const cleanChat = this.normalizeJid(chatJid);
    this.store.markChatRead(cleanChat);
    const sock = this.#client?.socket;
    if (sock) {
      try {
        await sock.readMessages([{ remoteJid: cleanChat, id: "", fromMe: false }]);
      } catch {}
    }
    return { success: true, chat: cleanChat };
  }

  /**
   * Download media attached to a stored or received WhatsApp message.
   */
  async downloadMedia(
    chatJid: string,
    msgId: string,
    targetPath?: string
  ): Promise<{ success: boolean; localPath?: string; bytes?: number; error?: string }> {
    await this.connect();
    const cleanChat = this.normalizeJid(chatJid);
    const existingMsg = this.store.getMediaMessage(cleanChat, msgId);
    if (existingMsg?.local_path && existsSync(existingMsg.local_path)) {
      try {
        const stats = statSync(existingMsg.local_path);
        return {
          success: true,
          localPath: existingMsg.local_path,
          bytes: stats.size,
        };
      } catch {}
    }

    const cacheKey = `${cleanChat}:${msgId}`;
    const rawMsg = this.#rawMessageCache.get(cacheKey);
    const sock = this.#client?.socket;
    if (!sock) {
      return { success: false, error: "WhatsApp socket not available for media download" };
    }

    try {
      let buffer: Buffer | null = null;
      if (rawMsg) {
        buffer = await downloadMediaMessage(
          rawMsg,
          "buffer",
          {},
          {
            logger: (sock as any).logger,
            reuploadRequest: sock.updateMediaMessage,
          }
        );
      }

      if (!buffer) {
        return {
          success: false,
          error: `Media message ${msgId} could not be downloaded (not in memory or expired).`,
        };
      }

      const mimeType = existingMsg?.mime_type || "application/octet-stream";
      let ext = "bin";
      if (existingMsg?.filename && existingMsg.filename.includes(".")) {
        ext = existingMsg.filename.split(".").pop()!;
      } else if (mimeType.includes("/")) {
        ext = (mimeType.split("/")[1] || "bin").split(";")[0].trim();
      }

      const mediaDir = targetPath
        ? dirname(targetPath)
        : join(dirname(this.store.dbPath), "media", cleanChat.replace(/[^a-zA-Z0-9_-]/g, "_"));
      if (!existsSync(mediaDir)) {
        mkdirSync(mediaDir, { recursive: true });
      }

      const destPath = targetPath || join(mediaDir, `${msgId}.${ext}`);
      writeFileSync(destPath, buffer);
      this.store.updateMessageLocalPath(cleanChat, msgId, destPath);

      return {
        success: true,
        localPath: destPath,
        bytes: buffer.length,
      };
    } catch (err: any) {
      console.error(`[WhatsAppManager] Error downloading media for ${cleanChat}:${msgId}:`, err);
      return { success: false, error: err?.message || String(err) };
    }
  }

  /**
   * Initiate an outbound 1:1 voice call in realtime audio mode.
   * Reuses the exact same authenticated session.
   */
  async placeCall(phoneNumber: string, durationMs = 120_000): Promise<CallSession> {
    if (!this.#client || !this.#connected) {
      throw new Error("WhatsApp client is not connected. Call connect() first.");
    }

    const cleanNumber = phoneNumber.replace(/\D/g, "");
    if (!cleanNumber) {
      throw new Error(`Invalid phone number provided: ${phoneNumber}`);
    }

    const activeCall = await this.#client.call(cleanNumber, {
      audioSource: "realtime",
      durationMs,
    });

    return new CallSession(activeCall, cleanNumber);
  }

  /**
   * Disconnect the client and free transport resources.
   */
  disconnect(): void {
    if (this.#client) {
      try {
        this.#client.disconnect();
      } catch {}
      this.#client = null;
    }
    this.#connected = false;
    this.#connectionState = "disconnected";
  }

  /**
   * Log out from WhatsApp and purge persistent credentials.
   */
  logout(): void {
    this.disconnect();
    if (existsSync(this.#authDir)) {
      try {
        const files = readdirSync(this.#authDir);
        for (const file of files) {
          rmSync(join(this.#authDir, file), { recursive: true, force: true });
        }
      } catch {}
    }
  }
}
