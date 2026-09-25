/**
 * Persistent Call History & Conversational Memory Layer.
 *
 * Persists structured call records and transcripts across sessions, enabling
 * context resolution (e.g., "wapis call lagao usko", "same number pe call karo")
 * even after restarts.
 */

import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { type TranscriptEntry } from "../bridge/realtime-audio.js";

export interface CallHistoryEntry {
  callId: string;
  targetName: string;
  phoneNumber: string;
  objective: string;
  callOutcome: string;
  recipientReply: string;
  summary: string;
  transcript: TranscriptEntry[];
  durationSec: string;
  timestamp: number;
  conversationMode?: string;
  conditions?: string[];
  commitments?: string[];
  actionItems?: string[];
  metrics?: {
    durationMs: number;
    inboundFramesCount: number;
    outboundFramesPushed: number;
    interruptionDiscards: number;
    bargeInCount?: number;
    userTurnCount?: number;
  };
}

export class CallHistoryManager {
  readonly #filePath: string;
  #records: CallHistoryEntry[] = [];

  constructor(filePath = "./logs/call-history.json") {
    this.#filePath = resolve(filePath);
    this.#ensureDir();
    this.#load();
  }

  get count(): number {
    return this.#records.length;
  }

  /**
   * Save a completed call record into history and disk.
   */
  async addRecord(record: CallHistoryEntry): Promise<void> {
    this.#records.push(record);
    await this.#persist();
  }

  /**
   * Return the most recent call record, if any.
   */
  getLatest(): CallHistoryEntry | null {
    if (this.#records.length === 0) return null;
    return this.#records[this.#records.length - 1];
  }

  /**
   * Retrieve a specific call record by call ID.
   */
  getCallById(callId: string): CallHistoryEntry | null {
    if (!callId) return null;
    return this.#records.find((r) => r.callId === callId) ?? null;
  }

  /**
   * Look up the most recent call matching a name or phone number.
   */
  getLatestByTarget(target: string): CallHistoryEntry | null {
    if (!target) return null;
    const cleanTarget = target.trim().toLowerCase();
    const targetDigits = cleanTarget.replace(/\D/g, "");

    for (let i = this.#records.length - 1; i >= 0; i--) {
      const rec = this.#records[i];
      if (rec.targetName.toLowerCase().includes(cleanTarget)) {
        return rec;
      }
      if (targetDigits && rec.phoneNumber.includes(targetDigits)) {
        return rec;
      }
    }
    return null;
  }

  /**
   * Return the N most recent call records.
   */
  getRecent(limit = 10): CallHistoryEntry[] {
    return this.#records.slice(-limit);
  }

  /**
   * Return all stored call records.
   */
  getAll(): CallHistoryEntry[] {
    return [...this.#records];
  }

  /**
   * Search call history by freeform query across target name, phone, objective, or summary.
   */
  search(query: string): CallHistoryEntry[] {
    const q = query.trim().toLowerCase();
    if (!q) return [];

    return this.#records.filter((rec) => {
      return (
        rec.targetName.toLowerCase().includes(q) ||
        rec.phoneNumber.includes(q) ||
        rec.objective.toLowerCase().includes(q) ||
        rec.summary.toLowerCase().includes(q) ||
        rec.recipientReply.toLowerCase().includes(q)
      );
    });
  }

  /**
   * Clear all records (in memory and on disk).
   */
  async clear(): Promise<void> {
    this.#records = [];
    await this.#persist();
  }

  #ensureDir(): void {
    try {
      const dir = dirname(this.#filePath);
      if (!existsSync(dir)) {
        mkdirSync(dir, { recursive: true });
      }
    } catch {}
  }

  #load(): void {
    if (!existsSync(this.#filePath)) return;
    try {
      const content = readFileSync(this.#filePath, "utf8");
      const parsed = JSON.parse(content);
      if (Array.isArray(parsed)) {
        this.#records = parsed;
      }
    } catch (err) {
      // Corrupt or empty file fallback
      this.#records = [];
    }
  }

  async #persist(): Promise<void> {
    this.#ensureDir();
    try {
      const data = JSON.stringify(this.#records, null, 2);
      writeFileSync(this.#filePath, data, "utf8");
    } catch (err) {
      console.error("[CallHistoryManager] Error persisting call history:", err);
    }
  }
}
