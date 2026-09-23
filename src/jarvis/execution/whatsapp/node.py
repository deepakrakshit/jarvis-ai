"""WhatsApp Execution Node for JARVIS Action Broker.

Bridges canonical WhatsApp capabilities into the autonomous VoIP calling engine.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from jarvis.actions.registry import capability_registry
from jarvis.config import settings
from jarvis.contracts.action import ActionRequest
from jarvis.execution.whatsapp.voice_caller import (
    WhatsAppVoiceCaller,
    get_whatsapp_caller,
)
from jarvis.policy.firewall import (
    CAPABILITY_WHATSAPP_CALL,
    CAPABILITY_WHATSAPP_HISTORY,
    CAPABILITY_WHATSAPP_LOGIN,
    CAPABILITY_WHATSAPP_LOGOUT,
    CAPABILITY_WHATSAPP_STATUS,
)
from jarvis.telemetry import logger


class WhatsAppNode:
    """Execution node for autonomous WhatsApp VoIP calling operations."""

    def __init__(self, caller: Optional[WhatsAppVoiceCaller] = None) -> None:
        self.caller = caller or get_whatsapp_caller()
        self._registered = False

    def register_capabilities(self) -> None:
        """Register all WhatsApp execution handlers into the canonical capability registry."""
        if self._registered:
            return

        logger.info("Registering WhatsApp Node execution capabilities...")

        async def handle_call(req: ActionRequest) -> Dict[str, Any]:
            target = str(req.arguments["target"])
            objective = str(req.arguments["objective"])
            mode = req.arguments.get("conversation_mode")
            duration_ms = req.arguments.get("duration_ms")
            int_duration: Optional[int] = int(duration_ms) if duration_ms is not None else None

            call_result = await self.caller.place_call(
                target=target,
                objective=objective,
                conversation_mode=str(mode) if mode else None,
                duration_ms=int_duration,
            )

            outcome_msg = (
                f"Call to {call_result.target_number} completed in {call_result.duration_seconds}s. "
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
            return {
                "available": self.caller.is_available,
                "authenticated": self.caller.has_persisted_session,
                "auth_dir": str(self.caller._auth_dir),
                "extension_dir": str(self.caller._extension_dir),
                "default_country_code": settings.WHATSAPP_DEFAULT_COUNTRY_CODE,
                "conversation_mode": settings.WHATSAPP_CONVERSATION_MODE,
                "enabled": settings.WHATSAPP_VOIP_ENABLED,
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

        capability_registry.register(CAPABILITY_WHATSAPP_CALL, handle_call)
        capability_registry.register(CAPABILITY_WHATSAPP_STATUS, handle_status)
        capability_registry.register(CAPABILITY_WHATSAPP_HISTORY, handle_history)
        capability_registry.register(CAPABILITY_WHATSAPP_LOGIN, handle_login)
        capability_registry.register(CAPABILITY_WHATSAPP_LOGOUT, handle_logout)

        self._registered = True
        logger.info("WhatsApp Node capabilities successfully registered.")


# Global singleton instance
whatsapp_node = WhatsAppNode()
