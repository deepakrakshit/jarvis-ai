"""WhatsApp SQLite Local Mirror Store for JARVIS.

Integrates the wacli local SQLite searchable mirror concept into JARVIS:
- contacts (phone, push_name, full_name, first_name, business_name, system_name)
- contact_aliases (local operator aliases with highest display precedence)
- contact_tags (grouping tags)
- lid_mappings (verified LID <-> phone number mappings)
- chats (kind, unread, unread_count, last_message_ts)
- messages (sender, text, display_text, media, reactions, quotes, timestamps)
- messages_fts (FTS5 virtual full-text search table)
- call_events (WhatsApp and VoIP calling events)
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from jarvis.config import settings
from jarvis.telemetry import logger


@dataclass
class ContactRecord:
    jid: str
    phone: Optional[str] = None
    push_name: Optional[str] = None
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    business_name: Optional[str] = None
    system_name: Optional[str] = None
    alias: Optional[str] = None
    display_name: str = ""
    tags: List[str] = field(default_factory=list)
    updated_at: int = 0


@dataclass
class ChatRecord:
    jid: str
    kind: str
    name: Optional[str] = None
    last_message_ts: Optional[int] = None
    archived: int = 0
    pinned: int = 0
    muted_until: int = 0
    unread: int = 0
    unread_count: int = 0


@dataclass
class MessageRecord:
    chat_jid: str
    msg_id: str
    ts: int
    from_me: int
    chat_name: Optional[str] = None
    sender_jid: Optional[str] = None
    sender_name: Optional[str] = None
    text: Optional[str] = None
    display_text: Optional[str] = None
    quoted_msg_id: Optional[str] = None
    quoted_sender_jid: Optional[str] = None
    is_forwarded: int = 0
    forwarding_score: int = 0
    reaction_to_id: Optional[str] = None
    reaction_emoji: Optional[str] = None
    media_type: Optional[str] = None
    media_caption: Optional[str] = None
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    local_path: Optional[str] = None
    revoked: int = 0
    deleted_for_me: int = 0
    deleted_at: Optional[int] = None
    deletion_reason: Optional[str] = None
    edited: int = 0
    edited_ts: int = 0
    delivery_status: str = "ACKNOWLEDGED"
    delivered_at: Optional[int] = None
    read_at: Optional[int] = None


class WhatsAppDatabaseStore:
    """Thread-safe SQLite store mirroring WhatsApp data locally."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = (
            db_path or settings.WHATSAPP_DB_PATH or (settings.DATA_DIR / "whatsapp" / "whatsapp.db")
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        try:
            self.reconcile_unread_chats()
        except Exception as e:
            logger.debug(f"Initial unread chat reconciliation notice: {e}")

    def _connect(self) -> sqlite3.Connection:
        """Create a connection with WAL mode and row factory."""
        con = sqlite3.connect(str(self.db_path), timeout=15.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode = WAL;")
        con.execute("PRAGMA synchronous = NORMAL;")
        con.execute("PRAGMA busy_timeout = 15000;")
        con.execute("PRAGMA foreign_keys = ON;")
        return con

    def _with_retry(self, fn: Any, max_retries: int = 5) -> Any:
        """Execute a database function with backoff retries on SQLITE_BUSY / locked."""
        import time

        for attempt in range(max_retries):
            try:
                return fn()
            except sqlite3.OperationalError as e:
                err_msg = str(e).lower()
                if ("locked" in err_msg or "busy" in err_msg) and attempt < max_retries - 1:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise

    def _init_schema(self) -> None:
        """Ensure all tables, triggers, and FTS5 indices exist."""
        with self._connect() as con:
            con.executescript("""
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

                CREATE INDEX IF NOT EXISTS idx_chats_unread ON chats(unread, unread_count);
                CREATE INDEX IF NOT EXISTS idx_chats_ts ON chats(last_message_ts);
                CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_jid, ts);
                CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);

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
                    chat_jid TEXT NOT NULL,
                    msg_id TEXT NOT NULL,
                    sender_jid TEXT,
                    sender_name TEXT,
                    ts INTEGER NOT NULL,
                    text TEXT,
                    media_type TEXT,
                    media_caption TEXT,
                    local_path TEXT,
                    UNIQUE(chat_jid, msg_id)
                );

                CREATE INDEX IF NOT EXISTS idx_status_messages_ts ON status_messages(ts);
            """)

            # Ensure columns exist on pre-existing messages tables before creating index
            for col_def in [
                "delivery_status TEXT DEFAULT 'ACKNOWLEDGED'",
                "delivered_at INTEGER",
                "read_at INTEGER",
            ]:
                try:
                    con.execute(f"ALTER TABLE messages ADD COLUMN {col_def};")
                except Exception:
                    pass

            try:
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_messages_delivery ON messages(delivery_status);"
                )
            except Exception:
                pass

            # Purge broadcast stories and normalize newsletter chats
            try:
                con.execute("DELETE FROM chats WHERE jid = 'status@broadcast';")
                con.execute("DELETE FROM messages WHERE chat_jid = 'status@broadcast';")
                con.execute(
                    "UPDATE chats SET kind = 'newsletter' WHERE jid LIKE '%@newsletter' AND kind != 'newsletter';"
                )
            except Exception:
                pass

            # Try to initialize FTS5 for message search
            try:
                con.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                        chat_jid UNINDEXED,
                        msg_id UNINDEXED,
                        text,
                        display_text,
                        sender_name,
                        chat_name
                    );
                """)
            except Exception as fts_err:
                logger.debug(f"FTS5 virtual table initialization notice: {fts_err}")

    # ──────────────────────────────────────────────────────────────────────────
    # Contacts & Identities
    # ──────────────────────────────────────────────────────────────────────────

    def upsert_contact(
        self,
        jid: str,
        phone: Optional[str] = None,
        push_name: Optional[str] = None,
        full_name: Optional[str] = None,
        first_name: Optional[str] = None,
        business_name: Optional[str] = None,
        system_name: Optional[str] = None,
        updated_at: Optional[int] = None,
    ) -> None:
        """Upsert a verified contact record."""
        import time

        now = updated_at or int(time.time())
        with self._connect() as con:
            con.execute(
                """
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
                """,
                (jid, phone, push_name, full_name, first_name, business_name, system_name, now),
            )

    def search_contacts(self, query: str, limit: int = 50) -> List[ContactRecord]:
        """Search contacts matching alias, name, push name, business name, phone, or JID.

        Implements wacli's display precedence:
        alias -> system_name -> full_name -> push_name -> business_name -> first_name -> phone -> jid
        """
        trimmed = query.strip()
        if not trimmed:
            return []

        pattern = f"%{trimmed.lower()}%"
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT
                    c.jid,
                    COALESCE(c.phone, '') as phone,
                    COALESCE(c.push_name, '') as push_name,
                    COALESCE(c.full_name, '') as full_name,
                    COALESCE(c.first_name, '') as first_name,
                    COALESCE(c.business_name, '') as business_name,
                    COALESCE(c.system_name, '') as system_name,
                    COALESCE(a.alias, '') as alias,
                    COALESCE(
                        NULLIF(a.alias, ''),
                        NULLIF(c.system_name, ''),
                        NULLIF(c.full_name, ''),
                        NULLIF(c.push_name, ''),
                        NULLIF(c.business_name, ''),
                        NULLIF(c.first_name, ''),
                        NULLIF(c.phone, ''),
                        c.jid
                    ) as display_name,
                    c.updated_at
                FROM contacts c
                LEFT JOIN contact_aliases a ON a.jid = c.jid
                WHERE LOWER(COALESCE(a.alias, '')) LIKE ?
                   OR LOWER(COALESCE(c.system_name, '')) LIKE ?
                   OR LOWER(COALESCE(c.full_name, '')) LIKE ?
                   OR LOWER(COALESCE(c.push_name, '')) LIKE ?
                   OR LOWER(COALESCE(c.business_name, '')) LIKE ?
                   OR LOWER(COALESCE(c.first_name, '')) LIKE ?
                   OR LOWER(COALESCE(c.phone, '')) LIKE ?
                   OR LOWER(c.jid) LIKE ?
                ORDER BY display_name ASC
                LIMIT ?
                """,
                (pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern, limit),
            ).fetchall()

            results: List[ContactRecord] = []
            for r in rows:
                jid = str(r["jid"])
                tags = self.get_tags(jid)
                results.append(
                    ContactRecord(
                        jid=jid,
                        phone=str(r["phone"]) or None,
                        push_name=str(r["push_name"]) or None,
                        full_name=str(r["full_name"]) or None,
                        first_name=str(r["first_name"]) or None,
                        business_name=str(r["business_name"]) or None,
                        system_name=str(r["system_name"]) or None,
                        alias=str(r["alias"]) or None,
                        display_name=str(r["display_name"]),
                        tags=tags,
                        updated_at=int(r["updated_at"]),
                    )
                )
            return results

    def get_contact(self, jid_or_phone: str) -> Optional[ContactRecord]:
        """Retrieve single contact by JID or phone number."""
        clean = jid_or_phone.strip()
        with self._connect() as con:
            row = con.execute(
                """
                SELECT
                    c.jid,
                    COALESCE(c.phone, '') as phone,
                    COALESCE(c.push_name, '') as push_name,
                    COALESCE(c.full_name, '') as full_name,
                    COALESCE(c.first_name, '') as first_name,
                    COALESCE(c.business_name, '') as business_name,
                    COALESCE(c.system_name, '') as system_name,
                    COALESCE(a.alias, '') as alias,
                    COALESCE(
                        NULLIF(a.alias, ''),
                        NULLIF(c.system_name, ''),
                        NULLIF(c.full_name, ''),
                        NULLIF(c.push_name, ''),
                        NULLIF(c.business_name, ''),
                        NULLIF(c.first_name, ''),
                        NULLIF(c.phone, ''),
                        c.jid
                    ) as display_name,
                    c.updated_at
                FROM contacts c
                LEFT JOIN contact_aliases a ON a.jid = c.jid
                WHERE c.jid = ? OR c.phone = ? OR c.jid LIKE ?
                LIMIT 1
                """,
                (clean, clean, f"{clean}@%"),
            ).fetchone()

            if not row:
                return None

            jid = str(row["jid"])
            tags = self.get_tags(jid)
            return ContactRecord(
                jid=jid,
                phone=str(row["phone"]) or None,
                push_name=str(row["push_name"]) or None,
                full_name=str(row["full_name"]) or None,
                first_name=str(row["first_name"]) or None,
                business_name=str(row["business_name"]) or None,
                system_name=str(row["system_name"]) or None,
                alias=str(row["alias"]) or None,
                display_name=str(row["display_name"]),
                tags=tags,
                updated_at=int(row["updated_at"]),
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Aliases & Tags
    # ──────────────────────────────────────────────────────────────────────────

    def set_alias(self, jid: str, alias: str, notes: str = "") -> None:
        """Assign a local operator alias to a contact."""
        import time

        now = int(time.time())
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO contact_aliases (jid, alias, notes, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(jid) DO UPDATE SET
                    alias = excluded.alias,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (jid.strip(), alias.strip(), notes.strip(), now),
            )

    def remove_alias(self, jid: str) -> None:
        """Remove a local operator alias."""
        with self._connect() as con:
            con.execute("DELETE FROM contact_aliases WHERE jid = ?", (jid.strip(),))

    def add_tag(self, jid: str, tag: str) -> None:
        """Add a grouping tag to a contact."""
        import time

        now = int(time.time())
        with self._connect() as con:
            con.execute(
                """
                INSERT OR IGNORE INTO contact_tags (jid, tag, updated_at)
                VALUES (?, ?, ?)
                """,
                (jid.strip(), tag.strip().lower(), now),
            )

    def remove_tag(self, jid: str, tag: str) -> None:
        """Remove a grouping tag from a contact."""
        with self._connect() as con:
            con.execute(
                "DELETE FROM contact_tags WHERE jid = ? AND tag = ?",
                (jid.strip(), tag.strip().lower()),
            )

    def get_tags(self, jid: str) -> List[str]:
        """List all tags for a contact."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT tag FROM contact_tags WHERE jid = ? ORDER BY tag ASC",
                (jid.strip(),),
            ).fetchall()
            return [str(r["tag"]) for r in rows]

    # ──────────────────────────────────────────────────────────────────────────
    # LID Mappings
    # ──────────────────────────────────────────────────────────────────────────

    def upsert_lid_mapping(self, lid: str, pn: str) -> None:
        """Record a verified phone number <-> LID mapping."""
        import time

        now = int(time.time())
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO lid_mappings (lid, pn, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(lid) DO UPDATE SET
                    pn = excluded.pn,
                    updated_at = excluded.updated_at
                """,
                (lid.strip(), pn.strip(), now),
            )

    def resolve_lid_to_pn(self, lid: str) -> Optional[str]:
        """Resolve a verified LID to phone number if mapped."""
        clean = lid.strip()
        clean_lid = clean.split("@")[0].strip()
        with self._connect() as con:
            row = con.execute(
                "SELECT pn FROM lid_mappings WHERE lid = ? OR lid = ?",
                (clean, clean_lid),
            ).fetchone()
            if row and row["pn"]:
                return str(row["pn"])

        # Fallback: scan auth directory for lid-mapping-<clean_lid>_reverse.json
        auth_dirs: List[Path] = []
        if settings.WHATSAPP_AUTH_DIR:
            auth_dirs.append(Path(settings.WHATSAPP_AUTH_DIR))
        for candidate in [
            settings.WORKSPACE_DIR / "substrate" / "extensions" / "whatsapp" / "auth",
            settings.WORKSPACE_DIR / "substrate" / "extensions" / "whatsapp-voice" / "auth",
        ]:
            if candidate not in auth_dirs:
                auth_dirs.append(candidate)

        for ad in auth_dirs:
            rev_file = ad / f"lid-mapping-{clean_lid}_reverse.json"
            if rev_file.is_file():
                try:
                    import json

                    val = json.loads(rev_file.read_text(encoding="utf-8"))
                    if isinstance(val, str) and val:
                        pn = val.strip()
                        self.upsert_lid_mapping(clean, pn)
                        self.upsert_lid_mapping(clean_lid, pn)
                        with self._connect() as con:
                            con.execute(
                                "UPDATE contacts SET phone = COALESCE(phone, ?) WHERE jid LIKE ?",
                                (pn, f"{clean_lid}%"),
                            )
                        return pn
                except Exception as e:
                    logger.debug(f"Failed to read reverse LID mapping file {rev_file}: {e}")
        return None

    def canonical_jid(self, jid: str) -> str:
        """Resolve a JID (including LID) to its canonical phone number JID if mapped."""
        clean = jid.strip()
        if clean.endswith("@lid"):
            pn = self.resolve_lid_to_pn(clean)
            if pn:
                digits = re.sub(r"\D", "", pn)
                return f"{digits}@s.whatsapp.net"
        return clean

    def migrate_all_lids(self) -> int:
        """Merge historical LID message rows and duplicate chat rows into canonical phone JIDs."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT lid, pn FROM lid_mappings WHERE pn IS NOT NULL AND pn != ''"
            ).fetchall()

            if not rows:
                return 0

            count = 0
            for r in rows:
                raw_lid = str(r["lid"]).strip()
                lid_jid = raw_lid if "@" in raw_lid else f"{raw_lid}@lid"
                pn_digits = re.sub(r"\D", "", str(r["pn"]).strip())
                pn_jid = f"{pn_digits}@s.whatsapp.net"

                # Update messages
                con.execute(
                    "UPDATE messages SET chat_jid = ? WHERE chat_jid = ?",
                    (pn_jid, lid_jid),
                )
                con.execute(
                    "UPDATE messages SET sender_jid = ? WHERE sender_jid = ?",
                    (pn_jid, lid_jid),
                )
                con.execute(
                    "UPDATE messages SET quoted_sender_jid = ? WHERE quoted_sender_jid = ?",
                    (pn_jid, lid_jid),
                )

                # Merge chats
                lid_chat = con.execute("SELECT * FROM chats WHERE jid = ?", (lid_jid,)).fetchone()
                if lid_chat:
                    pn_chat = con.execute("SELECT * FROM chats WHERE jid = ?", (pn_jid,)).fetchone()
                    if pn_chat:
                        max_ts = max(
                            lid_chat["last_message_ts"] or 0,
                            pn_chat["last_message_ts"] or 0,
                        )
                        name = pn_chat["name"] or lid_chat["name"]
                        con.execute(
                            "UPDATE chats SET name = ?, last_message_ts = ? WHERE jid = ?",
                            (name, max_ts, pn_jid),
                        )
                    else:
                        con.execute(
                            """
                            INSERT INTO chats (jid, kind, name, last_message_ts, unread, unread_count, archived, pinned)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                pn_jid,
                                lid_chat["kind"] or "dm",
                                lid_chat["name"],
                                lid_chat["last_message_ts"],
                                lid_chat["unread"],
                                lid_chat["unread_count"],
                                lid_chat["archived"] or 0,
                                lid_chat["pinned"] or 0,
                            ),
                        )
                    con.execute("DELETE FROM chats WHERE jid = ?", (lid_jid,))
                    count += 1
            return count

    def clear_chat_unread_through(self, chat_jid: str, through_ts: int) -> None:
        """Mark incoming messages up through through_ts as read and recalculate chat unread count."""
        clean = self.canonical_jid(chat_jid)
        all_jids: Set[str] = {clean}
        with self._connect() as con:
            if clean.endswith("@s.whatsapp.net"):
                phone = clean.split("@")[0]
                rows = con.execute(
                    "SELECT lid FROM lid_mappings WHERE pn = ? OR pn = ?",
                    (phone, f"+{phone}"),
                ).fetchall()
                for r in rows:
                    all_jids.add(str(r["lid"]))
            elif clean.endswith("@lid"):
                pn = self.resolve_lid_to_pn(clean)
                if pn:
                    digits = re.sub(r"\D", "", pn)
                    all_jids.add(f"{digits}@s.whatsapp.net")

            now = int(time.time())
            for j in all_jids:
                con.execute(
                    """
                    UPDATE messages SET read_at = COALESCE(read_at, ?)
                    WHERE chat_jid = ? AND from_me = 0 AND ts <= ?
                    """,
                    (now, j, through_ts),
                )
                rem_row = con.execute(
                    """
                    SELECT COUNT(*) as count FROM messages
                    WHERE chat_jid = ? AND from_me = 0 AND ts > ?
                      AND (read_at IS NULL OR read_at = 0)
                      AND revoked = 0 AND deleted_for_me = 0 AND deleted_at IS NULL
                      AND COALESCE(reaction_to_id, '') = '' AND COALESCE(reaction_emoji, '') = ''
                      AND (COALESCE(display_text, '') != '(message)' OR TRIM(COALESCE(text, '')) != '')
                    """,
                    (j, through_ts),
                ).fetchone()

                rem = int(rem_row["count"]) if rem_row else 0
                con.execute(
                    "UPDATE chats SET unread = ?, unread_count = ? WHERE jid = ?",
                    (1 if rem > 0 else 0, rem, j),
                )

    def reconcile_unread_chats(self) -> None:
        """Reconcile unread chats against latest outgoing replies and remaining messages."""
        self.migrate_all_lids()
        with self._connect() as con:
            chats = con.execute(
                """
                SELECT jid FROM chats
                WHERE (unread > 0 OR unread_count > 0)
                  AND jid != 'status@broadcast'
                  AND kind NOT IN ('broadcast', 'newsletter')
                """
            ).fetchall()

            for c in chats:
                jid = str(c["jid"])
                reply_row = con.execute(
                    "SELECT MAX(ts) as max_ts FROM messages WHERE chat_jid = ? AND from_me = 1",
                    (jid,),
                ).fetchone()

                if reply_row and reply_row["max_ts"] is not None:
                    self.clear_chat_unread_through(jid, int(reply_row["max_ts"]))
                else:
                    # Only reconcile if messages exist in this chat
                    total_row = con.execute(
                        "SELECT COUNT(*) as total FROM messages WHERE chat_jid = ?",
                        (jid,),
                    ).fetchone()
                    total_msgs = int(total_row["total"]) if total_row else 0
                    if total_msgs > 0:
                        rem_row = con.execute(
                            """
                            SELECT COUNT(*) as count FROM messages
                            WHERE chat_jid = ? AND from_me = 0
                              AND (read_at IS NULL OR read_at = 0)
                              AND revoked = 0 AND deleted_for_me = 0 AND deleted_at IS NULL
                              AND COALESCE(reaction_to_id, '') = '' AND COALESCE(reaction_emoji, '') = ''
                              AND (COALESCE(display_text, '') != '(message)' OR TRIM(COALESCE(text, '')) != '')
                            """,
                            (jid,),
                        ).fetchone()
                        rem = int(rem_row["count"]) if rem_row else 0
                        if rem == 0:
                            con.execute(
                                "UPDATE chats SET unread = 0, unread_count = 0 WHERE jid = ?",
                                (jid,),
                            )

    # ──────────────────────────────────────────────────────────────────────────
    # Chats & Messages
    # ──────────────────────────────────────────────────────────────────────────

    def upsert_chat(
        self,
        jid: str,
        kind: str,
        name: Optional[str] = None,
        last_message_ts: Optional[int] = None,
        unread: int = 0,
        unread_count: int = 0,
        archived: int = 0,
        pinned: int = 0,
    ) -> None:
        """Upsert a chat record."""
        clean_jid = self.canonical_jid(jid)
        if clean_jid == "status@broadcast":
            return
        effective_kind = kind
        if clean_jid.endswith("@newsletter"):
            effective_kind = "newsletter"
        elif clean_jid.endswith("@g.us"):
            effective_kind = "group"

        with self._connect() as con:
            con.execute(
                """
                INSERT INTO chats (jid, kind, name, last_message_ts, unread, unread_count, archived, pinned)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jid) DO UPDATE SET
                    kind = excluded.kind,
                    name = COALESCE(NULLIF(excluded.name, ''), chats.name),
                    last_message_ts = CASE WHEN excluded.last_message_ts IS NOT NULL
                                           THEN MAX(COALESCE(chats.last_message_ts, 0), excluded.last_message_ts)
                                           ELSE chats.last_message_ts END,
                    unread = excluded.unread,
                    unread_count = excluded.unread_count,
                    archived = excluded.archived,
                    pinned = excluded.pinned
                """,
                (
                    clean_jid,
                    effective_kind,
                    name,
                    last_message_ts,
                    unread,
                    unread_count,
                    archived,
                    pinned,
                ),
            )

    def list_chats(
        self,
        limit: int = 50,
        unread_only: bool = False,
        include_archived: bool = True,
    ) -> List[ChatRecord]:
        """List chats ordered by recency."""
        conditions: List[str] = [
            "jid != 'status@broadcast'",
            "kind NOT IN ('broadcast', 'newsletter')",
        ]
        params: List[Any] = []
        if unread_only:
            conditions.append("(unread = 1 OR unread_count > 0)")
        if not include_archived:
            conditions.append("archived = 0")

        query = "SELECT * FROM chats"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY COALESCE(last_message_ts, 0) DESC LIMIT ?"
        params.append(limit)

        with self._connect() as con:
            rows = con.execute(query, tuple(params)).fetchall()
            return [
                ChatRecord(
                    jid=str(r["jid"]),
                    kind=str(r["kind"]),
                    name=str(r["name"]) if r["name"] else None,
                    last_message_ts=r["last_message_ts"],
                    archived=int(r["archived"]),
                    pinned=int(r["pinned"]),
                    muted_until=int(r["muted_until"]),
                    unread=int(r["unread"]),
                    unread_count=int(r["unread_count"]),
                )
                for r in rows
            ]

    def get_unread_chats(self) -> List[ChatRecord]:
        """Retrieve all currently unread chats."""
        self.reconcile_unread_chats()
        return self.list_chats(limit=100, unread_only=True)

    def get_chat(self, jid: str) -> Optional[ChatRecord]:
        """Retrieve a specific chat by JID."""
        with self._connect() as con:
            row = con.execute("SELECT * FROM chats WHERE jid = ?", (jid.strip(),)).fetchone()
            if not row:
                return None
            return ChatRecord(
                jid=str(row["jid"]),
                kind=str(row["kind"]),
                name=str(row["name"]) if row["name"] else None,
                last_message_ts=row["last_message_ts"],
                archived=int(row["archived"]),
                pinned=int(row["pinned"]),
                muted_until=int(row["muted_until"]),
                unread=int(row["unread"]),
                unread_count=int(row["unread_count"]),
            )

    def upsert_message(self, msg: MessageRecord) -> None:
        """Persist or update an incoming or outgoing message."""
        canonical_chat = self.canonical_jid(msg.chat_jid)
        canonical_sender = self.canonical_jid(msg.sender_jid) if msg.sender_jid else None
        canonical_quoted_sender = (
            self.canonical_jid(msg.quoted_sender_jid) if msg.quoted_sender_jid else None
        )

        # Route status broadcast stories to dedicated status_messages table
        if canonical_chat == "status@broadcast" or canonical_chat.endswith("@broadcast"):
            with self._connect() as con:
                con.execute(
                    """
                    INSERT INTO status_messages (
                        chat_jid, msg_id, sender_jid, sender_name, ts, text,
                        media_type, media_caption, local_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chat_jid, msg_id) DO UPDATE SET
                        text = COALESCE(excluded.text, status_messages.text),
                        media_type = COALESCE(excluded.media_type, status_messages.media_type),
                        media_caption = COALESCE(excluded.media_caption, status_messages.media_caption),
                        local_path = COALESCE(excluded.local_path, status_messages.local_path)
                    """,
                    (
                        canonical_chat,
                        msg.msg_id,
                        canonical_sender,
                        msg.sender_name,
                        msg.ts,
                        msg.text or msg.display_text,
                        msg.media_type,
                        msg.media_caption,
                        msg.local_path,
                    ),
                )
            return

        with self._connect() as con:
            # Clear unread state locally through msg.ts when sending outgoing message (from_me == 1)
            if msg.from_me:
                self.clear_chat_unread_through(canonical_chat, msg.ts)

            con.execute(
                """
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
                """,
                (
                    canonical_chat,
                    msg.chat_name,
                    msg.msg_id,
                    canonical_sender,
                    msg.sender_name,
                    msg.ts,
                    msg.from_me,
                    msg.text,
                    msg.display_text,
                    msg.quoted_msg_id,
                    canonical_quoted_sender,
                    msg.is_forwarded,
                    msg.forwarding_score,
                    msg.reaction_to_id,
                    msg.reaction_emoji,
                    msg.media_type,
                    msg.media_caption,
                    msg.filename,
                    msg.mime_type,
                    msg.local_path,
                    msg.revoked,
                    msg.deleted_for_me,
                    msg.deleted_at,
                    msg.deletion_reason,
                    msg.edited,
                    msg.edited_ts,
                    msg.delivery_status,
                    msg.delivered_at,
                    msg.read_at,
                ),
            )

            # Sync to FTS5 if available
            try:
                con.execute(
                    """
                    INSERT INTO messages_fts (chat_jid, msg_id, text, display_text, sender_name, chat_name)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        canonical_chat,
                        msg.msg_id,
                        msg.text or "",
                        msg.display_text or "",
                        msg.sender_name or "",
                        msg.chat_name or "",
                    ),
                )
            except Exception:
                pass

    def list_messages(
        self,
        chat_jid: Optional[str] = None,
        limit: int = 50,
        asc: bool = False,
        before_timestamp: Optional[int] = None,
    ) -> List[MessageRecord]:
        """List messages optionally filtered by chat JID."""
        order = "ASC" if asc else "DESC"
        conditions: List[str] = []
        params: List[Any] = []
        if chat_jid:
            conditions.append("chat_jid = ?")
            params.append(chat_jid.strip())
        if before_timestamp is not None:
            conditions.append("ts < ?")
            params.append(before_timestamp)

        query = "SELECT * FROM messages"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += f" ORDER BY ts {order} LIMIT ?"
        params.append(limit)

        with self._connect() as con:
            rows = con.execute(query, tuple(params)).fetchall()
            return [self._row_to_message(r) for r in rows]

    def search_messages(
        self,
        query: str,
        chat_jid: Optional[str] = None,
        limit: int = 20,
    ) -> List[MessageRecord]:
        """Search message text using FTS5 or LIKE query."""
        trimmed = query.strip()
        if not trimmed:
            return []

        with self._connect() as con:
            # First attempt FTS5
            try:
                fts_query = """
                    SELECT m.* FROM messages m
                    JOIN messages_fts f ON m.chat_jid = f.chat_jid AND m.msg_id = f.msg_id
                    WHERE messages_fts MATCH ?
                """
                params: List[Any] = [trimmed]
                if chat_jid:
                    fts_query += " AND m.chat_jid = ?"
                    params.append(chat_jid.strip())
                fts_query += " ORDER BY m.ts DESC LIMIT ?"
                params.append(limit)
                rows = con.execute(fts_query, tuple(params)).fetchall()
                if rows:
                    return [self._row_to_message(r) for r in rows]
            except Exception:
                pass

            # Fallback to LIKE
            like_pattern = f"%{trimmed.lower()}%"
            like_query = """
                SELECT * FROM messages
                WHERE (LOWER(COALESCE(text, '')) LIKE ? OR LOWER(COALESCE(display_text, '')) LIKE ?)
            """
            like_params: List[Any] = [like_pattern, like_pattern]
            if chat_jid:
                like_query += " AND chat_jid = ?"
                like_params.append(chat_jid.strip())
            like_query += " ORDER BY ts DESC LIMIT ?"
            like_params.append(limit)
            rows = con.execute(like_query, tuple(like_params)).fetchall()
            return [self._row_to_message(r) for r in rows]

    def get_message_context(
        self,
        chat_jid: str,
        msg_id: str,
        before: int = 3,
        after: int = 3,
        radius: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Retrieve chronological context around a specific message."""
        if radius is not None:
            before = radius
            after = radius

        with self._connect() as con:
            target_row = con.execute(
                "SELECT * FROM messages WHERE chat_jid = ? AND msg_id = ?",
                (chat_jid.strip(), msg_id.strip()),
            ).fetchone()

            if not target_row:
                return {"found": False, "target": None, "before": [], "after": []}

            target = self._row_to_message(target_row)
            before_rows = con.execute(
                """
                SELECT * FROM messages
                WHERE chat_jid = ? AND ts <= ? AND msg_id != ?
                ORDER BY ts DESC LIMIT ?
                """,
                (chat_jid.strip(), target.ts, target.msg_id, before),
            ).fetchall()

            after_rows = con.execute(
                """
                SELECT * FROM messages
                WHERE chat_jid = ? AND ts >= ? AND msg_id != ?
                ORDER BY ts ASC LIMIT ?
                """,
                (chat_jid.strip(), target.ts, target.msg_id, after),
            ).fetchall()

            # Order chronologically
            before_msgs = [self._row_to_message(r) for r in reversed(before_rows)]
            after_msgs = [self._row_to_message(r) for r in after_rows]

            return {
                "found": True,
                "target": target,
                "before": before_msgs,
                "after": after_msgs,
                "all_chronological": before_msgs + [target] + after_msgs,
            }

    def get_unread_messages_with_context(
        self,
        chat_jid: str,
        max_unread: int = 15,
        context_before: int = 3,
    ) -> Dict[str, Any]:
        """Fetch all unread messages for a chat plus preceding context messages."""
        clean = self.canonical_jid(chat_jid)
        if clean == "status@broadcast" or clean.endswith("@newsletter"):
            return {
                "chat_jid": clean,
                "unread_messages": [],
                "context_before": [],
                "full_conversation": [],
            }

        all_jids: List[str] = [clean]
        with self._connect() as con:
            if clean.endswith("@s.whatsapp.net"):
                phone = clean.split("@")[0]
                rows = con.execute(
                    "SELECT lid FROM lid_mappings WHERE pn = ? OR pn = ?",
                    (phone, f"+{phone}"),
                ).fetchall()
                for r in rows:
                    lid_val = str(r["lid"]).strip()
                    if lid_val not in all_jids:
                        all_jids.append(lid_val)
            elif clean.endswith("@lid"):
                pn = self.resolve_lid_to_pn(clean)
                if pn:
                    digits = re.sub(r"\D", "", pn)
                    pn_jid = f"{digits}@s.whatsapp.net"
                    if pn_jid not in all_jids:
                        all_jids.append(pn_jid)

            placeholders = ",".join("?" for _ in all_jids)

            chat_row = con.execute(
                f"SELECT unread, unread_count FROM chats WHERE jid IN ({placeholders}) ORDER BY unread DESC LIMIT 1",
                tuple(all_jids),
            ).fetchone()

            if chat_row and chat_row["unread"] == 0 and chat_row["unread_count"] == 0:
                return {
                    "chat_jid": clean,
                    "unread_messages": [],
                    "context_before": [],
                    "full_conversation": [],
                }

            limit_count = (
                int(chat_row["unread_count"])
                if chat_row and chat_row["unread_count"] > 0
                else max_unread
            )
            want = min(limit_count, max_unread)

            # Invariant: If there is an outgoing reply (from_me = 1), any incoming message
            # sent prior to or at that reply has already been addressed and cannot be unread.
            reply_row = con.execute(
                f"""
                SELECT MAX(ts) as max_ts FROM messages
                WHERE chat_jid IN ({placeholders}) AND from_me = 1
                """,
                tuple(all_jids),
            ).fetchone()
            latest_reply_ts = (
                int(reply_row["max_ts"]) if (reply_row and reply_row["max_ts"] is not None) else 0
            )

            # Query strictly eligible unread messages matching wacli specification
            unread_rows = con.execute(
                f"""
                SELECT * FROM (
                    SELECT * FROM messages
                    WHERE chat_jid IN ({placeholders})
                      AND from_me = 0
                      AND ts > ?
                      AND (read_at IS NULL OR read_at = 0)
                      AND revoked = 0
                      AND deleted_for_me = 0
                      AND deleted_at IS NULL
                      AND COALESCE(reaction_to_id, '') = ''
                      AND COALESCE(reaction_emoji, '') = ''
                      AND (COALESCE(display_text, '') != '(message)' OR TRIM(COALESCE(text, '')) != '')
                    ORDER BY ts DESC, rowid DESC
                    LIMIT ?
                ) ORDER BY ts ASC, rowid ASC
                """,
                (*all_jids, latest_reply_ts, want),
            ).fetchall()

            unread_msgs = [self._row_to_message(r) for r in unread_rows]
            if not unread_msgs:
                # No unread messages remain after latest reply cutoff; ensure chat is marked read
                self.mark_chat_read(clean)
                return {
                    "chat_jid": clean,
                    "unread_messages": [],
                    "context_before": [],
                    "full_conversation": [],
                }

            oldest_ts = unread_msgs[0].ts
            context_rows = con.execute(
                f"""
                SELECT * FROM (
                    SELECT * FROM messages
                    WHERE chat_jid IN ({placeholders}) AND ts < ?
                    ORDER BY ts DESC, rowid DESC
                    LIMIT ?
                ) ORDER BY ts ASC, rowid ASC
                """,
                (*all_jids, oldest_ts, context_before),
            ).fetchall()

            context_msgs = [self._row_to_message(r) for r in context_rows]

            return {
                "chat_jid": clean,
                "unread_messages": unread_msgs,
                "context_before": context_msgs,
                "full_conversation": context_msgs + unread_msgs,
            }

    def mark_chat_read(self, chat_jid: str) -> None:
        """Mark a chat as read locally in the SQLite mirror across all alias JIDs."""
        clean = self.canonical_jid(chat_jid)
        all_jids: Set[str] = {clean}
        with self._connect() as con:
            if clean.endswith("@s.whatsapp.net"):
                phone = clean.split("@")[0]
                rows = con.execute(
                    "SELECT lid FROM lid_mappings WHERE pn = ? OR pn = ?",
                    (phone, f"+{phone}"),
                ).fetchall()
                for r in rows:
                    all_jids.add(str(r["lid"]))
            elif clean.endswith("@lid"):
                pn = self.resolve_lid_to_pn(clean)
                if pn:
                    digits = re.sub(r"\D", "", pn)
                    all_jids.add(f"{digits}@s.whatsapp.net")

            now = int(time.time())
            for j in all_jids:
                con.execute(
                    "UPDATE chats SET unread = 0, unread_count = 0 WHERE jid = ?",
                    (j,),
                )
                con.execute(
                    "UPDATE messages SET read_at = COALESCE(read_at, ?) WHERE chat_jid = ? AND from_me = 0 AND (read_at IS NULL OR read_at = 0)",
                    (now, j),
                )

    def increment_chat_unread(
        self, chat_jid: str, ts: int, sender_name: Optional[str] = None
    ) -> None:
        """Increment unread count for a chat when a new incoming message arrives."""
        clean = chat_jid.strip()
        if clean == "status@broadcast" or clean.endswith("@newsletter"):
            return
        kind = "group" if clean.endswith("@g.us") else "dm"
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO chats (jid, kind, name, last_message_ts, unread, unread_count)
                VALUES (?, ?, ?, ?, 1, 1)
                ON CONFLICT(jid) DO UPDATE SET
                    last_message_ts = MAX(COALESCE(chats.last_message_ts, 0), excluded.last_message_ts),
                    unread = 1,
                    unread_count = COALESCE(chats.unread_count, 0) + 1
                """,
                (clean, kind, sender_name, ts),
            )

    def has_message(self, chat_jid: str, msg_id: str) -> bool:
        """Check if a message ID exists in the database."""
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM messages WHERE chat_jid = ? AND msg_id = ?",
                (chat_jid.strip(), msg_id.strip()),
            ).fetchone()
            return row is not None

    def update_delivery_status(
        self,
        chat_jid: str,
        msg_id: str,
        status: str,
        ts: Optional[int] = None,
    ) -> None:
        """Update message delivery status and corresponding timestamp."""
        with self._connect() as con:
            if status == "DELIVERED":
                con.execute(
                    """
                    UPDATE messages SET delivery_status = ?, delivered_at = COALESCE(?, delivered_at)
                    WHERE chat_jid = ? AND msg_id = ?
                    """,
                    (status, ts, chat_jid.strip(), msg_id.strip()),
                )
            elif status == "READ":
                con.execute(
                    """
                    UPDATE messages SET delivery_status = ?, read_at = COALESCE(?, read_at), delivered_at = COALESCE(delivered_at, ?)
                    WHERE chat_jid = ? AND msg_id = ?
                    """,
                    (status, ts, ts, chat_jid.strip(), msg_id.strip()),
                )
            else:
                con.execute(
                    "UPDATE messages SET delivery_status = ? WHERE chat_jid = ? AND msg_id = ?",
                    (status, chat_jid.strip(), msg_id.strip()),
                )

    def _row_to_message(self, r: sqlite3.Row) -> MessageRecord:
        keys = r.keys()
        return MessageRecord(
            chat_jid=str(r["chat_jid"]),
            msg_id=str(r["msg_id"]),
            ts=int(r["ts"]),
            from_me=int(r["from_me"]),
            chat_name=str(r["chat_name"]) if r["chat_name"] else None,
            sender_jid=str(r["sender_jid"]) if r["sender_jid"] else None,
            sender_name=str(r["sender_name"]) if r["sender_name"] else None,
            text=str(r["text"]) if r["text"] else None,
            display_text=str(r["display_text"]) if r["display_text"] else None,
            quoted_msg_id=str(r["quoted_msg_id"]) if r["quoted_msg_id"] else None,
            quoted_sender_jid=str(r["quoted_sender_jid"]) if r["quoted_sender_jid"] else None,
            is_forwarded=int(r["is_forwarded"]),
            forwarding_score=int(r["forwarding_score"]),
            reaction_to_id=str(r["reaction_to_id"]) if r["reaction_to_id"] else None,
            reaction_emoji=str(r["reaction_emoji"]) if r["reaction_emoji"] else None,
            media_type=str(r["media_type"]) if r["media_type"] else None,
            media_caption=str(r["media_caption"]) if r["media_caption"] else None,
            filename=str(r["filename"]) if r["filename"] else None,
            mime_type=str(r["mime_type"]) if r["mime_type"] else None,
            local_path=str(r["local_path"]) if r["local_path"] else None,
            revoked=int(r["revoked"]),
            deleted_for_me=int(r["deleted_for_me"]),
            deleted_at=r["deleted_at"],
            deletion_reason=str(r["deletion_reason"]) if r["deletion_reason"] else None,
            edited=int(r["edited"]),
            edited_ts=int(r["edited_ts"]),
            delivery_status=str(r["delivery_status"])
            if "delivery_status" in keys and r["delivery_status"]
            else "ACKNOWLEDGED",
            delivered_at=r["delivered_at"] if "delivered_at" in keys else None,
            read_at=r["read_at"] if "read_at" in keys else None,
        )

    def get_stats(self) -> Dict[str, int]:
        """Return store record counts including unread counts."""
        with self._connect() as con:
            contacts_cnt = con.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
            chats_cnt = con.execute(
                "SELECT COUNT(*) FROM chats WHERE jid != 'status@broadcast' AND kind NOT IN ('broadcast', 'newsletter')"
            ).fetchone()[0]
            messages_cnt = con.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_jid != 'status@broadcast'"
            ).fetchone()[0]
            calls_cnt = con.execute("SELECT COUNT(*) FROM call_events").fetchone()[0]
            unread_chats_cnt = con.execute(
                "SELECT COUNT(*) FROM chats WHERE (unread = 1 OR unread_count > 0) AND jid != 'status@broadcast' AND kind NOT IN ('broadcast', 'newsletter')"
            ).fetchone()[0]
            unread_msgs_cnt = con.execute(
                "SELECT COUNT(*) FROM messages WHERE from_me = 0 AND chat_jid != 'status@broadcast' AND (read_at IS NULL OR read_at = 0)"
            ).fetchone()[0]
            return {
                "contacts": int(contacts_cnt),
                "chats": int(chats_cnt),
                "messages": int(messages_cnt),
                "calls": int(calls_cnt),
                "unread_chats": int(unread_chats_cnt),
                "unread_messages": int(unread_msgs_cnt),
            }


# Global store instance
whatsapp_store = WhatsAppDatabaseStore()
