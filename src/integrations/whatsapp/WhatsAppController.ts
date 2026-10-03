/**
 * Long-lived session owner and connection controller for WhatsApp Rust engine.
 *
 * Enforces a single-instance process lock, manages connection lifecycle,
 * backoff reconnection, and event dispatching.
 */

import { existsSync, mkdirSync, readFileSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import pino from "pino";
import { makeWASocket, DisconnectReason } from "@oxidezap/baileyrs";
import { WhatsAppAuth } from "./WhatsAppAuth.js";
import type { WhatsAppConfig, WhatsAppStatus } from "./types.js";

export interface ControllerOptions {
  config: WhatsAppConfig;
  auth: WhatsAppAuth;
  silent?: boolean;
}

export class WhatsAppController {
  private readonly config: WhatsAppConfig;
  private readonly auth: WhatsAppAuth;
  private readonly lockFilePath: string;
  private readonly silent: boolean;
  private socket: any = null;
  private isConnected: boolean = false;
  private connectionStartTime: number = 0;
  private lastSyncTime: number | null = null;
  private reconnectAttempts: number = 0;
  private maxReconnectAttempts: number = 8;
  private baseBackoffMs: number = 1000;
  private isShuttingDown: boolean = false;
  private userJid: string | null = null;
  private pushName: string | null = null;

  constructor(options: ControllerOptions) {
    this.config = options.config;
    this.auth = options.auth;
    this.lockFilePath = resolve(this.config.lockFilePath);
    this.silent = options.silent ?? false;
  }

  /**
   * Acquire single-instance lock file. Throws if another active process holds it.
   */
  public acquireLock(): void {
    mkdirSync(dirname(this.lockFilePath), { recursive: true });

    if (existsSync(this.lockFilePath)) {
      try {
        const raw = readFileSync(this.lockFilePath, "utf-8");
        const lockInfo = JSON.parse(raw);
        const pid = lockInfo.pid;

        if (this.isProcessRunning(pid)) {
          throw new Error(
            `Another WhatsApp controller process (PID ${pid}) holds the session lock at ${this.lockFilePath}. ` +
            `Dual-socket execution is prohibited to preserve session integrity.`
          );
        } else {
          // Lock is stale from a crashed process; remove it
          if (!this.silent) {
            console.log(`[WhatsAppController] Removing stale lock file from PID ${pid}`);
          }
          unlinkSync(this.lockFilePath);
        }
      } catch (err: any) {
        if (err.message.includes("holds the session lock")) {
          throw err;
        }
        // In case of unparseable lock file, replace it
        try {
          unlinkSync(this.lockFilePath);
        } catch {}
      }
    }

    const payload = {
      pid: process.pid,
      timestamp: Date.now(),
      hostname: process.env.COMPUTERNAME || "localhost",
    };
    writeFileSync(this.lockFilePath, JSON.stringify(payload, null, 2), "utf-8");

    // Clean up lock on termination signals
    const cleanup = () => this.releaseLock();
    process.once("exit", cleanup);
    process.once("SIGINT", cleanup);
    process.once("SIGTERM", cleanup);
  }

  /**
   * Release the single-instance lock file.
   */
  public releaseLock(): void {
    try {
      if (existsSync(this.lockFilePath)) {
        const raw = readFileSync(this.lockFilePath, "utf-8");
        const lockInfo = JSON.parse(raw);
        if (lockInfo.pid === process.pid) {
          unlinkSync(this.lockFilePath);
        }
      }
    } catch {
      // Best-effort cleanup
    }
  }

  private isProcessRunning(pid: number): boolean {
    if (!pid || pid <= 0) return false;
    try {
      // Signal 0 tests process existence without terminating it
      process.kill(pid, 0);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Initialize socket connection with upstream whatsapp-rust WASM engine.
   */
  public async connect(): Promise<any> {
    this.acquireLock();
    const { state, saveCreds } = await this.auth.loadState();

    const logger = pino({
      level: this.silent ? "silent" : (process.env.WHATSAPP_LOG_LEVEL || "warn"),
    });

    return new Promise((resolveConnect, rejectConnect) => {
      let resolved = false;

      try {
        this.socket = makeWASocket({
          auth: state,
          logger,
          printQRInTerminal: !this.silent,
        });

        // Listen for credential updates to keep auth durable
        this.socket.ev.on("creds.update", saveCreds);

        // Connection lifecycle events
        this.socket.ev.on("connection.update", async (update: any) => {
          const { connection, lastDisconnect, qr } = update;

          if (qr) {
            this.auth.handleQrCode(qr);
          }

          if (connection === "open") {
            this.isConnected = true;
            this.connectionStartTime = Date.now();
            this.lastSyncTime = Date.now();
            this.reconnectAttempts = 0;

            const user = this.socket.user;
            this.userJid = user?.id || null;
            this.pushName = user?.name || null;

            if (!this.silent) {
              console.log("[WhatsAppController] Connected to WhatsApp network successfully.");
            }

            if (!resolved) {
              resolved = true;
              resolveConnect(this.socket);
            }
          }

          if (connection === "close") {
            this.isConnected = false;
            const statusCode = lastDisconnect?.error?.output?.statusCode;
            const shouldReconnect =
              !this.isShuttingDown &&
              statusCode !== DisconnectReason.loggedOut &&
              statusCode !== DisconnectReason.connectionReplaced;

            if (!this.silent) {
              console.log(
                `[WhatsAppController] Connection closed (code: ${statusCode}, reconnect: ${shouldReconnect})`
              );
            }

            if (statusCode === DisconnectReason.loggedOut) {
              this.auth.clearSession();
              if (!resolved) {
                resolved = true;
                rejectConnect(new Error("WhatsApp session logged out by remote device."));
              }
              return;
            }

            if (shouldReconnect) {
              await this.handleReconnect();
            } else {
              if (!resolved) {
                resolved = true;
                rejectConnect(lastDisconnect?.error || new Error("Connection closed."));
              }
            }
          }
        });
      } catch (err) {
        if (!resolved) {
          resolved = true;
          rejectConnect(err);
        }
      }
    });
  }

  private async handleReconnect(): Promise<void> {
    if (this.isShuttingDown) return;

    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error(
        `[WhatsAppController] Max reconnection attempts (${this.maxReconnectAttempts}) reached. Halting.`
      );
      return;
    }

    this.reconnectAttempts++;
    // Exponential backoff with random jitter
    const backoff =
      Math.min(30000, this.baseBackoffMs * Math.pow(2, this.reconnectAttempts - 1)) +
      Math.floor(Math.random() * 500);

    if (!this.silent) {
      console.log(
        `[WhatsAppController] Reconnecting in ${backoff}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})...`
      );
    }

    await new Promise((r) => setTimeout(r, backoff));
    try {
      await this.connect();
    } catch (err) {
      console.error("[WhatsAppController] Reconnect attempt failed:", err);
    }
  }

  public getSocket(): any {
    return this.socket;
  }

  public getStatus(activeCallsCount: number = 0): WhatsAppStatus {
    const userPhone = this.userJid ? this.userJid.split("@")[0].split(":")[0] : null;
    const uptimeSec = this.isConnected && this.connectionStartTime > 0
      ? Math.floor((Date.now() - this.connectionStartTime) / 1000)
      : 0;

    return {
      connected: this.isConnected,
      authenticated: this.auth.hasPersistedCredentials(),
      userJid: this.userJid,
      userPhone,
      pushName: this.pushName,
      platform: "whatsapp-rust-wasm",
      uptimeSec,
      lastSyncTimestamp: this.lastSyncTime,
      authDir: this.auth.directory,
      activeCallsCount,
    };
  }

  public disconnect(): void {
    this.isShuttingDown = true;
    try {
      if (this.socket) {
        this.socket.end(undefined);
      }
    } catch {
      // Ignore disconnect errors
    } finally {
      this.isConnected = false;
      this.releaseLock();
    }
  }
}
