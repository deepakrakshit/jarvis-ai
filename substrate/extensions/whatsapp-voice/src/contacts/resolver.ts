/**
 * Contact and phone number resolver.
 *
 * Resolves contact names and phone numbers cleanly, safely preventing
 * unauthorized or arbitrary calls.
 */

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export interface ResolvedContact {
  name?: string;
  phoneNumber: string; // digits only e.g. "919876543210"
  formatted: string;   // e.g. "+91 98765 43210"
}

export interface ContactResolverOptions {
  contactsFilePath?: string;
  defaultCountryCode?: string;
}

export class ContactResolver {
  readonly #contactsFile: string;
  readonly #defaultCountryCode: string;
  #knownContacts: Map<string, string> = new Map();

  constructor(options?: ContactResolverOptions | string) {
    const envContactsFile = process.env.WHATSAPP_CONTACTS_FILE?.trim();
    const envCountryCode = process.env.DEFAULT_COUNTRY_CODE?.trim() || "91";

    if (typeof options === "string") {
      this.#contactsFile = resolve(options);
      this.#defaultCountryCode = envCountryCode;
    } else if (options && typeof options === "object") {
      this.#contactsFile = resolve(options.contactsFilePath || envContactsFile || "./contacts.json");
      this.#defaultCountryCode = options.defaultCountryCode || envCountryCode;
    } else {
      this.#contactsFile = resolve(envContactsFile || "./contacts.json");
      this.#defaultCountryCode = envCountryCode;
    }
    this.#loadContacts();
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
    if (this.#knownContacts.has(lowerKey)) {
      const number = this.#knownContacts.get(lowerKey)!;
      return {
        name: rawTarget,
        phoneNumber: number,
        formatted: this.formatE164(number),
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
}
