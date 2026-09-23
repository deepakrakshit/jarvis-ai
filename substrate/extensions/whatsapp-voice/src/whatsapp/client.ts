/**
 * WhatsApp client manager.
 *
 * Coordinates initialization, QR login, session persistence, and outbound call creation.
 */

import { VoipClient } from "../voip/index.mjs";
import { CallSession } from "./call.js";
import { existsSync, readdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { installSecuritySanitizer } from "../security/sanitizer.js";

// Ensure cryptographic sanitizer is active whenever WhatsApp client is initialized
installSecuritySanitizer();

export interface WhatsAppManagerOptions {
  authDir: string;
  onQrCode?: (qr: string) => void;
  silent?: boolean;
}

export class WhatsAppManager {
  readonly #authDir: string;
  readonly #onQrCode?: (qr: string) => void;
  readonly #silent: boolean;
  #client: VoipClient | null = null;
  #connected = false;

  constructor(options: WhatsAppManagerOptions) {
    this.#authDir = options.authDir;
    this.#onQrCode = options.onQrCode;
    this.#silent = options.silent ?? false;
  }

  get isConnected(): boolean {
    return this.#connected;
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

  /**
   * Connect to WhatsApp. Displays QR code if unauthenticated.
   */
  async connect(): Promise<void> {
    if (this.#connected && this.#client) return;

    this.#client = new VoipClient({
      authDir: this.#authDir,
      onQrCode: this.#onQrCode,
      silent: this.#silent,
    });
    await this.#client.connect();
    this.#connected = true;
  }

  /**
   * Initiate an outbound 1:1 voice call in realtime audio mode.
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
