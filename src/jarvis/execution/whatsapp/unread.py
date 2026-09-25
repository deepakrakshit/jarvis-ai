"""WhatsApp Unread Message Review and Dialogue Context Engine.

Implements unread communication inspection:
- Inspects all unread chats.
- Retrieves unread messages in chronological order with surrounding context.
- Resolves individual sender identities (preserving group participant distinction).
- Extracts clean structured dialogue timelines without canned templates or heuristic bias.
- Delegates intent analysis, reply necessity, and response composition to Gemini 3.8 Live.
- DRAFT-FIRST SAFETY GUARANTEE: Never sends automatically; drafts are held
  in memory/state until explicitly authorized by the operator via Action Broker.
- Preserves WhatsApp read/unread status on the phone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from jarvis.execution.whatsapp.store import (
    ChatRecord,
    WhatsAppDatabaseStore,
    whatsapp_store,
)


@dataclass
class ConversationReview:
    """Structured dialogue context and dynamic analysis state for an unread conversation."""

    chat: str
    canonical_jid: str
    display_name: str
    unread_count: int
    is_group: bool
    conversation_summary: str
    latest_message_text: str
    unread_messages: List[Dict[str, Any]]
    recent_dialogue: List[str]
    participants: List[str]
    draft: Optional[str] = None
    intent: Optional[str] = None
    requires_reply: Optional[bool] = None
    confidence: float = 1.0
    ambiguity_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chat": self.chat,
            "canonical_jid": self.canonical_jid,
            "display_name": self.display_name,
            "unread_count": self.unread_count,
            "is_group": self.is_group,
            "conversation_summary": self.conversation_summary,
            "latest_message_text": self.latest_message_text,
            "unread_messages": self.unread_messages,
            "recent_dialogue": self.recent_dialogue,
            "participants": self.participants,
            "draft": self.draft,
            "intent": self.intent,
            "requires_reply": self.requires_reply,
            "confidence": self.confidence,
            "ambiguity_reason": self.ambiguity_reason,
        }


class WhatsAppUnreadReviewer:
    """Manages unread conversation discovery, contextual analysis, and safe drafting."""

    def __init__(self, store: Optional[WhatsAppDatabaseStore] = None) -> None:
        self.store = store or whatsapp_store
        self._pending_drafts: Dict[str, str] = {}

    def get_pending_draft(self, jid: str) -> Optional[str]:
        """Retrieve a previously generated draft by canonical JID."""
        return self._pending_drafts.get(jid.strip())

    def set_pending_draft(self, jid: str, draft: str) -> None:
        """Store or update a pending draft for operator review."""
        self._pending_drafts[jid.strip()] = draft

    def clear_pending_draft(self, jid: str) -> None:
        """Clear a held draft once sent or rejected."""
        self._pending_drafts.pop(jid.strip(), None)

    async def review_unread_chats(
        self,
        max_chats: int = 15,
        context_limit: int = 5,
    ) -> Dict[str, Any]:
        """Inspect unread conversations, extract dialogue context, and prepare review payload."""
        unread_chats = self.store.get_unread_chats()
        if not unread_chats:
            return {
                "unread_chats_count": 0,
                "total_unread_messages": 0,
                "reviews": [],
                "message": "All WhatsApp messages are currently read. No pending unread chats.",
            }

        reviews: List[ConversationReview] = []
        total_messages = 0

        for chat in unread_chats[:max_chats]:
            review = await self._analyze_chat(chat, context_limit=context_limit)
            if review:
                reviews.append(review)
                total_messages += review.unread_count
                if review.draft:
                    self._pending_drafts[review.canonical_jid] = review.draft

        return {
            "unread_chats_count": len(reviews),
            "total_unread_messages": total_messages,
            "reviews": [r.to_dict() for r in reviews],
            "safety_policy": (
                "DRAFT_FIRST_SAFETY: No messages have been sent. Gemini 3.8 Live dynamically evaluates conversation intent, "
                "decides whether a response is required, formulates natural draft replies, and presents them for operator "
                "approval before sending."
            ),
        }

    async def _analyze_chat(
        self,
        chat: ChatRecord,
        context_limit: int = 5,
    ) -> Optional[ConversationReview]:
        """Extract unread messages and surrounding conversation context for cognitive evaluation."""
        if (
            chat.jid == "status@broadcast"
            or chat.kind in ("broadcast", "newsletter")
            or chat.jid.endswith("@newsletter")
        ):
            return None

        context_data = self.store.get_unread_messages_with_context(
            chat_jid=chat.jid,
            max_unread=chat.unread_count or 10,
            context_before=context_limit,
        )

        unread_msgs = context_data.get("unread_messages", [])
        if not unread_msgs:
            return None

        is_group = chat.kind == "group" or chat.jid.endswith("@g.us")
        chat_name = chat.name

        # Resolve display name
        if not chat_name:
            contact = self.store.get_contact(chat.jid)
            chat_name = contact.display_name if contact else chat.jid.split("@")[0]

        # Gather sender names, unique participants, and formatted dialogue lines
        dialogue_lines: List[str] = []
        unique_senders: set[str] = set()

        for m in context_data.get("full_conversation", []):
            sender_label = "Me" if m.from_me else (m.sender_name or m.sender_jid or "Sender")
            if not m.from_me and m.sender_jid:
                unique_senders.add(m.sender_jid)
            text_content = m.display_text or m.text or "[media]"
            dialogue_lines.append(f"{sender_label}: {text_content}")

        latest_unread = unread_msgs[-1]
        latest_text = (latest_unread.display_text or latest_unread.text or "").strip()

        # Build clean structured unread message items for model consumption
        unread_details: List[Dict[str, Any]] = [
            {
                "msg_id": m.msg_id,
                "ts": m.ts,
                "sender_jid": m.sender_jid,
                "sender_name": m.sender_name or (chat_name if not is_group else "Participant"),
                "text": m.text or m.display_text or "",
                "media_type": m.media_type,
                "local_path": m.local_path,
                "has_media": bool(m.media_type),
            }
            for m in unread_msgs
        ]

        # Objective conversation summary without hardcoded assumptions
        summary = f"{len(unread_msgs)} unread message(s) from {chat_name}."
        if dialogue_lines:
            summary += " Recent dialogue context:\n" + "\n".join(dialogue_lines[-5:])

        existing_draft = self._pending_drafts.get(chat.jid)

        return ConversationReview(
            chat=chat.jid,
            canonical_jid=chat.jid,
            display_name=chat_name,
            unread_count=len(unread_msgs),
            is_group=is_group,
            conversation_summary=summary,
            latest_message_text=latest_text,
            unread_messages=unread_details,
            recent_dialogue=dialogue_lines,
            participants=list(unique_senders),
            draft=existing_draft,
            intent=None,
            requires_reply=None,
            confidence=1.0,
        )


# Global unread reviewer instance
whatsapp_unread_reviewer = WhatsAppUnreadReviewer()
