"""Native Telegram Remote Control Integration Subsystem for JARVIS.

Enables secure operator communication, cryptographic pairing, task execution,
single-card status tracking, and privileged action approvals.
"""

from jarvis.telegram.authorizer import TelegramAuthorizer, telegram_authorizer
from jarvis.telegram.cards import CardManager, card_manager
from jarvis.telegram.formatters import (
    format_approval_request_card,
    format_call_status_card,
    format_system_status,
    format_task_status_card,
    redact_sensitive_data,
)
from jarvis.telegram.keyboards import (
    create_approval_keyboard,
    create_cancel_keyboard,
)
from jarvis.telegram.live_session import (
    TelegramLiveSession,
    TelegramLiveSessionManager,
    TelegramLiveTurnResult,
    telegram_live_manager,
)
from jarvis.telegram.pairing import TelegramPairingManager, telegram_pairing_manager
from jarvis.telegram.service import TelegramService, get_telegram_service, telegram_service

__all__ = [
    "TelegramLiveSession",
    "TelegramLiveSessionManager",
    "TelegramLiveTurnResult",
    "telegram_live_manager",
    "TelegramAuthorizer",
    "telegram_authorizer",
    "CardManager",
    "card_manager",
    "format_approval_request_card",
    "format_call_status_card",
    "format_system_status",
    "format_task_status_card",
    "redact_sensitive_data",
    "create_approval_keyboard",
    "create_cancel_keyboard",
    "TelegramPairingManager",
    "telegram_pairing_manager",
    "TelegramService",
    "get_telegram_service",
    "telegram_service",
]
