/**
 * Contact and identity resolver for the WhatsApp subsystem.
 *
 * Implements strict phone number normalization, LID preservation,
 * and contact alias resolution.
 *
 * Invariant: Never strip "@lid" to invent a phone number.
 */

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import type { WhatsAppRecipient } from "./types.js";

export interface ContactResolverConfig {
  contactsFilePath?: string;
  defaultCountryCode?: string;
  dbPath?: string;
}

export class WhatsAppContacts {
  private readonly contactsFile: string;
  private readonly defaultCountryCode: string;
  private knownContacts: Map<string, string> = new Map();
  private lidToPhoneMap: Map<string, string> = new Map();
  private phoneToLidMap: Map<string, string> = new Map();

  constructor(options?: ContactResolverConfig) {
    this.defaultCountryCode = options?.defaultCountryCode || process.env.DEFAULT_COUNTRY_CODE || "91";
    this.contactsFile = resolve(
      options?.contactsFilePath ||
      process.env.WHATSAPP_CONTACTS_FILE ||
      "./contacts.json"
    );
    this.loadContactsFromFile();
  }

  /**
   * Load alias contacts from contacts JSON file if present.
   */
  private loadContactsFromFile(): void {
    if (!existsSync(this.contactsFile)) return;
    try {
      const raw = readFileSync(this.contactsFile, "utf-8");
      const parsed = JSON.parse(raw);
      if (typeof parsed === "object" && parsed !== null) {
        for (const [key, val] of Object.entries(parsed)) {
          if (typeof val === "string" || typeof val === "number") {
            const digits = String(val).replace(/\D/g, "");
            if (digits) {
              this.knownContacts.set(key.toLowerCase().trim(), digits);
            }
          }
        }
      }
    } catch {
      // Ignore format errors
    }
  }

  /**
   * Register mapping between phone number and WhatsApp LID (Linked Identity).
   */
  public registerLidMapping(phone: string, lid: string): void {
    const cleanPhone = phone.replace(/\D/g, "");
    const cleanLid = lid.trim();
    if (cleanPhone && cleanLid) {
      this.phoneToLidMap.set(cleanPhone, cleanLid);
      this.lidToPhoneMap.set(cleanLid, cleanPhone);
    }
  }

  /**
   * Resolve a target identifier (name, phone number, JID, or LID) into a WhatsAppRecipient.
   */
  public resolveRecipient(target: string): WhatsAppRecipient {
    const raw = target.trim();
    if (!raw) {
      throw new Error("Recipient target cannot be empty.");
    }

    // 1. Group JID check
    if (raw.endsWith("@g.us")) {
      return {
        raw,
        jid: raw,
        phone: "",
        name: raw.replace("@g.us", ""),
        isGroup: true,
      };
    }

    // 2. Strict LID check: Preserve @lid without stripping
    if (raw.endsWith("@lid")) {
      const mappedPhone = this.lidToPhoneMap.get(raw) || "";
      return {
        raw,
        jid: raw,
        phone: mappedPhone,
        lid: raw,
        name: mappedPhone ? `+${mappedPhone}` : raw,
        isGroup: false,
      };
    }

    // 3. User JID check (@s.whatsapp.net)
    if (raw.endsWith("@s.whatsapp.net")) {
      const digits = raw.split("@")[0].split(":")[0].replace(/\D/g, "");
      return {
        raw,
        jid: `${digits}@s.whatsapp.net`,
        phone: digits,
        lid: this.phoneToLidMap.get(digits),
        name: `+${digits}`,
        isGroup: false,
      };
    }

    // 4. Contact name alias resolution
    const lower = raw.toLowerCase();
    if (this.knownContacts.has(lower)) {
      const phoneDigits = this.knownContacts.get(lower)!;
      return {
        raw,
        jid: `${phoneDigits}@s.whatsapp.net`,
        phone: phoneDigits,
        lid: this.phoneToLidMap.get(phoneDigits),
        name: raw,
        isGroup: false,
      };
    }

    // Substring or prefix match if unique
    const matches: Array<{ name: string; phone: string }> = [];
    for (const [name, phone] of this.knownContacts.entries()) {
      if (name === lower || name.startsWith(lower) || lower.startsWith(name)) {
        matches.push({ name, phone });
      }
    }
    if (matches.length === 1) {
      const match = matches[0];
      return {
        raw,
        jid: `${match.phone}@s.whatsapp.net`,
        phone: match.phone,
        lid: this.phoneToLidMap.get(match.phone),
        name: match.name,
        isGroup: false,
      };
    }

    // 5. Raw phone number digits normalization
    let digits = raw.replace(/\D/g, "");
    if (digits.length === 10 && /^[6-9]/.test(digits)) {
      digits = `${this.defaultCountryCode}${digits}`;
    }

    if (digits.length >= 7 && digits.length <= 15) {
      return {
        raw,
        jid: `${digits}@s.whatsapp.net`,
        phone: digits,
        lid: this.phoneToLidMap.get(digits),
        name: `+${digits}`,
        isGroup: false,
      };
    }

    throw new Error(
      `Could not resolve recipient "${target}". It is neither a recognized contact, group, valid phone number (7-15 digits), nor a valid LID.`
    );
  }

  /**
   * Save a newly discovered contact to disk.
   */
  public addContact(name: string, phone: string): void {
    const digits = phone.replace(/\D/g, "");
    if (digits.length < 7) {
      throw new Error(`Invalid phone number for contact ${name}: ${phone}`);
    }
    this.knownContacts.set(name.toLowerCase().trim(), digits);

    try {
      const current: Record<string, string> = {};
      for (const [k, v] of this.knownContacts.entries()) {
        current[k] = v;
      }
      writeFileSync(this.contactsFile, JSON.stringify(current, null, 2), "utf-8");
    } catch {
      // Best-effort file sync
    }
  }
}
