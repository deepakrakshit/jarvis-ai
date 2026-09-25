/**
 * Contact and phone number resolver.
 *
 * Resolves contact names and phone numbers cleanly, safely preventing
 * unauthorized or arbitrary calls.
 */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";

export interface ResolvedContact {
  name?: string;
  phoneNumber: string; // digits only e.g. "919876543210"
  formatted: string;   // e.g. "+91 98765 43210"
}

export interface ContactResolverOptions {
  contactsFilePath?: string;
  defaultCountryCode?: string;
  dbPath?: string;
  authDir?: string;
}

export class ContactResolver {
  readonly #contactsFile: string;
  readonly #defaultCountryCode: string;
  readonly #dbPath: string;
  readonly #authDir: string;
  #knownContacts: Map<string, string> = new Map();

  constructor(options?: ContactResolverOptions | string) {
    const envContactsFile = process.env.WHATSAPP_CONTACTS_FILE?.trim();
    const envCountryCode = process.env.DEFAULT_COUNTRY_CODE?.trim() || "91";
    const envDbPath = process.env.WHATSAPP_DB_PATH?.trim() || "./data/whatsapp/whatsapp.db";
    const envAuthDir = process.env.WHATSAPP_AUTH_DIR?.trim() || "./auth";

    if (typeof options === "string") {
      this.#contactsFile = resolve(options);
      this.#defaultCountryCode = envCountryCode;
      this.#dbPath = resolve(envDbPath);
      this.#authDir = resolve(envAuthDir);
    } else if (options && typeof options === "object") {
      this.#contactsFile = resolve(options.contactsFilePath || envContactsFile || "./contacts.json");
      this.#defaultCountryCode = options.defaultCountryCode || envCountryCode;
      this.#dbPath = resolve(options.dbPath || envDbPath);
      this.#authDir = resolve(options.authDir || envAuthDir);
    } else {
      this.#contactsFile = resolve(envContactsFile || "./contacts.json");
      this.#defaultCountryCode = envCountryCode;
      this.#dbPath = resolve(envDbPath);
      this.#authDir = resolve(envAuthDir);
    }
    this.#loadContacts();
    this.#loadDbContacts();
  }

  /**
   * Resolve a target string (e.g. "Rahul", "+91 98765 43210", "919876543210")
   * to a normalized contact descriptor.
   */
  resolveTarget(target: string): ResolvedContact {
    const rawTarget = target.trim();
    if (!rawTarget) {
      throw new Error("Target contact or phone number cannot be empty.");
    }

    // 1. Check if the target is in the local known contacts map
    const lowerKey = rawTarget.toLowerCase();
    const cleanKey = lowerKey.replace(/^~/, "").trim();

    if (this.#knownContacts.has(lowerKey)) {
      const number = this.#knownContacts.get(lowerKey)!;
      return {
        name: rawTarget,
        phoneNumber: number,
        formatted: this.formatE164(number),
      };
    }
    if (cleanKey && this.#knownContacts.has(cleanKey)) {
      const number = this.#knownContacts.get(cleanKey)!;
      return {
        name: rawTarget,
        phoneNumber: number,
        formatted: this.formatE164(number),
      };
    }

    // 1b. Check substring / prefix match if unique
    const matches: Array<{ name: string; number: string }> = [];
    for (const [name, num] of this.#knownContacts.entries()) {
      if (name === cleanKey || name.startsWith(cleanKey) || cleanKey.startsWith(name)) {
        matches.push({ name, number: num });
      }
    }
    if (matches.length === 1) {
      return {
        name: rawTarget,
        phoneNumber: matches[0].number,
        formatted: this.formatE164(matches[0].number),
      };
    }

    // 2. Check if target is a raw phone number (with or without '+', dashes, spaces)
    let digitsOnly = rawTarget.replace(/\D/g, "");
    if (digitsOnly.length === 10 && /^[6-9]/.test(digitsOnly)) {
      digitsOnly = `${this.#defaultCountryCode}${digitsOnly}`;
    }
    if (digitsOnly.length >= 7 && digitsOnly.length <= 15) {
      return {
        name: undefined,
        phoneNumber: digitsOnly,
        formatted: this.formatE164(digitsOnly),
      };
    }

    throw new Error(
      `Could not resolve contact "${target}". It is neither a recognized contact name nor a valid phone number (7-15 digits).`
    );
  }

  /**
   * Register a contact in-memory or dynamically.
   */
  registerContact(name: string, phoneNumber: string): void {
    const digitsOnly = phoneNumber.replace(/\D/g, "");
    if (digitsOnly.length < 7) {
      throw new Error(`Invalid phone number for ${name}: ${phoneNumber}`);
    }
    this.#knownContacts.set(name.toLowerCase(), digitsOnly);
  }

  /**
   * Format digits as E.164.
   */
  formatE164(digits: string): string {
    return `+${digits}`;
  }

  #loadContacts(): void {
    if (!existsSync(this.#contactsFile)) return;
    try {
      const data = JSON.parse(readFileSync(this.#contactsFile, "utf8"));
      if (typeof data === "object" && data !== null) {
        for (const [name, num] of Object.entries(data)) {
          if (typeof num === "string" || typeof num === "number") {
            const digits = String(num).replace(/\D/g, "");
            if (digits) {
              this.#knownContacts.set(name.toLowerCase(), digits);
            }
          }
        }
      }
    } catch {}
  }

  #loadDbContacts(): void {
    // 1. Scan auth directory for cached LID reverse files if available
    const lidToPn = new Map<string, string>();
    if (existsSync(this.#authDir)) {
      try {
        const revFiles = readdirSync(this.#authDir).filter(
          (f) => f.startsWith("lid-mapping-") && f.endsWith("_reverse.json")
        );
        for (const f of revFiles) {
          const rawLid = f.replace("lid-mapping-", "").replace("_reverse.json", "");
          const lidJid = `${rawLid}@lid`.toLowerCase();
          const content = readFileSync(join(this.#authDir, f), "utf8").trim();
          const pn = content.replace(/^"|"$/g, "").trim().replace(/\D/g, "");
          if (pn) {
            lidToPn.set(lidJid, pn);
          }
        }
      } catch {}
    }

    // 2. Query SQLite mirror database
    if (!existsSync(this.#dbPath)) return;
    try {
      const db = new DatabaseSync(this.#dbPath);
      // Read lid_mappings
      try {
        const rows = db.prepare("SELECT lid, pn FROM lid_mappings").all() as any[];
        for (const r of rows) {
          if (r.lid && r.pn) {
            const pnDigits = String(r.pn).replace(/\D/g, "");
            if (pnDigits) {
              lidToPn.set(String(r.lid).toLowerCase(), pnDigits);
            }
          }
        }
      } catch {}

      // Read contacts
      try {
        const contacts = db
          .prepare(
            "SELECT jid, phone, push_name, full_name, first_name, business_name, system_name FROM contacts"
          )
          .all() as any[];
        for (const c of contacts) {
          let phone = c.phone ? String(c.phone).replace(/\D/g, "") : "";
          if (!phone && c.jid && lidToPn.has(String(c.jid).toLowerCase())) {
            phone = lidToPn.get(String(c.jid).toLowerCase())!;
          }
          if (!phone && c.jid?.endsWith("@s.whatsapp.net")) {
            phone = String(c.jid).replace("@s.whatsapp.net", "").replace(/\D/g, "");
          }

          if (phone) {
            const names = [
              c.push_name,
              c.full_name,
              c.first_name,
              c.system_name,
              c.business_name,
            ];
            for (const n of names) {
              if (n) {
                const clean = String(n).replace(/^~/, "").trim().toLowerCase();
                if (clean) {
                  this.#knownContacts.set(clean, phone);
                }
              }
            }
          }
        }
      } catch {}

      // Read aliases
      try {
        const aliases = db
          .prepare(
            "SELECT ca.alias, c.phone, c.jid FROM contact_aliases ca JOIN contacts c ON ca.jid = c.jid"
          )
          .all() as any[];
        for (const a of aliases) {
          let phone = a.phone ? String(a.phone).replace(/\D/g, "") : "";
          if (!phone && a.jid && lidToPn.has(String(a.jid).toLowerCase())) {
            phone = lidToPn.get(String(a.jid).toLowerCase())!;
          }
          if (phone && a.alias) {
            this.#knownContacts.set(String(a.alias).trim().toLowerCase(), phone);
          }
        }
      } catch {}

      try {
        db.close();
      } catch {}
    } catch (e) {
      console.warn("[ContactResolver] Notice: failed loading contacts from database:", e);
    }
  }
}
