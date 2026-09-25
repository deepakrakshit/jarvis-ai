/**
 * WhatsApp SQLite Mirror Store.
 *
 * Implements the local database mirror schema with hardened concurrency:
 * - contacts (phone, push_name, full_name, first_name, business_name, system_name)
 * - contact_aliases (local operator aliases)
 * - contact_tags (grouping tags)
 * - lid_mappings (verified LID <-> PN mappings)
 * - chats (kind, unread, unread_count, last_message_ts)
 * - messages (sender, text, display_text, media, delivery_status, delivered_at, read_at)
 * - call_events (VoIP and WhatsApp call signaling history)
 */

import { DatabaseSync } from "node:sqlite";
import { existsSync, mkdirSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

export interface StatusMessageRecord {
  msg_id: string;
  ts: number;
  from_me: number;
  sender_jid?: string;
  sender_name?: string;
  text?: string;
  media_type?: string;
  media_caption?: string;
  filename?: string;
  mime_type?: string;
  local_path?: string;
}

export interface ContactRecord {
  jid: string;
  phone?: string;
  push_name?: string;
  full_name?: string;
  first_name?: string;
  business_name?: string;
  system_name?: string;
  updated_at?: number;
}

export interface ChatRecord {
  jid: string;
  kind: string; // "dm" | "group" | "broadcast" | "newsletter" | "unknown"
  name?: string;
  last_message_ts?: number;
  archived?: number;
  pinned?: number;
  muted_until?: number;
  unread?: number;
  unread_count?: number;
}

export interface MessageRecord {
  chat_jid: string;
  chat_name?: string;
  msg_id: string;
  sender_jid?: string;
  sender_name?: string;
  ts: number;
  from_me: number;
  text?: string;
  display_text?: string;
  quoted_msg_id?: string;
  quoted_sender_jid?: string;
  is_forwarded?: number;
  forwarding_score?: number;
  reaction_to_id?: string;
  reaction_emoji?: string;
  media_type?: string;
  media_caption?: string;
  filename?: string;
  mime_type?: string;
  local_path?: string;
  revoked?: number;
  deleted_for_me?: number;
  deleted_at?: number;
  deletion_reason?: string;
  edited?: number;
  edited_ts?: number;
  delivery_status?: string;
  delivered_at?: number;
  read_at?: number;
}

export interface CallEventRecord {
  chat_jid: string;
  chat_name?: string;
  sender_jid?: string;
  sender_name?: string;
  call_id: string;
  msg_id?: string;
  event_type: string;
  direction?: string;
  media?: string;
  outcome?: string;
  reason?: string;
  call_type?: string;
  duration_secs?: number;
  ts: number;
  participants?: string;
}

export interface StoreStats {
  contacts: number;
  chats: number;
  messages: number;
  unread_chats: number;
  unread_messages: number;
}

export class WhatsAppStore {
  readonly #db: DatabaseSync;
  readonly #dbPath: string;

  constructor(dbPath: string) {
    this.#dbPath = resolve(dbPath);
    const dir = dirname(this.#dbPath);
    if (!existsSync(dir)) {
      mkdirSync(dir, { recursive: true });
    }
    this.#db = new DatabaseSync(this.#dbPath);
    this.#initSchema();
  }

  get dbPath(): string {
    return this.#dbPath;
  }

  #withRetry<T>(fn: () => T, maxRetries = 6): T {
    let lastError: any;
    for (let attempt = 0; attempt < maxRetries; attempt++) {
      try {
        return fn();
      } catch (err: any) {
        lastError = err;
        const msg = String(err?.message || "").toLowerCase();
        if (msg.includes("locked") || msg.includes("busy") || err?.code === "SQLITE_BUSY") {
          const delayMs = (attempt + 1) * 60 + Math.floor(Math.random() * 30);
          const start = Date.now();
          while (Date.now() - start < delayMs) {
            // Synchronous backoff
          }
          continue;
        }
        throw err;
      }
    }
    throw lastError;
  }

  #initSchema(): void {
    this.#withRetry(() => {
      this.#db.exec(`
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA busy_timeout = 15000;

        CREATE TABLE IF NOT EXISTS chats (
            jid TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            name TEXT,
            last_message_ts INTEGER,
            archived INTEGER NOT NULL DEFAULT 0,
            pinned INTEGER NOT NULL DEFAULT 0,
            muted_until INTEGER NOT NULL DEFAULT 0,
            unread INTEGER NOT NULL DEFAULT 0,
            unread_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS contacts (
            jid TEXT PRIMARY KEY,
            phone TEXT,
            push_name TEXT,
            full_name TEXT,
            first_name TEXT,
            business_name TEXT,
            system_name TEXT,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS contact_aliases (
            jid TEXT PRIMARY KEY,
            alias TEXT NOT NULL,
            notes TEXT,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS contact_tags (
            jid TEXT NOT NULL,
            tag TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (jid, tag)
        );

        CREATE TABLE IF NOT EXISTS lid_mappings (
            lid TEXT PRIMARY KEY,
            pn TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_jid TEXT NOT NULL,
            chat_name TEXT,
            msg_id TEXT NOT NULL,
            sender_jid TEXT,
            sender_name TEXT,
            ts INTEGER NOT NULL,
            from_me INTEGER NOT NULL,
            text TEXT,
            display_text TEXT,
            quoted_msg_id TEXT,
            quoted_sender_jid TEXT,
            is_forwarded INTEGER NOT NULL DEFAULT 0,
            forwarding_score INTEGER NOT NULL DEFAULT 0,
            reaction_to_id TEXT,
            reaction_emoji TEXT,
            media_type TEXT,
            media_caption TEXT,
            filename TEXT,
            mime_type TEXT,
            local_path TEXT,
            revoked INTEGER NOT NULL DEFAULT 0,
            deleted_for_me INTEGER NOT NULL DEFAULT 0,
            deleted_at INTEGER,
            deletion_reason TEXT,
            edited INTEGER NOT NULL DEFAULT 0,
            edited_ts INTEGER NOT NULL DEFAULT 0,
            delivery_status TEXT DEFAULT 'ACKNOWLEDGED',
            delivered_at INTEGER,
            read_at INTEGER,
            UNIQUE(chat_jid, msg_id)
        );
      `);

      // Ensure migrated columns exist on pre-existing messages table
      try {
        this.#db.exec("ALTER TABLE messages ADD COLUMN delivery_status TEXT DEFAULT 'ACKNOWLEDGED';");
      } catch {}
      try {
        this.#db.exec("ALTER TABLE messages ADD COLUMN delivered_at INTEGER;");
      } catch {}
      try {
        this.#db.exec("ALTER TABLE messages ADD COLUMN read_at INTEGER;");
      } catch {}

      this.#db.exec(`
        CREATE INDEX IF NOT EXISTS idx_chats_unread ON chats(unread, unread_count);
        CREATE INDEX IF NOT EXISTS idx_chats_ts ON chats(last_message_ts);
        CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_jid, ts);
        CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
        CREATE INDEX IF NOT EXISTS idx_messages_delivery ON messages(delivery_status);

        CREATE TABLE IF NOT EXISTS call_events (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_jid TEXT NOT NULL,
            chat_name TEXT,
            sender_jid TEXT,
            sender_name TEXT,
            call_id TEXT NOT NULL,
            msg_id TEXT,
            event_type TEXT NOT NULL,
            direction TEXT,
            media TEXT,
            outcome TEXT,
            reason TEXT,
            call_type TEXT,
            duration_secs INTEGER NOT NULL DEFAULT 0,
            ts INTEGER NOT NULL,
            participants TEXT,
            UNIQUE(chat_jid, call_id, event_type, ts)
        );

        CREATE INDEX IF NOT EXISTS idx_call_events_chat_ts ON call_events(chat_jid, ts);
        CREATE INDEX IF NOT EXISTS idx_call_events_ts ON call_events(ts);

        CREATE TABLE IF NOT EXISTS status_messages (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id TEXT NOT NULL UNIQUE,
            ts INTEGER NOT NULL,
            from_me INTEGER NOT NULL,
            sender_jid TEXT,
            sender_name TEXT,
            text TEXT,
            media_type TEXT,
            media_caption TEXT,
            filename TEXT,
            mime_type TEXT,
            local_path TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_status_messages_ts ON status_messages(ts);
      `);

      // Migrations for existing databases
      try {
        this.#db.exec(`ALTER TABLE messages ADD COLUMN delivery_status TEXT DEFAULT 'ACKNOWLEDGED'`);
      } catch {}
      try {
        this.#db.exec(`ALTER TABLE messages ADD COLUMN delivered_at INTEGER`);
      } catch {}
      try {
        this.#db.exec(`ALTER TABLE messages ADD COLUMN read_at INTEGER`);
      } catch {}

      // Clean up broadcast/status stories from personal chats and categorize newsletters properly
      try {
        this.#db.exec("DELETE FROM chats WHERE jid = 'status@broadcast' OR jid LIKE '%@broadcast';");
        this.#db.exec("DELETE FROM messages WHERE chat_jid = 'status@broadcast' OR chat_jid LIKE '%@broadcast';");
        this.#db.exec("UPDATE chats SET kind = 'newsletter' WHERE jid LIKE '%@newsletter';");
      } catch {}
    });
  }

  upsertBatch(action: (store: WhatsAppStore) => void): void {
    this.#withRetry(() => {
      this.#db.exec("BEGIN IMMEDIATE");
      try {
        action(this);
        this.#db.exec("COMMIT");
      } catch (err) {
        try {
          this.#db.exec("ROLLBACK");
        } catch {}
        throw err;
      }
    });
  }

  upsertContact(contact: ContactRecord): void {
    this.#withRetry(() => {
      const now = contact.updated_at || Date.now();
      const stmt = this.#db.prepare(`
        INSERT INTO contacts (jid, phone, push_name, full_name, first_name, business_name, system_name, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(jid) DO UPDATE SET
          phone = COALESCE(NULLIF(excluded.phone, ''), contacts.phone),
          push_name = COALESCE(NULLIF(excluded.push_name, ''), contacts.push_name),
          full_name = COALESCE(NULLIF(excluded.full_name, ''), contacts.full_name),
          first_name = COALESCE(NULLIF(excluded.first_name, ''), contacts.first_name),
          business_name = COALESCE(NULLIF(excluded.business_name, ''), contacts.business_name),
          system_name = COALESCE(NULLIF(excluded.system_name, ''), contacts.system_name),
          updated_at = excluded.updated_at
      `);
      stmt.run(
        contact.jid,
        contact.phone ?? null,
        contact.push_name ?? null,
        contact.full_name ?? null,
        contact.first_name ?? null,
        contact.business_name ?? null,
        contact.system_name ?? null,
        now
      );
    });
  }

  upsertLidMapping(lid: string, pn: string): void {
    this.#withRetry(() => {
      const now = Date.now();
      const stmt = this.#db.prepare(`
        INSERT INTO lid_mappings (lid, pn, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(lid) DO UPDATE SET
          pn = excluded.pn,
          updated_at = excluded.updated_at
      `);
      stmt.run(lid, pn, now);
    });
  }

  resolveLidToPn(lid: string): string | undefined {
    return this.#withRetry(() => {
      const clean = lid.trim();
      const rawLid = clean.includes("@") ? clean : `${clean}@lid`;
      const bareLid = clean.split("@")[0];
      const stmt = this.#db.prepare(`
        SELECT pn FROM lid_mappings WHERE lid = ? OR lid = ? LIMIT 1
      `);
      const row = stmt.get(rawLid, bareLid) as { pn?: string } | undefined;
      return row?.pn || undefined;
    });
  }

  canonicalJid(jid: string): string {
    const clean = jid.trim();
    if (clean.endsWith("@lid")) {
      const pn = this.resolveLidToPn(clean);
      if (pn) {
        const digits = pn.replace(/\D/g, "");
        return `${digits}@s.whatsapp.net`;
      }
    }
    return clean;
  }

  migrateAllLids(): number {
    return this.#withRetry(() => {
      const rows = this.#db.prepare(`
        SELECT lid, pn FROM lid_mappings WHERE pn IS NOT NULL AND pn != ''
      `).all() as Array<{ lid: string; pn: string }>;

      let count = 0;
      this.#db.exec("BEGIN IMMEDIATE");
      try {
        const updateMsgChat = this.#db.prepare(`UPDATE messages SET chat_jid = ? WHERE chat_jid = ?`);
        const updateMsgSender = this.#db.prepare(`UPDATE messages SET sender_jid = ? WHERE sender_jid = ?`);
        const updateMsgQuoted = this.#db.prepare(`UPDATE messages SET quoted_sender_jid = ? WHERE quoted_sender_jid = ?`);
        const getLidChat = this.#db.prepare(`SELECT * FROM chats WHERE jid = ?`);
        const getPnChat = this.#db.prepare(`SELECT * FROM chats WHERE jid = ?`);
        const updatePnChat = this.#db.prepare(`
          UPDATE chats SET
            name = COALESCE(?, name),
            last_message_ts = MAX(COALESCE(last_message_ts, 0), ?)
          WHERE jid = ?
        `);
        const insertPnChat = this.#db.prepare(`
          INSERT INTO chats (jid, kind, name, last_message_ts, unread, unread_count, archived, pinned)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        `);
        const deleteLidChat = this.#db.prepare(`DELETE FROM chats WHERE jid = ?`);

        for (const r of rows) {
          const rawLid = r.lid.trim();
          const lidJid = rawLid.includes("@") ? rawLid : `${rawLid}@lid`;
          const pn = r.pn.trim().replace(/\D/g, "");
          const pnJid = `${pn}@s.whatsapp.net`;

          updateMsgChat.run(pnJid, lidJid);
          updateMsgSender.run(pnJid, lidJid);
          updateMsgQuoted.run(pnJid, lidJid);

          const lidChat = getLidChat.get(lidJid) as any;
          if (lidChat) {
            const pnChat = getPnChat.get(pnJid) as any;
            if (pnChat) {
              const maxTs = Math.max(lidChat.last_message_ts || 0, pnChat.last_message_ts || 0);
              const name = pnChat.name || lidChat.name;
              updatePnChat.run(name, maxTs, pnJid);
            } else {
              insertPnChat.run(
                pnJid,
                lidChat.kind || "dm",
                lidChat.name,
                lidChat.last_message_ts,
                lidChat.unread,
                lidChat.unread_count,
                lidChat.archived || 0,
                lidChat.pinned || 0
              );
            }
            deleteLidChat.run(lidJid);
            count++;
          }
        }
        this.#db.exec("COMMIT");
      } catch (err) {
        try {
          this.#db.exec("ROLLBACK");
        } catch {}
        throw err;
      }
      return count;
    });
  }

  clearChatUnreadThrough(chatJid: string, throughTs: number): void {
    this.#withRetry(() => {
      const clean = this.canonicalJid(chatJid);
      const allJids = new Set<string>([clean]);
      if (clean.endsWith("@s.whatsapp.net")) {
        const phone = clean.split("@")[0];
        const rows = this.#db.prepare(`SELECT lid FROM lid_mappings WHERE pn = ? OR pn = ?`).all(phone, `+${phone}`) as Array<{ lid: string }>;
        for (const r of rows) allJids.add(r.lid);
      } else if (clean.endsWith("@lid")) {
        const pn = this.resolveLidToPn(clean);
        if (pn) allJids.add(`${pn.replace(/\D/g, "")}@s.whatsapp.net`);
      }

      const now = Math.floor(Date.now() / 1000);
      for (const j of allJids) {
        this.#db.prepare(`
          UPDATE messages SET read_at = COALESCE(read_at, ?)
          WHERE chat_jid = ? AND from_me = 0 AND ts <= ?
        `).run(now, j, throughTs);

        const rem = this.#db.prepare(`
          SELECT COUNT(*) as count FROM messages
          WHERE chat_jid = ? AND from_me = 0 AND ts > ?
            AND revoked = 0 AND deleted_for_me = 0 AND deleted_at IS NULL
            AND COALESCE(reaction_to_id, '') = '' AND COALESCE(reaction_emoji, '') = ''
            AND (COALESCE(display_text, '') != '(message)' OR TRIM(COALESCE(text, '')) != '')
        `).get(j, throughTs) as { count: number };

        const unreadCount = rem ? rem.count : 0;
        this.#db.prepare(`
          UPDATE chats SET unread = ?, unread_count = ? WHERE jid = ?
        `).run(unreadCount > 0 ? 1 : 0, unreadCount, j);
      }
    });
  }

  reconcileUnreadChats(): void {
    this.#withRetry(() => {
      this.migrateAllLids();

      const chats = this.#db.prepare(`
        SELECT jid FROM chats WHERE (unread > 0 OR unread_count > 0)
          AND jid != 'status@broadcast' AND kind NOT IN ('broadcast', 'newsletter')
      `).all() as Array<{ jid: string }>;

      for (const c of chats) {
        const row = this.#db.prepare(`
          SELECT MAX(ts) as max_ts FROM messages WHERE chat_jid = ? AND from_me = 1
        `).get(c.jid) as { max_ts?: number };

        if (row?.max_ts) {
          this.clearChatUnreadThrough(c.jid, row.max_ts);
        }
      }
    });
  }

  upsertChat(chat: ChatRecord): void {
    const canonical = this.canonicalJid(chat.jid);
    if (canonical === "status@broadcast") return;

    let effectiveKind = chat.kind;
    if (canonical.endsWith("@newsletter")) effectiveKind = "newsletter";
    else if (canonical.endsWith("@g.us")) effectiveKind = "group";

    this.#withRetry(() => {
      const stmt = this.#db.prepare(`
        INSERT INTO chats (jid, kind, name, last_message_ts, archived, pinned, muted_until, unread, unread_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(jid) DO UPDATE SET
          kind = excluded.kind,
          name = COALESCE(NULLIF(excluded.name, ''), chats.name),
          last_message_ts = CASE WHEN excluded.last_message_ts IS NOT NULL AND excluded.last_message_ts > COALESCE(chats.last_message_ts, 0)
                                 THEN excluded.last_message_ts ELSE chats.last_message_ts END,
          archived = COALESCE(excluded.archived, chats.archived),
          pinned = COALESCE(excluded.pinned, chats.pinned),
          muted_until = COALESCE(excluded.muted_until, chats.muted_until),
          unread = CASE WHEN excluded.unread IS NOT NULL THEN excluded.unread ELSE chats.unread END,
          unread_count = CASE WHEN excluded.unread_count IS NOT NULL THEN excluded.unread_count ELSE chats.unread_count END
      `);
      stmt.run(
        canonical,
        effectiveKind,
        chat.name ?? null,
        chat.last_message_ts ?? null,
        chat.archived ?? 0,
        chat.pinned ?? 0,
        chat.muted_until ?? 0,
        chat.unread !== undefined ? chat.unread : null,
        chat.unread_count !== undefined ? chat.unread_count : null
      );
    });
  }

  markChatRead(chatJid: string): void {
    this.#withRetry(() => {
      const clean = this.canonicalJid(chatJid);
      const allJids = new Set<string>([clean]);
      if (clean.endsWith("@s.whatsapp.net")) {
        const phone = clean.split("@")[0];
        const rows = this.#db.prepare(`SELECT lid FROM lid_mappings WHERE pn = ? OR pn = ?`).all(phone, `+${phone}`) as Array<{ lid: string }>;
        for (const r of rows) allJids.add(r.lid);
      } else if (clean.endsWith("@lid")) {
        const pn = this.resolveLidToPn(clean);
        if (pn) allJids.add(`${pn.replace(/\D/g, "")}@s.whatsapp.net`);
      }

      const stmtChat = this.#db.prepare(`UPDATE chats SET unread = 0, unread_count = 0 WHERE jid = ?`);
      const stmtMsg = this.#db.prepare(`UPDATE messages SET read_at = COALESCE(read_at, ?) WHERE chat_jid = ? AND from_me = 0 AND (read_at IS NULL OR read_at = 0)`);
      const now = Math.floor(Date.now() / 1000);

      for (const j of allJids) {
        stmtChat.run(j);
        stmtMsg.run(now, j);
      }
    });
  }

  upsertStatusMessage(status: StatusMessageRecord): void {
    this.#withRetry(() => {
      const stmt = this.#db.prepare(`
        INSERT INTO status_messages (
          msg_id, ts, from_me, sender_jid, sender_name, text,
          media_type, media_caption, filename, mime_type, local_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(msg_id) DO UPDATE SET
          ts = excluded.ts,
          sender_jid = COALESCE(excluded.sender_jid, status_messages.sender_jid),
          sender_name = COALESCE(excluded.sender_name, status_messages.sender_name),
          text = COALESCE(excluded.text, status_messages.text),
          media_type = COALESCE(excluded.media_type, status_messages.media_type),
          media_caption = COALESCE(excluded.media_caption, status_messages.media_caption),
          filename = COALESCE(excluded.filename, status_messages.filename),
          mime_type = COALESCE(excluded.mime_type, status_messages.mime_type),
          local_path = COALESCE(excluded.local_path, status_messages.local_path)
      `);
      stmt.run(
        status.msg_id,
        status.ts,
        status.from_me,
        status.sender_jid ?? null,
        status.sender_name ?? null,
        status.text ?? null,
        status.media_type ?? null,
        status.media_caption ?? null,
        status.filename ?? null,
        status.mime_type ?? null,
        status.local_path ?? null
      );
    });
  }

  syncAuthLidMappings(authDir: string): number {
    const dir = resolve(authDir);
    if (!existsSync(dir)) return 0;
    try {
      const files = readdirSync(dir).filter(
        (f) => f.startsWith("lid-mapping-") && f.endsWith("_reverse.json")
      );
      if (files.length === 0) return 0;

      let imported = 0;
      this.#withRetry(() => {
        const stmtLid = this.#db.prepare(`
          INSERT INTO lid_mappings (lid, pn, updated_at)
          VALUES (?, ?, ?)
          ON CONFLICT(lid) DO UPDATE SET pn = excluded.pn, updated_at = excluded.updated_at
        `);
        const stmtContact = this.#db.prepare(`
          UPDATE contacts SET phone = ? WHERE jid = ? AND (phone IS NULL OR phone = '')
        `);

        const now = Date.now();
        this.#db.exec("BEGIN IMMEDIATE");
        try {
          for (const f of files) {
            const rawLid = f.replace("lid-mapping-", "").replace("_reverse.json", "");
            const lidJid = `${rawLid}@lid`;
            const content = readFileSync(join(dir, f), "utf8").trim();
            const pn = content.replace(/^"|"$/g, "").trim();
            if (pn) {
              stmtLid.run(lidJid, pn, now);
              stmtContact.run(pn, lidJid);
              imported++;
            }
          }
          this.#db.exec("COMMIT");
        } catch (e) {
          try {
            this.#db.exec("ROLLBACK");
          } catch {}
          throw e;
        }
      });
      return imported;
    } catch (err) {
      console.warn("[WhatsAppStore] Notice: failed syncing auth LID mappings:", err);
      return 0;
    }
  }

  upsertMessage(msg: MessageRecord): void {
    const canonicalChat = this.canonicalJid(msg.chat_jid);
    if (canonicalChat === "status@broadcast" || canonicalChat.endsWith("@broadcast")) {
      this.upsertStatusMessage({
        msg_id: msg.msg_id,
        ts: msg.ts,
        from_me: msg.from_me,
        sender_jid: msg.sender_jid ? this.canonicalJid(msg.sender_jid) : undefined,
        sender_name: msg.sender_name,
        text: msg.text,
        media_type: msg.media_type,
        media_caption: msg.media_caption,
        filename: msg.filename,
        mime_type: msg.mime_type,
        local_path: msg.local_path,
      });
      return;
    }

    const canonicalSender = msg.sender_jid ? this.canonicalJid(msg.sender_jid) : null;
    const canonicalQuotedSender = msg.quoted_sender_jid ? this.canonicalJid(msg.quoted_sender_jid) : null;

    this.#withRetry(() => {
      // Clear chat unread state immediately through msg.ts if this is an outgoing reply from user
      if (msg.from_me) {
        this.clearChatUnreadThrough(canonicalChat, msg.ts);
      }

      const stmt = this.#db.prepare(`
        INSERT INTO messages (
          chat_jid, chat_name, msg_id, sender_jid, sender_name, ts, from_me,
          text, display_text, quoted_msg_id, quoted_sender_jid, is_forwarded,
          forwarding_score, reaction_to_id, reaction_emoji, media_type,
          media_caption, filename, mime_type, local_path, revoked,
          deleted_for_me, deleted_at, deletion_reason, edited, edited_ts,
          delivery_status, delivered_at, read_at
        ) VALUES (
          ?, ?, ?, ?, ?, ?, ?,
          ?, ?, ?, ?, ?,
          ?, ?, ?, ?,
          ?, ?, ?, ?, ?,
          ?, ?, ?, ?, ?,
          ?, ?, ?
        )
        ON CONFLICT(chat_jid, msg_id) DO UPDATE SET
          text = COALESCE(excluded.text, messages.text),
          display_text = COALESCE(excluded.display_text, messages.display_text),
          sender_name = COALESCE(excluded.sender_name, messages.sender_name),
          reaction_to_id = COALESCE(excluded.reaction_to_id, messages.reaction_to_id),
          reaction_emoji = COALESCE(excluded.reaction_emoji, messages.reaction_emoji),
          media_type = COALESCE(excluded.media_type, messages.media_type),
          media_caption = COALESCE(excluded.media_caption, messages.media_caption),
          filename = COALESCE(excluded.filename, messages.filename),
          mime_type = COALESCE(excluded.mime_type, messages.mime_type),
          local_path = COALESCE(excluded.local_path, messages.local_path),
          revoked = COALESCE(excluded.revoked, messages.revoked),
          deleted_for_me = COALESCE(excluded.deleted_for_me, messages.deleted_for_me),
          edited = COALESCE(excluded.edited, messages.edited),
          edited_ts = COALESCE(excluded.edited_ts, messages.edited_ts),
          delivery_status = COALESCE(excluded.delivery_status, messages.delivery_status),
          delivered_at = COALESCE(excluded.delivered_at, messages.delivered_at),
          read_at = COALESCE(excluded.read_at, messages.read_at)
      `);

      stmt.run(
        canonicalChat,
        msg.chat_name ?? null,
        msg.msg_id,
        canonicalSender,
        msg.sender_name ?? null,
        msg.ts,
        msg.from_me,
        msg.text ?? null,
        msg.display_text ?? null,
        msg.quoted_msg_id ?? null,
        canonicalQuotedSender,
        msg.is_forwarded ?? 0,
        msg.forwarding_score ?? 0,
        msg.reaction_to_id ?? null,
        msg.reaction_emoji ?? null,
        msg.media_type ?? null,
        msg.media_caption ?? null,
        msg.filename ?? null,
        msg.mime_type ?? null,
        msg.local_path ?? null,
        msg.revoked ?? 0,
        msg.deleted_for_me ?? 0,
        msg.deleted_at ?? null,
        msg.deletion_reason ?? null,
        msg.edited ?? 0,
        msg.edited_ts ?? 0,
        msg.delivery_status ?? (msg.from_me ? "WHATSAPP_ACKNOWLEDGED" : "DELIVERED"),
        msg.delivered_at ?? null,
        msg.read_at ?? null
      );

      // Determine chat kind correctly
      let chatKind: "dm" | "group" | "broadcast" | "newsletter" | "unknown" = "dm";
      if (canonicalChat.endsWith("@g.us")) chatKind = "group";
      else if (canonicalChat.endsWith("@newsletter")) chatKind = "newsletter";
      else if (canonicalChat.endsWith("@broadcast")) chatKind = "broadcast";

      const ensureChatStmt = this.#db.prepare(`
        INSERT INTO chats (jid, kind, name, last_message_ts)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(jid) DO UPDATE SET
          last_message_ts = MAX(COALESCE(chats.last_message_ts, 0), excluded.last_message_ts),
          name = COALESCE(NULLIF(excluded.name, ''), chats.name),
          kind = CASE WHEN chats.kind = 'unknown' OR chats.kind = 'dm' THEN excluded.kind ELSE chats.kind END
      `);
      ensureChatStmt.run(canonicalChat, chatKind, msg.sender_name ?? null, msg.ts);
    });
  }

  hasMessage(chatJid: string, msgId: string): boolean {
    const canonical = this.canonicalJid(chatJid);
    return this.#withRetry(() => {
      const row = this.#db
        .prepare("SELECT 1 FROM messages WHERE (chat_jid = ? OR chat_jid = ?) AND msg_id = ?")
        .get(canonical, chatJid, msgId);
      return Boolean(row);
    });
  }

  incrementChatUnread(chatJid: string, ts: number, senderName?: string): void {
    const canonical = this.canonicalJid(chatJid);
    if (canonical === "status@broadcast" || canonical.endsWith("@broadcast") || canonical.endsWith("@newsletter")) {
      return;
    }
    this.#withRetry(() => {
      const chatKind = canonical.endsWith("@g.us") ? "group" : "dm";
      const stmt = this.#db.prepare(`
        INSERT INTO chats (jid, kind, name, last_message_ts, unread, unread_count)
        VALUES (?, ?, ?, ?, 1, 1)
        ON CONFLICT(jid) DO UPDATE SET
          last_message_ts = MAX(COALESCE(chats.last_message_ts, 0), excluded.last_message_ts),
          unread = 1,
          unread_count = COALESCE(chats.unread_count, 0) + 1
      `);
      stmt.run(canonical, chatKind, senderName ?? null, ts);
    });
  }

  updateMessageLocalPath(chatJid: string, msgId: string, localPath: string): void {
    const canonical = this.canonicalJid(chatJid);
    this.#withRetry(() => {
      const stmt = this.#db.prepare(`
        UPDATE messages SET local_path = ? WHERE (chat_jid = ? OR chat_jid = ?) AND msg_id = ?
      `);
      stmt.run(localPath, canonical, chatJid, msgId);
    });
  }

  getMediaMessage(chatJid: string, msgId: string): MessageRecord | null {
    const canonical = this.canonicalJid(chatJid);
    return this.#withRetry(() => {
      const row = this.#db
        .prepare(`SELECT * FROM messages WHERE (chat_jid = ? OR chat_jid = ?) AND msg_id = ?`)
        .get(canonical, chatJid, msgId) as any;
      return row ? (row as MessageRecord) : null;
    });
  }

  updateMessageDeliveryStatus(
    chatJid: string,
    msgId: string,
    status: "WHATSAPP_ACKNOWLEDGED" | "DELIVERED" | "READ",
    ts?: number
  ): boolean {
    const canonical = this.canonicalJid(chatJid);
    return this.#withRetry(() => {
      const effectiveTs = ts || Math.floor(Date.now() / 1000);
      let query = "UPDATE messages SET delivery_status = ?";
      const params: any[] = [status];
      if (status === "DELIVERED") {
        query += ", delivered_at = COALESCE(delivered_at, ?)";
        params.push(effectiveTs);
      } else if (status === "READ") {
        query += ", read_at = COALESCE(read_at, ?), delivered_at = COALESCE(delivered_at, ?)";
        params.push(effectiveTs, effectiveTs);
      }
      query += " WHERE (chat_jid = ? OR chat_jid = ?) AND msg_id = ?";
      params.push(canonical, chatJid, msgId);
      const stmt = this.#db.prepare(query);
      const res = stmt.run(...params);
      return (res as any).changes > 0;
    });
  }

  getMessageDeliveryStatus(
    chatJid: string,
    msgId: string
  ): { status: string; delivered_at?: number; read_at?: number } | null {
    const canonical = this.canonicalJid(chatJid);
    return this.#withRetry(() => {
      const stmt = this.#db.prepare(
        "SELECT delivery_status, delivered_at, read_at FROM messages WHERE (chat_jid = ? OR chat_jid = ?) AND msg_id = ?"
      );
      const row = stmt.get(canonical, chatJid, msgId) as any;
      if (!row) return null;
      return {
        status: String(row.delivery_status || "UNKNOWN"),
        delivered_at: row.delivered_at ? Number(row.delivered_at) : undefined,
        read_at: row.read_at ? Number(row.read_at) : undefined,
      };
    });
  }

  insertCallEvent(event: CallEventRecord): void {
    this.#withRetry(() => {
      const stmt = this.#db.prepare(`
        INSERT OR IGNORE INTO call_events (
          chat_jid, chat_name, sender_jid, sender_name, call_id,
          msg_id, event_type, direction, media, outcome, reason,
          call_type, duration_secs, ts, participants
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `);
      stmt.run(
        event.chat_jid,
        event.chat_name ?? null,
        event.sender_jid ?? null,
        event.sender_name ?? null,
        event.call_id,
        event.msg_id ?? null,
        event.event_type,
        event.direction ?? null,
        event.media ?? null,
        event.outcome ?? null,
        event.reason ?? null,
        event.call_type ?? null,
        event.duration_secs ?? 0,
        event.ts,
        event.participants ?? null
      );
    });
  }

  getStats(): StoreStats {
    return this.#withRetry(() => {
      const contactsRow = this.#db.prepare("SELECT COUNT(*) as count FROM contacts").get() as any;
      const chatsRow = this.#db.prepare("SELECT COUNT(*) as count FROM chats").get() as any;
      const messagesRow = this.#db.prepare("SELECT COUNT(*) as count FROM messages").get() as any;
      const unreadChatsRow = this.#db
        .prepare(
          "SELECT COUNT(*) as count FROM chats WHERE (unread = 1 OR unread_count > 0) AND jid != 'status@broadcast' AND kind NOT IN ('broadcast', 'newsletter')"
        )
        .get() as any;
      const unreadMsgsRow = this.#db
        .prepare(
          "SELECT COALESCE(SUM(unread_count), 0) as count FROM chats WHERE (unread = 1 OR unread_count > 0) AND jid != 'status@broadcast' AND kind NOT IN ('broadcast', 'newsletter')"
        )
        .get() as any;
      return {
        contacts: Number(contactsRow?.count ?? 0),
        chats: Number(chatsRow?.count ?? 0),
        messages: Number(messagesRow?.count ?? 0),
        unread_chats: Number(unreadChatsRow?.count ?? 0),
        unread_messages: Number(unreadMsgsRow?.count ?? 0),
      };
    });
  }

  close(): void {
    try {
      this.#db.close();
    } catch {}
  }
}
