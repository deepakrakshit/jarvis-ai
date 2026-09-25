"""Comprehensive Test Suite for JARVIS Unified WhatsApp Subsystem.

Verifies:
- SQLite local database mirror operations (contacts, aliases, tags, LIDs, chats, messages, FTS5)
- Native Contact & Identity Resolver with alias precedence and multi-candidate disambiguation
- Unread message reviewer with draft-first safety guarantees (never sends automatically)
- Unified WhatsAppNode ActionBroker capability registration and execution
- Gemini Live and Telegram Live tool declaration and routing to WHATSAPP_NODE
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.actions.broker import ActionBroker
from jarvis.cognition.gemini_live import (
    DEFAULT_LIVE_TOOLS,
)
from jarvis.cognition.gemini_live import (
    TOOL_TO_CAPABILITY_MAP as GEMINI_TOOL_MAP,
)
from jarvis.contracts.action import ActionRequest, ActionStatus, RiskTier
from jarvis.execution.whatsapp.node import WhatsAppNode
from jarvis.execution.whatsapp.resolver import JARVISContactResolver
from jarvis.execution.whatsapp.store import (
    MessageRecord,
    WhatsAppDatabaseStore,
)
from jarvis.execution.whatsapp.unread import WhatsAppUnreadReviewer
from jarvis.policy.firewall import (
    CAPABILITY_WHATSAPP_CHATS_LIST,
    CAPABILITY_WHATSAPP_CONTACTS_ALIAS,
    CAPABILITY_WHATSAPP_CONTACTS_SEARCH,
    CAPABILITY_WHATSAPP_REVIEW_UNREAD,
    CAPABILITY_WHATSAPP_SEND_TEXT,
    CAPABILITY_WHATSAPP_SYNC,
)
from jarvis.telegram.live_session import (
    TELEGRAM_LIVE_TOOLS_SPEC,
)
from jarvis.telegram.live_session import (
    TOOL_TO_CAPABILITY_MAP as TELEGRAM_TOOL_MAP,
)

# ==============================================================================
# 1. SQLite Local Mirror Store Tests
# ==============================================================================


def test_whatsapp_store_crud(tmp_path: Path) -> None:
    """Test contact, chat, and message storage and retrieval."""
    db_file = tmp_path / "test_whatsapp.db"
    store = WhatsAppDatabaseStore(db_path=db_file)

    # 1. Contacts
    store.upsert_contact(
        jid="919876543210@s.whatsapp.net",
        phone="919876543210",
        full_name="Deepak Rakshit",
        push_name="Deepak",
    )
    store.upsert_contact(
        jid="917088669912@s.whatsapp.net",
        phone="917088669912",
        full_name="Lucifer Morningstar",
        push_name="Lucifer",
    )

    contacts = store.search_contacts("Deepak")
    assert len(contacts) == 1
    assert contacts[0].phone == "919876543210"
    assert contacts[0].display_name == "Deepak Rakshit"

    # Aliases
    store.set_alias("917088669912@s.whatsapp.net", "Boss")
    boss_contact = store.get_contact("917088669912@s.whatsapp.net")
    assert boss_contact is not None
    assert boss_contact.alias == "Boss"
    assert boss_contact.display_name == "Boss"

    # Tags
    store.add_tag("917088669912@s.whatsapp.net", "VIP")
    store.add_tag("917088669912@s.whatsapp.net", "Core")
    tags = store.get_tags("917088669912@s.whatsapp.net")
    assert "vip" in tags
    assert "core" in tags

    store.remove_tag("917088669912@s.whatsapp.net", "VIP")
    assert "vip" not in store.get_tags("917088669912@s.whatsapp.net")

    # LID Mappings
    store.upsert_lid_mapping("123456789@lid", "919876543210")
    resolved_pn = store.resolve_lid_to_pn("123456789@lid")
    assert resolved_pn == "919876543210"

    # 2. Chats
    store.upsert_chat(
        jid="919876543210@s.whatsapp.net",
        kind="direct",
        name="Deepak",
        last_message_ts=int(time.time()),
        unread=1,
        unread_count=2,
    )
    store.upsert_chat(
        jid="12036304@g.us",
        kind="group",
        name="PBL Project Team",
        last_message_ts=int(time.time()),
        unread=0,
        unread_count=0,
    )

    chats = store.list_chats(limit=10)
    assert len(chats) == 2
    unread_chats = store.get_unread_chats()
    assert len(unread_chats) == 1
    assert unread_chats[0].jid == "919876543210@s.whatsapp.net"

    # 3. Messages
    now = int(time.time())
    store.upsert_message(
        MessageRecord(
            chat_jid="919876543210@s.whatsapp.net",
            msg_id="MSG001",
            ts=now - 10,
            from_me=0,
            sender_name="Deepak",
            text="Hello JARVIS, are you online?",
        )
    )
    store.upsert_message(
        MessageRecord(
            chat_jid="919876543210@s.whatsapp.net",
            msg_id="MSG002",
            ts=now,
            from_me=0,
            sender_name="Deepak",
            text="Are you going to college today?",
        )
    )

    msgs = store.list_messages("919876543210@s.whatsapp.net")
    assert len(msgs) == 2

    # Context
    ctx = store.get_message_context("919876543210@s.whatsapp.net", "MSG002", radius=2)
    assert ctx["found"] is True
    assert len(ctx["all_chronological"]) >= 1

    # Search
    search_res = store.search_messages("college")
    assert len(search_res) >= 1
    assert search_res[0].msg_id == "MSG002"

    # Stats
    stats = store.get_stats()
    assert stats["contacts"] == 2
    assert stats["chats"] == 2
    assert stats["messages"] == 2


# ==============================================================================
# 2. Contact & Identity Resolver Tests
# ==============================================================================


def test_contact_resolver_exact_and_alias(tmp_path: Path) -> None:
    """Verify phone, JID, and operator alias resolution."""
    db_file = tmp_path / "test_whatsapp.db"
    store = WhatsAppDatabaseStore(db_path=db_file)
    store.upsert_contact(
        jid="919876543210@s.whatsapp.net",
        phone="919876543210",
        full_name="Alex Johnson",
        push_name="Alex",
    )
    store.set_alias("919876543210@s.whatsapp.net", "Partner")

    resolver = JARVISContactResolver(store=store)

    # 1. Resolve by alias (highest priority)
    res = resolver.resolve("Partner")
    assert res.resolved is True
    assert res.canonical_jid == "919876543210@s.whatsapp.net"
    assert res.display_name == "Partner"

    # 2. Resolve by phone number
    res_phone = resolver.resolve("+91 98765 43210")
    assert res_phone.resolved is True
    assert res_phone.canonical_jid == "919876543210@s.whatsapp.net"

    # 3. Resolve by direct JID
    res_jid = resolver.resolve("919876543210@s.whatsapp.net")
    assert res_jid.resolved is True


def test_contact_resolver_disambiguation(tmp_path: Path) -> None:
    """Verify resolver NEVER guesses between multiple matching contacts."""
    db_file = tmp_path / "test_whatsapp.db"
    store = WhatsAppDatabaseStore(db_path=db_file)

    # Insert two contacts sharing the first name 'Deepak'
    store.upsert_contact(
        jid="919876543210@s.whatsapp.net",
        phone="919876543210",
        full_name="Deepak Sharma",
    )
    store.upsert_contact(
        jid="917088669912@s.whatsapp.net",
        phone="917088669912",
        full_name="Deepak Verma",
    )

    resolver = JARVISContactResolver(store=store)
    res = resolver.resolve("Deepak")

    # MUST be ambiguous, NOT resolved to a random contact
    assert res.resolved is False
    assert res.ambiguous is True
    assert len(res.candidates) == 2
    assert res.disambiguation_prompt is not None
    assert "found 2 contacts" in res.disambiguation_prompt.lower()
    assert "Deepak Sharma" in res.disambiguation_prompt
    assert "Deepak Verma" in res.disambiguation_prompt


def test_contact_resolver_lid_handling(tmp_path: Path) -> None:
    """Verify resolver maps known LIDs to phone numbers and marks unmapped LIDs safely."""
    db_file = tmp_path / "test_whatsapp.db"
    store = WhatsAppDatabaseStore(db_path=db_file)
    store.upsert_lid_mapping("123456789@lid", "919876543210")
    store.upsert_contact(
        jid="919876543210@s.whatsapp.net",
        phone="919876543210",
        full_name="LID User",
    )

    resolver = JARVISContactResolver(store=store)

    # Known LID
    res = resolver.resolve("123456789@lid")
    assert res.resolved is True
    assert res.canonical_jid == "919876543210@s.whatsapp.net"

    # Unmapped LID
    res_unmapped = resolver.resolve("999999999@lid")
    assert res_unmapped.resolved is False
    assert res_unmapped.error is not None
    assert "unmapped" in res_unmapped.error.lower()


# ==============================================================================
# 3. Unread Message Review & Draft-First Safety Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_unread_reviewer_draft_first_safety(tmp_path: Path) -> None:
    """Verify unread reviewer generates contextual drafts and never sends automatically."""
    db_file = tmp_path / "test_whatsapp.db"
    store = WhatsAppDatabaseStore(db_path=db_file)

    now = int(time.time())
    store.upsert_contact(
        jid="919876543210@s.whatsapp.net",
        phone="919876543210",
        full_name="Professor Smith",
    )
    store.upsert_chat(
        jid="919876543210@s.whatsapp.net",
        kind="direct",
        name="Professor Smith",
        last_message_ts=now,
        unread=1,
        unread_count=1,
    )
    store.upsert_message(
        MessageRecord(
            chat_jid="919876543210@s.whatsapp.net",
            msg_id="MSG_UNREAD_1",
            ts=now,
            from_me=0,
            sender_name="Professor Smith",
            text="Are you going to college today for the presentation?",
        )
    )

    reviewer = WhatsAppUnreadReviewer(store=store)
    review_output = await reviewer.review_unread_chats()

    assert review_output["unread_chats_count"] == 1
    assert review_output["total_unread_messages"] == 1
    assert "DRAFT_FIRST_SAFETY" in review_output["safety_policy"]

    review = review_output["reviews"][0]
    assert review["canonical_jid"] == "919876543210@s.whatsapp.net"
    assert review["display_name"] == "Professor Smith"
    assert review["unread_count"] == 1
    assert len(review["unread_messages"]) == 1
    assert (
        review["unread_messages"][0]["text"]
        == "Are you going to college today for the presentation?"
    )
    assert len(review["recent_dialogue"]) >= 1

    # Simulate Gemini 3.8 Live formulating a draft and setting it into pending review
    dynamic_draft = "Good day Professor, yes I will be at college for the presentation."
    reviewer.set_pending_draft("919876543210@s.whatsapp.net", dynamic_draft)

    # Verify draft is held in memory for operator confirmation
    pending = reviewer.get_pending_draft("919876543210@s.whatsapp.net")
    assert pending == dynamic_draft

    # Clear pending draft
    reviewer.clear_pending_draft("919876543210@s.whatsapp.net")
    assert reviewer.get_pending_draft("919876543210@s.whatsapp.net") is None


# ==============================================================================
# 4. WhatsAppNode ActionBroker Execution Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_whatsapp_node_action_broker_integration(tmp_path: Path) -> None:
    """Verify WhatsAppNode registers and executes canonical capabilities cleanly."""
    db_file = tmp_path / "test_whatsapp.db"
    store = WhatsAppDatabaseStore(db_path=db_file)
    store.upsert_contact(
        jid="919876543210@s.whatsapp.net",
        phone="919876543210",
        full_name="Deepak Rakshit",
    )
    store.upsert_chat(
        jid="919876543210@s.whatsapp.net",
        kind="direct",
        name="Deepak",
        last_message_ts=int(time.time()),
        unread=1,
        unread_count=1,
    )

    mock_caller = MagicMock()
    mock_caller.sync = AsyncMock(return_value={"success": True, "output": "Synced"})
    mock_caller.send_text = AsyncMock(return_value={"success": True, "sent": True})
    mock_caller.check_number = AsyncMock(
        return_value={"registered": True, "jid": "919876543210@s.whatsapp.net"}
    )

    resolver = JARVISContactResolver(store=store)
    unread_reviewer = WhatsAppUnreadReviewer(store=store)

    node = WhatsAppNode(
        caller=mock_caller,
        store=store,
        resolver=resolver,
        unread_reviewer=unread_reviewer,
    )
    node.register_capabilities()

    broker = ActionBroker()

    # 1. whatsapp.contacts.search
    req_search = ActionRequest(
        task_id="TASK-WA-01",
        session_id="SESS-WA-01",
        capability=CAPABILITY_WHATSAPP_CONTACTS_SEARCH,
        arguments={"query": "Deepak"},
        risk_tier=RiskTier.READ_ONLY,
    )
    res_search = await broker.execute(req_search)
    assert res_search.status == ActionStatus.SUCCEEDED
    assert res_search.output["count"] == 1

    # 2. whatsapp.contacts.alias
    req_alias = ActionRequest(
        task_id="TASK-WA-02",
        session_id="SESS-WA-01",
        capability=CAPABILITY_WHATSAPP_CONTACTS_ALIAS,
        arguments={"jid": "919876543210@s.whatsapp.net", "alias": "Operator"},
        risk_tier=RiskTier.LOW,
    )
    res_alias = await broker.execute(req_alias)
    assert res_alias.status == ActionStatus.SUCCEEDED
    assert res_alias.output["action"] == "set"

    # 3. whatsapp.chats.list
    req_chats = ActionRequest(
        task_id="TASK-WA-03",
        session_id="SESS-WA-01",
        capability=CAPABILITY_WHATSAPP_CHATS_LIST,
        arguments={"limit": 10},
        risk_tier=RiskTier.READ_ONLY,
    )
    res_chats = await broker.execute(req_chats)
    assert res_chats.status == ActionStatus.SUCCEEDED
    assert res_chats.output["count"] >= 1

    # 4. whatsapp.send.text with resolved contact
    req_send = ActionRequest(
        task_id="TASK-WA-04",
        session_id="SESS-WA-01",
        capability=CAPABILITY_WHATSAPP_SEND_TEXT,
        arguments={"recipient": "Operator", "message": "All systems operational."},
        risk_tier=RiskTier.MEDIUM,
    )
    res_send = await broker.execute(req_send)
    assert res_send.status == ActionStatus.SUCCEEDED
    mock_caller.send_text.assert_called_once_with(
        recipient="919876543210@s.whatsapp.net", message="All systems operational."
    )

    # 5. whatsapp.send.text with unmapped LID -> Must reject safely
    req_send_lid = ActionRequest(
        task_id="TASK-WA-05",
        session_id="SESS-WA-01",
        capability=CAPABILITY_WHATSAPP_SEND_TEXT,
        arguments={"recipient": "999888777@lid", "message": "Test unsafe LID send"},
        risk_tier=RiskTier.MEDIUM,
    )
    res_send_lid = await broker.execute(req_send_lid)
    assert res_send_lid.output.get("success") is False
    assert "unmapped" in str(res_send_lid.output.get("error", "")).lower()


# ==============================================================================
# 5. Live Tool Wiring & Routing Tests
# ==============================================================================


def test_gemini_and_telegram_live_tool_declarations() -> None:
    """Verify tool specs and capability maps in Gemini Live and Telegram Live engines."""
    # 1. Gemini Live Tools
    gemini_tool_names = {t["name"] for t in DEFAULT_LIVE_TOOLS}
    assert "whatsapp_search_contacts" in gemini_tool_names
    assert "whatsapp_send_message" in gemini_tool_names
    assert "whatsapp_review_unread" in gemini_tool_names
    assert "whatsapp_sync" in gemini_tool_names

    assert GEMINI_TOOL_MAP["whatsapp_search_contacts"] == CAPABILITY_WHATSAPP_CONTACTS_SEARCH
    assert GEMINI_TOOL_MAP["whatsapp_send_message"] == CAPABILITY_WHATSAPP_SEND_TEXT
    assert GEMINI_TOOL_MAP["whatsapp_review_unread"] == CAPABILITY_WHATSAPP_REVIEW_UNREAD
    assert GEMINI_TOOL_MAP["whatsapp_sync"] == CAPABILITY_WHATSAPP_SYNC

    # 2. Telegram Live Tools
    tg_tool_names = {t["name"] for t in TELEGRAM_LIVE_TOOLS_SPEC}
    assert "whatsapp_search_contacts" in tg_tool_names
    assert "whatsapp_send_message" in tg_tool_names
    assert "whatsapp_review_unread" in tg_tool_names
    assert "whatsapp_sync" in tg_tool_names

    assert TELEGRAM_TOOL_MAP["whatsapp_search_contacts"] == CAPABILITY_WHATSAPP_CONTACTS_SEARCH
    assert TELEGRAM_TOOL_MAP["whatsapp_send_message"] == CAPABILITY_WHATSAPP_SEND_TEXT
    assert TELEGRAM_TOOL_MAP["whatsapp_review_unread"] == CAPABILITY_WHATSAPP_REVIEW_UNREAD
    assert TELEGRAM_TOOL_MAP["whatsapp_sync"] == CAPABILITY_WHATSAPP_SYNC


# ==============================================================================
# 6. Unread Clearing Position Cutoff & LID Cross-Clearing Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_unread_clearing_on_reply_position_cutoff(tmp_path: Path) -> None:
    """Verify that once a reply is sent, all messages prior to reply are cleared from unread."""
    db_file = tmp_path / "test_whatsapp.db"
    store = WhatsAppDatabaseStore(db_path=db_file)
    reviewer = WhatsAppUnreadReviewer(store=store)

    chat_jid = "919876543210@s.whatsapp.net"
    store.upsert_contact(
        jid=chat_jid,
        phone="919876543210",
        full_name="Deepak Rakshit",
    )

    # 1. Incoming message from contact arrives
    t1 = 1790000000
    store.upsert_message(
        MessageRecord(
            chat_jid=chat_jid,
            msg_id="MSG_01",
            ts=t1,
            from_me=0,
            sender_name="Deepak",
            text="Bhai kal college aayega?",
        )
    )
    store.increment_chat_unread(chat_jid, t1, "Deepak")

    # Reviewer detects 1 unread message
    review_1 = await reviewer.review_unread_chats()
    assert review_1["unread_chats_count"] == 1
    assert review_1["total_unread_messages"] == 1
    assert review_1["reviews"][0]["unread_messages"][0]["text"] == "Bhai kal college aayega?"

    # 2. User or JARVIS replies to Deepak
    t2 = t1 + 60
    store.upsert_message(
        MessageRecord(
            chat_jid=chat_jid,
            msg_id="MSG_REPLY_01",
            ts=t2,
            from_me=1,
            text="Kal chutti hai bhai.",
        )
    )

    # Reviewer MUST now detect 0 unread messages (never re-read already-replied messages!)
    review_2 = await reviewer.review_unread_chats()
    assert review_2["unread_chats_count"] == 0
    assert review_2["total_unread_messages"] == 0
    assert len(review_2["reviews"]) == 0

    # 3. New incoming message arrives AFTER the reply
    t3 = t2 + 60
    store.upsert_message(
        MessageRecord(
            chat_jid=chat_jid,
            msg_id="MSG_02",
            ts=t3,
            from_me=0,
            sender_name="Deepak",
            text="Accha theek hai, parso milte hain.",
        )
    )
    store.increment_chat_unread(chat_jid, t3, "Deepak")

    # Reviewer MUST catch the new incoming message, with previous messages in context!
    review_3 = await reviewer.review_unread_chats()
    assert review_3["unread_chats_count"] == 1
    assert review_3["total_unread_messages"] == 1
    unread_msg = review_3["reviews"][0]["unread_messages"][0]
    assert unread_msg["text"] == "Accha theek hai, parso milte hain."
    recent_dialogue = review_3["reviews"][0]["recent_dialogue"]
    assert any("Kal chutti hai bhai." in line for line in recent_dialogue)


@pytest.mark.asyncio
async def test_lid_cross_clearing_and_migration(tmp_path: Path) -> None:
    """Verify incoming message under LID is cleared when reply is sent to canonical phone JID."""
    db_file = tmp_path / "test_whatsapp.db"
    store = WhatsAppDatabaseStore(db_path=db_file)
    reviewer = WhatsAppUnreadReviewer(store=store)

    lid_jid = "109354749567018@lid"
    phone_jid = "917088669912@s.whatsapp.net"
    phone_num = "917088669912"

    # Set up reverse LID mapping
    store.upsert_lid_mapping(lid_jid, phone_num)
    store.upsert_contact(
        jid=phone_jid,
        phone=phone_num,
        full_name="Deepak",
    )

    # Incoming message arrives under LID
    t1 = 1790000000
    store.upsert_message(
        MessageRecord(
            chat_jid=lid_jid,
            msg_id="MSG_LID_01",
            ts=t1,
            from_me=0,
            sender_name="Deepak",
            text="Hey JARVIS",
        )
    )
    store.increment_chat_unread(lid_jid, t1, "Deepak")

    # Reviewer detects the unread message under canonical phone JID
    review_1 = await reviewer.review_unread_chats()
    assert review_1["unread_chats_count"] == 1
    assert review_1["total_unread_messages"] == 1
    assert review_1["reviews"][0]["canonical_jid"] == phone_jid
    assert review_1["reviews"][0]["unread_messages"][0]["text"] == "Hey JARVIS"

    # JARVIS replies to Deepak via canonical phone JID
    t2 = t1 + 30
    store.upsert_message(
        MessageRecord(
            chat_jid=phone_jid,
            msg_id="MSG_REPLY_02",
            ts=t2,
            from_me=1,
            text="Hello Deepak, how can I assist you?",
        )
    )

    # Both LID and PN unread states are cleared through timestamp!
    review_2 = await reviewer.review_unread_chats()
    assert review_2["unread_chats_count"] == 0
    assert review_2["total_unread_messages"] == 0
