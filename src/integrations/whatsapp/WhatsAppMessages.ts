/**
 * Messaging controller for WhatsApp Rust engine.
 *
 * Supports sending text messages, media files, voice notes, reactions,
 * marking as read, and querying messages/chats.
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { basename, extname } from "node:path";
import type { WhatsAppContacts } from "./WhatsAppContacts.js";
import type { WhatsAppMessageResult } from "./types.js";

export class WhatsAppMessages {
  private readonly getSocket: () => any;
  private readonly contacts: WhatsAppContacts;

  constructor(getSocket: () => any, contacts: WhatsAppContacts) {
    this.getSocket = getSocket;
    this.contacts = contacts;
  }

  private ensureSocket(): any {
    const socket = this.getSocket();
    if (!socket) {
      throw new Error("WhatsApp socket is not connected.");
    }
    return socket;
  }

  /**
   * Send a text message to a recipient.
   */
  public async sendText(
    target: string,
    text: string,
    replyToId?: string
  ): Promise<WhatsAppMessageResult> {
    const sock = this.ensureSocket();
    const recipient = this.contacts.resolveRecipient(target);

    const messagePayload: any = { text };
    let quotingOptions: any = undefined;

    if (replyToId) {
      quotingOptions = {
        quoted: {
          key: {
            remoteJid: recipient.jid,
            id: replyToId,
          },
          message: { conversation: "" },
        },
      };
    }

    try {
      const response = await sock.sendMessage(recipient.jid, messagePayload, quotingOptions);
      return {
        success: true,
        messageId: response?.key?.id,
        chatJid: recipient.jid,
        timestamp: Date.now(),
      };
    } catch (err: any) {
      return {
        success: false,
        chatJid: recipient.jid,
        timestamp: Date.now(),
        error: err?.message || String(err),
      };
    }
  }

  /**
   * Send a file or media document.
   */
  public async sendFile(
    target: string,
    filePath: string,
    caption?: string,
    fileAs?: string
  ): Promise<WhatsAppMessageResult> {
    const sock = this.ensureSocket();
    const recipient = this.contacts.resolveRecipient(target);

    if (!existsSync(filePath)) {
      throw new Error(`File does not exist: ${filePath}`);
    }

    const buffer = readFileSync(filePath);
    const fileName = basename(filePath);
    const ext = extname(filePath).toLowerCase();

    let mimeType = "application/octet-stream";
    let isImage = false;
    let isVideo = false;
    let isAudio = false;

    if (ext === ".jpg" || ext === ".jpeg") {
      mimeType = "image/jpeg";
      isImage = true;
    } else if (ext === ".png") {
      mimeType = "image/png";
      isImage = true;
    } else if (ext === ".webp") {
      mimeType = "image/webp";
      isImage = true;
    } else if (ext === ".mp4") {
      mimeType = "video/mp4";
      isVideo = true;
    } else if (ext === ".mp3") {
      mimeType = "audio/mpeg";
      isAudio = true;
    } else if (ext === ".ogg" || ext === ".opus") {
      mimeType = "audio/ogg; codecs=opus";
      isAudio = true;
    } else if (ext === ".pdf") {
      mimeType = "application/pdf";
    }

    let messagePayload: any;

    if (fileAs === "audio" || (isAudio && fileAs !== "document")) {
      messagePayload = {
        audio: buffer,
        mimetype: mimeType,
        ptt: false,
      };
    } else if (fileAs === "voice" || fileAs === "ptt") {
      messagePayload = {
        audio: buffer,
        mimetype: "audio/ogg; codecs=opus",
        ptt: true,
      };
    } else if (fileAs === "image" || (isImage && fileAs !== "document")) {
      messagePayload = {
        image: buffer,
        caption,
        mimetype: mimeType,
      };
    } else if (fileAs === "video" || (isVideo && fileAs !== "document")) {
      messagePayload = {
        video: buffer,
        caption,
        mimetype: mimeType,
      };
    } else {
      messagePayload = {
        document: buffer,
        mimetype: mimeType,
        fileName,
        caption,
      };
    }

    try {
      const response = await sock.sendMessage(recipient.jid, messagePayload);
      return {
        success: true,
        messageId: response?.key?.id,
        chatJid: recipient.jid,
        timestamp: Date.now(),
      };
    } catch (err: any) {
      return {
        success: false,
        chatJid: recipient.jid,
        timestamp: Date.now(),
        error: err?.message || String(err),
      };
    }
  }

  /**
   * Send a voice note (PTT recording).
   */
  public async sendVoice(target: string, filePath: string): Promise<WhatsAppMessageResult> {
    return await this.sendFile(target, filePath, undefined, "ptt");
  }

  /**
   * React to an existing message with an emoji.
   */
  public async sendReaction(
    target: string,
    messageId: string,
    emoji: string
  ): Promise<WhatsAppMessageResult> {
    const sock = this.ensureSocket();
    const recipient = this.contacts.resolveRecipient(target);

    const reactionPayload = {
      react: {
        text: emoji,
        key: {
          remoteJid: recipient.jid,
          id: messageId,
        },
      },
    };

    try {
      const response = await sock.sendMessage(recipient.jid, reactionPayload);
      return {
        success: true,
        messageId: response?.key?.id,
        chatJid: recipient.jid,
        timestamp: Date.now(),
      };
    } catch (err: any) {
      return {
        success: false,
        chatJid: recipient.jid,
        timestamp: Date.now(),
        error: err?.message || String(err),
      };
    }
  }

  /**
   * Mark a chat or specific message as read.
   */
  public async markRead(chatJid: string, messageId?: string): Promise<WhatsAppMessageResult> {
    const sock = this.ensureSocket();
    const recipient = this.contacts.resolveRecipient(chatJid);

    try {
      if (typeof sock.readMessages === "function" && messageId) {
        await sock.readMessages([
          {
            remoteJid: recipient.jid,
            id: messageId,
          },
        ]);
      } else if (typeof sock.chatModify === "function") {
        await sock.chatModify({ markRead: true, lastMessages: [] }, recipient.jid);
      }
      return {
        success: true,
        chatJid: recipient.jid,
        timestamp: Date.now(),
      };
    } catch (err: any) {
      return {
        success: false,
        chatJid: recipient.jid,
        timestamp: Date.now(),
        error: err?.message || String(err),
      };
    }
  }

  /**
   * Query whether a phone number is registered on WhatsApp.
   */
  public async checkNumber(phone: string): Promise<{ phone: string; exists: boolean; jid?: string }> {
    const sock = this.ensureSocket();
    const cleanDigits = phone.replace(/\D/g, "");

    try {
      if (typeof sock.onWhatsApp === "function") {
        const results = await sock.onWhatsApp(cleanDigits);
        if (Array.isArray(results) && results.length > 0) {
          const item = results[0];
          return {
            phone: cleanDigits,
            exists: !!item?.exists,
            jid: item?.jid,
          };
        }
      }
      return {
        phone: cleanDigits,
        exists: false,
      };
    } catch (err: any) {
      return {
        phone: cleanDigits,
        exists: false,
      };
    }
  }
}
