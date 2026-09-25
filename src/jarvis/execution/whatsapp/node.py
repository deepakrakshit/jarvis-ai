"""WhatsApp Execution Node for JARVIS Action Broker.

Bridges canonical WhatsApp capabilities into the autonomous VoIP calling engine.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from jarvis.actions.registry import capability_registry
from jarvis.config import settings
from jarvis.contracts.action import ActionRequest
from jarvis.execution.whatsapp.resolver import (
    JARVISContactResolver,
    jarvis_contact_resolver,
)
from jarvis.execution.whatsapp.store import (
    WhatsAppDatabaseStore,
    whatsapp_store,
)
from jarvis.execution.whatsapp.unread import (
    WhatsAppUnreadReviewer,
    whatsapp_unread_reviewer,
)
from jarvis.execution.whatsapp.voice_caller import (
    WhatsAppVoiceCaller,
    get_whatsapp_caller,
)
from jarvis.policy.firewall import (
    CAPABILITY_WHATSAPP_CALL,
    CAPABILITY_WHATSAPP_CHATS_GET,
    CAPABILITY_WHATSAPP_CHATS_LIST,
    CAPABILITY_WHATSAPP_CHATS_UNREAD,
    CAPABILITY_WHATSAPP_CONTACTS_ALIAS,
    CAPABILITY_WHATSAPP_CONTACTS_CHECK,
    CAPABILITY_WHATSAPP_CONTACTS_GET,
    CAPABILITY_WHATSAPP_CONTACTS_REFRESH,
    CAPABILITY_WHATSAPP_CONTACTS_SEARCH,
    CAPABILITY_WHATSAPP_CONTACTS_TAGS,
    CAPABILITY_WHATSAPP_HISTORY,
    CAPABILITY_WHATSAPP_LOGIN,
    CAPABILITY_WHATSAPP_LOGOUT,
    CAPABILITY_WHATSAPP_MEDIA_DOWNLOAD,
    CAPABILITY_WHATSAPP_MESSAGES_CONTEXT,
    CAPABILITY_WHATSAPP_MESSAGES_LIST,
    CAPABILITY_WHATSAPP_MESSAGES_SEARCH,
    CAPABILITY_WHATSAPP_REVIEW_UNREAD,
    CAPABILITY_WHATSAPP_SEND_FILE,
    CAPABILITY_WHATSAPP_SEND_REACTION,
    CAPABILITY_WHATSAPP_SEND_REPLY,
    CAPABILITY_WHATSAPP_SEND_TEXT,
    CAPABILITY_WHATSAPP_SEND_VOICE,
    CAPABILITY_WHATSAPP_STATUS,
    CAPABILITY_WHATSAPP_SYNC,
)
from jarvis.telemetry import logger


class WhatsAppNode:
    """Execution node for unified WhatsApp VoIP, messaging, contacts, and unread review."""

    def __init__(
        self,
        caller: Optional[WhatsAppVoiceCaller] = None,
        store: Optional[WhatsAppDatabaseStore] = None,
        resolver: Optional[JARVISContactResolver] = None,
        unread_reviewer: Optional[WhatsAppUnreadReviewer] = None,
    ) -> None:
        self.caller = caller or get_whatsapp_caller()
        self.store = store or whatsapp_store
        self.resolver = resolver or jarvis_contact_resolver
        self.unread_reviewer = unread_reviewer or whatsapp_unread_reviewer
        self._registered = False

    def register_capabilities(self) -> None:
        """Register all WhatsApp execution handlers into the canonical capability registry."""
        if self._registered:
            return

        logger.info("Registering WhatsApp Node execution capabilities...")

        async def handle_call(req: ActionRequest) -> Dict[str, Any]:
            raw_target = str(req.arguments["target"])
            objective = str(req.arguments["objective"])
            mode = req.arguments.get("conversation_mode")
            duration_ms = req.arguments.get("duration_ms")
            int_duration: Optional[int] = int(duration_ms) if duration_ms is not None else None

            # Resolve contact name to phone number if possible
            resolved = self.resolver.resolve(raw_target)
            target = resolved.phone if (resolved.resolved and resolved.phone) else raw_target

            # Dispatch real-time telephony state transitions to Telegram live call card
            async def _on_telephony_event(state: str) -> None:
                try:
                    from jarvis.telegram.service import telegram_service

                    await telegram_service.send_call_status(
                        target=target,
                        call_state=state,
                        objective=objective,
                    )
                except Exception as tg_err:
                    logger.debug(f"Telegram call status hook error: {tg_err}")

            call_result = await self.caller.place_call(
                target=target,
                objective=objective,
                conversation_mode=str(mode) if mode else None,
                duration_ms=int_duration,
                on_status=_on_telephony_event,
            )

            # Broadcast final call summary update to the same editable card
            try:
                from jarvis.telegram.service import telegram_service

                final_state = "COMPLETED" if call_result.success else "FAILED"
                await telegram_service.send_call_status(
                    target=target,
                    call_state=final_state,
                    duration_seconds=int(call_result.duration_seconds),
                    objective=objective,
                    reply_preview=call_result.recipient_reply,
                    summary=call_result.summary_text,
                )
            except Exception as tg_err:
                logger.debug(f"Telegram call status final hook error: {tg_err}")

            target_display = call_result.target_number or target
            outcome_msg = (
                f"Call to {target_display} completed in {call_result.duration_seconds}s. "
                f"Recipient's reply: '{call_result.recipient_reply or 'No verbal reply detected'}'. "
                f"Summary: {call_result.summary_text or 'Call completed.'}"
            )

            return {
                "success": call_result.success,
                "call_completed": True,
                "call_id": call_result.call_id,
                "target_number": call_result.target_number,
                "duration_seconds": call_result.duration_seconds,
                "recipient_reply": call_result.recipient_reply,
                "summary": call_result.summary_text,
                "outcome_message": outcome_msg,
                "transcript_path": call_result.transcript_path,
                "error": call_result.error,
            }

        async def handle_status(req: ActionRequest) -> Dict[str, Any]:
            stats = self.store.get_stats()
            diag: Dict[str, Any] = {}
            try:
                diag = await self.caller.get_diagnostics()
            except Exception:
                pass
            return {
                "available": self.caller.is_available,
                "authenticated": self.caller.has_persisted_session,
                "auth_dir": str(self.caller._auth_dir),
                "extension_dir": str(self.caller._extension_dir),
                "database_path": str(self.store.db_path),
                "default_country_code": settings.WHATSAPP_DEFAULT_COUNTRY_CODE,
                "conversation_mode": settings.WHATSAPP_CONVERSATION_MODE,
                "enabled": settings.WHATSAPP_VOIP_ENABLED,
                "connection_state": diag.get("connection_state", "disconnected"),
                "contacts_count": stats.get("contacts", 0),
                "chats_count": stats.get("chats", 0),
                "messages_count": stats.get("messages", 0),
                "unread_chats_count": stats.get("unread_chats", 0),
                "unread_messages_count": stats.get("unread_messages", 0),
                "last_sync_event": diag.get("last_sync_event"),
                "last_message_event": diag.get("last_message_event"),
            }

        async def handle_history(req: ActionRequest) -> Dict[str, Any]:
            limit = int(req.arguments.get("limit", 5))
            query = req.arguments.get("query")
            if query:
                records = self.caller.search_call_history(str(query))
            else:
                records = self.caller.get_call_history(limit=limit)

            latest = records[0] if records else None
            return {
                "count": len(records),
                "latest_call": latest,
                "latest_recipient_reply": latest.get("recipientReply") if latest else None,
                "latest_summary": latest.get("summary") if latest else None,
                "calls": records,
            }

        async def handle_login(req: ActionRequest) -> Dict[str, Any]:
            force_refresh = bool(req.arguments.get("force_refresh", False))
            wait_for_scan = bool(req.arguments.get("wait_for_scan", False))
            return await self.caller.login(force_refresh=force_refresh, wait_for_scan=wait_for_scan)

        async def handle_logout(req: ActionRequest) -> Dict[str, Any]:
            return self.caller.logout()

        async def handle_sync(req: ActionRequest) -> Dict[str, Any]:
            full = bool(req.arguments.get("full", False))
            res = await self.caller.sync(full=full)
            stats = self.store.get_stats()
            return {
                "success": res.get("success", False),
                "error": res.get("error"),
                "store_stats": stats,
                "output": res.get("output"),
            }

        async def handle_contacts_search(req: ActionRequest) -> Dict[str, Any]:
            query = str(req.arguments.get("query", ""))
            limit = int(req.arguments.get("limit", 20))
            contacts = self.store.search_contacts(query=query, limit=limit)
            return {
                "query": query,
                "count": len(contacts),
                "contacts": [
                    {
                        "jid": c.jid,
                        "phone": c.phone,
                        "display_name": c.display_name,
                        "full_name": c.full_name,
                        "push_name": c.push_name,
                        "alias": c.alias,
                        "business_name": c.business_name,
                        "tags": c.tags,
                    }
                    for c in contacts
                ],
            }

        async def handle_contacts_get(req: ActionRequest) -> Dict[str, Any]:
            query = str(
                req.arguments.get("query")
                or req.arguments.get("jid")
                or req.arguments.get("phone")
                or ""
            )
            res = self.resolver.resolve(query)
            if res.resolved and res.candidate:
                c = res.candidate
                return {
                    "found": True,
                    "resolved": True,
                    "contact": {
                        "jid": c.jid,
                        "phone": c.phone,
                        "display_name": c.display_name,
                        "full_name": c.full_name,
                        "push_name": c.push_name,
                        "alias": c.alias,
                        "business_name": c.business_name,
                        "tags": c.tags,
                    },
                }
            elif res.ambiguous:
                return {
                    "found": True,
                    "resolved": False,
                    "ambiguous": True,
                    "disambiguation_prompt": res.disambiguation_prompt,
                    "candidates": [
                        {
                            "jid": cand.jid,
                            "phone": cand.phone,
                            "display_name": cand.display_name,
                            "alias": cand.alias,
                        }
                        for cand in res.candidates
                    ],
                }
            return {
                "found": False,
                "resolved": False,
                "error": res.error or f"Contact '{query}' not found.",
            }

        async def handle_contacts_check(req: ActionRequest) -> Dict[str, Any]:
            phone = str(req.arguments.get("phone", ""))
            return await self.caller.check_number(phone=phone)

        async def handle_contacts_refresh(req: ActionRequest) -> Dict[str, Any]:
            full = bool(req.arguments.get("full", False))
            res = await self.caller.sync(full=full)
            stats = self.store.get_stats()
            return {
                "success": res.get("success", False),
                "contacts_count": stats.get("contacts", 0),
                "stats": stats,
            }

        async def handle_contacts_alias(req: ActionRequest) -> Dict[str, Any]:
            jid = str(req.arguments.get("jid", ""))
            alias = str(req.arguments.get("alias", ""))
            action = str(req.arguments.get("action", "add")).lower()
            if action == "remove":
                self.store.remove_alias(alias)
                return {"success": True, "action": "removed", "alias": alias}
            else:
                if "@" not in jid:
                    res = self.resolver.resolve(jid)
                    if res.resolved and res.canonical_jid:
                        jid = res.canonical_jid
                    else:
                        return {"success": False, "error": f"Cannot resolve '{jid}' to JID"}
                self.store.set_alias(jid=jid, alias=alias)
                return {"success": True, "action": "set", "jid": jid, "alias": alias}

        async def handle_contacts_tags(req: ActionRequest) -> Dict[str, Any]:
            jid = str(req.arguments.get("jid", ""))
            tag = str(req.arguments.get("tag", ""))
            action = str(req.arguments.get("action", "add")).lower()
            if "@" not in jid:
                res = self.resolver.resolve(jid)
                if res.resolved and res.canonical_jid:
                    jid = res.canonical_jid
                else:
                    return {"success": False, "error": f"Cannot resolve '{jid}' to JID"}
            if action == "remove":
                self.store.remove_tag(jid=jid, tag=tag)
                return {"success": True, "action": "removed", "jid": jid, "tag": tag}
            else:
                self.store.add_tag(jid=jid, tag=tag)
                return {"success": True, "action": "added", "jid": jid, "tag": tag}

        async def handle_chats_list(req: ActionRequest) -> Dict[str, Any]:
            limit = int(req.arguments.get("limit", 50))
            archived = bool(req.arguments.get("archived", False))
            unread_only = bool(req.arguments.get("unread_only", False))
            if unread_only:
                chats = self.store.get_unread_chats()
            else:
                chats = self.store.list_chats(limit=limit, include_archived=archived)
            return {
                "count": len(chats),
                "chats": [
                    {
                        "jid": c.jid,
                        "kind": c.kind,
                        "name": c.name,
                        "last_message_ts": c.last_message_ts,
                        "unread": c.unread,
                        "unread_count": c.unread_count,
                        "archived": c.archived,
                    }
                    for c in chats[:limit]
                ],
            }

        async def handle_chats_get(req: ActionRequest) -> Dict[str, Any]:
            jid = str(req.arguments.get("jid", ""))
            if "@" not in jid:
                res = self.resolver.resolve(jid)
                if res.resolved and res.canonical_jid:
                    jid = res.canonical_jid
            chat = self.store.get_chat(jid)
            if not chat:
                return {"found": False, "jid": jid}
            msgs = self.store.list_messages(chat_jid=jid, limit=10)
            return {
                "found": True,
                "chat": {
                    "jid": chat.jid,
                    "kind": chat.kind,
                    "name": chat.name,
                    "last_message_ts": chat.last_message_ts,
                    "unread_count": chat.unread_count,
                },
                "recent_messages_count": len(msgs),
            }

        async def handle_chats_unread(req: ActionRequest) -> Dict[str, Any]:
            limit = int(req.arguments.get("limit", 20))
            chats = self.store.get_unread_chats()
            return {
                "unread_chats_count": len(chats[:limit]),
                "chats": [
                    {
                        "jid": c.jid,
                        "kind": c.kind,
                        "name": c.name,
                        "unread_count": c.unread_count,
                        "last_message_ts": c.last_message_ts,
                    }
                    for c in chats[:limit]
                ],
            }

        async def handle_messages_list(req: ActionRequest) -> Dict[str, Any]:
            chat_jid = str(req.arguments.get("chat_jid", ""))
            if "@" not in chat_jid:
                res = self.resolver.resolve(chat_jid)
                if res.resolved and res.canonical_jid:
                    chat_jid = res.canonical_jid
            limit = int(req.arguments.get("limit", 50))
            before = req.arguments.get("before")
            before_ts = int(before) if before is not None else None
            messages = self.store.list_messages(
                chat_jid=chat_jid, limit=limit, before_timestamp=before_ts
            )
            return {
                "chat_jid": chat_jid,
                "count": len(messages),
                "messages": [
                    {
                        "msg_id": m.msg_id,
                        "ts": m.ts,
                        "from_me": bool(m.from_me),
                        "sender_jid": m.sender_jid,
                        "sender_name": m.sender_name,
                        "text": m.text,
                        "media_type": m.media_type,
                    }
                    for m in messages
                ],
            }

        async def handle_messages_search(req: ActionRequest) -> Dict[str, Any]:
            query = str(req.arguments.get("query", ""))
            chat_jid = req.arguments.get("chat_jid")
            str_chat_jid = str(chat_jid) if chat_jid else None
            limit = int(req.arguments.get("limit", 20))
            messages = self.store.search_messages(query=query, chat_jid=str_chat_jid, limit=limit)
            return {
                "query": query,
                "chat_jid": str_chat_jid,
                "count": len(messages),
                "messages": [
                    {
                        "chat_jid": m.chat_jid,
                        "msg_id": m.msg_id,
                        "ts": m.ts,
                        "from_me": bool(m.from_me),
                        "sender_name": m.sender_name,
                        "text": m.text,
                    }
                    for m in messages
                ],
            }

        async def handle_messages_context(req: ActionRequest) -> Dict[str, Any]:
            chat_jid = str(req.arguments.get("chat_jid", ""))
            msg_id = str(req.arguments.get("message_id", ""))
            radius = int(req.arguments.get("radius", 5))
            ctx_data = self.store.get_message_context(
                chat_jid=chat_jid, msg_id=msg_id, radius=radius
            )
            messages = ctx_data.get("all_chronological", [])
            return {
                "chat_jid": chat_jid,
                "message_id": msg_id,
                "count": len(messages),
                "messages": [
                    {
                        "msg_id": m.msg_id,
                        "ts": m.ts,
                        "from_me": bool(m.from_me),
                        "sender_name": m.sender_name,
                        "text": m.text,
                    }
                    for m in messages
                ],
            }

        async def handle_send_text(req: ActionRequest) -> Dict[str, Any]:
            recipient = str(req.arguments.get("recipient", ""))
            message = str(req.arguments.get("message", ""))
            res = self.resolver.resolve(recipient)
            if not res.resolved or not res.canonical_jid:
                if res.ambiguous:
                    return {
                        "success": False,
                        "error": "AMBIGUOUS_RECIPIENT",
                        "disambiguation_prompt": res.disambiguation_prompt,
                        "candidates": [
                            {
                                "jid": cand.jid,
                                "phone": cand.phone,
                                "display_name": cand.display_name,
                                "alias": cand.alias,
                            }
                            for cand in res.candidates
                        ],
                    }
                return {
                    "success": False,
                    "error": res.error or f"Recipient '{recipient}' could not be resolved",
                }
            result = await self.caller.send_text(recipient=res.canonical_jid, message=message)
            if result.get("success") or result.get("sent"):
                self.store.mark_chat_read(res.canonical_jid)
            return result

        async def handle_send_file(req: ActionRequest) -> Dict[str, Any]:
            recipient = str(req.arguments.get("recipient", ""))
            file_path = str(req.arguments.get("file_path", ""))
            caption = req.arguments.get("caption")
            str_caption = str(caption) if caption else None
            res = self.resolver.resolve(recipient)
            if not res.resolved or not res.canonical_jid:
                if res.ambiguous:
                    return {
                        "success": False,
                        "error": "AMBIGUOUS_RECIPIENT",
                        "disambiguation_prompt": res.disambiguation_prompt,
                    }
                return {
                    "success": False,
                    "error": res.error or f"Recipient '{recipient}' could not be resolved",
                }
            result = await self.caller.send_file(
                recipient=res.canonical_jid, file_path=file_path, caption=str_caption
            )
            if result.get("success") or result.get("sent"):
                self.store.mark_chat_read(res.canonical_jid)
            return result

        async def handle_send_voice(req: ActionRequest) -> Dict[str, Any]:
            recipient = str(req.arguments.get("recipient", ""))
            audio_path = str(req.arguments.get("audio_path", ""))
            res = self.resolver.resolve(recipient)
            if not res.resolved or not res.canonical_jid:
                if res.ambiguous:
                    return {
                        "success": False,
                        "error": "AMBIGUOUS_RECIPIENT",
                        "disambiguation_prompt": res.disambiguation_prompt,
                    }
                return {
                    "success": False,
                    "error": res.error or f"Recipient '{recipient}' could not be resolved",
                }
            result = await self.caller.send_voice(
                recipient=res.canonical_jid, audio_path=audio_path
            )
            if result.get("success") or result.get("sent"):
                self.store.mark_chat_read(res.canonical_jid)
            return result

        async def handle_send_reaction(req: ActionRequest) -> Dict[str, Any]:
            chat_jid = str(req.arguments.get("chat_jid", ""))
            message_id = str(req.arguments.get("message_id", ""))
            reaction = str(req.arguments.get("reaction", ""))
            if "@" not in chat_jid:
                res = self.resolver.resolve(chat_jid)
                if res.resolved and res.canonical_jid:
                    chat_jid = res.canonical_jid
            return await self.caller.send_reaction(
                chat_jid=chat_jid, message_id=message_id, reaction=reaction
            )

        async def handle_send_reply(req: ActionRequest) -> Dict[str, Any]:
            chat_jid = str(req.arguments.get("chat_jid", ""))
            message = str(req.arguments.get("message", ""))
            if "@" not in chat_jid:
                res = self.resolver.resolve(chat_jid)
                if res.resolved and res.canonical_jid:
                    chat_jid = res.canonical_jid
            result = await self.caller.send_text(recipient=chat_jid, message=message)
            if result.get("success") or result.get("sent"):
                self.store.mark_chat_read(chat_jid)
            return result

        async def handle_media_download(req: ActionRequest) -> Dict[str, Any]:
            chat_jid = str(req.arguments.get("chat_jid", ""))
            msg_id = str(req.arguments.get("message_id", "") or req.arguments.get("msg_id", ""))
            target_path = req.arguments.get("target_path") or req.arguments.get("file_path")
            str_target_path = str(target_path) if target_path else None
            if "@" not in chat_jid:
                res = self.resolver.resolve(chat_jid)
                if res.resolved and res.canonical_jid:
                    chat_jid = res.canonical_jid
            return await self.caller.download_media(
                chat_jid=chat_jid, msg_id=msg_id, target_path=str_target_path
            )

        async def handle_review_unread(req: ActionRequest) -> Dict[str, Any]:
            limit_chats = int(req.arguments.get("limit_chats", 15))
            context_limit = int(req.arguments.get("context_limit", 5))
            return await self.unread_reviewer.review_unread_chats(
                max_chats=limit_chats, context_limit=context_limit
            )

        capability_registry.register(CAPABILITY_WHATSAPP_CALL, handle_call)
        capability_registry.register(CAPABILITY_WHATSAPP_STATUS, handle_status)
        capability_registry.register(CAPABILITY_WHATSAPP_HISTORY, handle_history)
        capability_registry.register(CAPABILITY_WHATSAPP_LOGIN, handle_login)
        capability_registry.register(CAPABILITY_WHATSAPP_LOGOUT, handle_logout)
        capability_registry.register(CAPABILITY_WHATSAPP_SYNC, handle_sync)
        capability_registry.register(CAPABILITY_WHATSAPP_CONTACTS_SEARCH, handle_contacts_search)
        capability_registry.register(CAPABILITY_WHATSAPP_CONTACTS_GET, handle_contacts_get)
        capability_registry.register(CAPABILITY_WHATSAPP_CONTACTS_CHECK, handle_contacts_check)
        capability_registry.register(CAPABILITY_WHATSAPP_CONTACTS_REFRESH, handle_contacts_refresh)
        capability_registry.register(CAPABILITY_WHATSAPP_CONTACTS_ALIAS, handle_contacts_alias)
        capability_registry.register(CAPABILITY_WHATSAPP_CONTACTS_TAGS, handle_contacts_tags)
        capability_registry.register(CAPABILITY_WHATSAPP_CHATS_LIST, handle_chats_list)
        capability_registry.register(CAPABILITY_WHATSAPP_CHATS_GET, handle_chats_get)
        capability_registry.register(CAPABILITY_WHATSAPP_CHATS_UNREAD, handle_chats_unread)
        capability_registry.register(CAPABILITY_WHATSAPP_MESSAGES_LIST, handle_messages_list)
        capability_registry.register(CAPABILITY_WHATSAPP_MESSAGES_SEARCH, handle_messages_search)
        capability_registry.register(CAPABILITY_WHATSAPP_MESSAGES_CONTEXT, handle_messages_context)
        capability_registry.register(CAPABILITY_WHATSAPP_SEND_TEXT, handle_send_text)
        capability_registry.register(CAPABILITY_WHATSAPP_SEND_FILE, handle_send_file)
        capability_registry.register(CAPABILITY_WHATSAPP_SEND_VOICE, handle_send_voice)
        capability_registry.register(CAPABILITY_WHATSAPP_SEND_REACTION, handle_send_reaction)
        capability_registry.register(CAPABILITY_WHATSAPP_SEND_REPLY, handle_send_reply)
        capability_registry.register(CAPABILITY_WHATSAPP_REVIEW_UNREAD, handle_review_unread)
        capability_registry.register(CAPABILITY_WHATSAPP_MEDIA_DOWNLOAD, handle_media_download)

        self._registered = True
        logger.info("WhatsApp Node capabilities successfully registered.")


# Global singleton instance
whatsapp_node = WhatsAppNode()
