"""Inline keyboards and interactive buttons for native JARVIS Telegram interface."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def create_approval_keyboard(opaque_token: str) -> InlineKeyboardMarkup:
    """Build interactive inline keyboard for privileged approval tickets.

    Payload format:
    - Authorize: 'appr:a:<token>' (<= 20 bytes)
    - Deny: 'appr:d:<token>' (<= 20 bytes)
    Strictly conforms to Telegram's 64-byte callback_data ceiling.
    """
    token = opaque_token.strip()
    auth_data = f"appr:a:{token}"
    deny_data = f"appr:d:{token}"

    if len(auth_data.encode("utf-8")) > 64 or len(deny_data.encode("utf-8")) > 64:
        raise ValueError("Callback data exceeds Telegram 64-byte payload limit")

    buttons = [
        [
            InlineKeyboardButton(text="✅ AUTHORIZE", callback_data=auth_data),
            InlineKeyboardButton(text="❌ DENY", callback_data=deny_data),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_cancel_keyboard(telegram_task_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard allowing the operator to abort an active task."""
    cb_data = f"task:c:{telegram_task_id[:16]}"
    buttons = [[InlineKeyboardButton(text="🛑 CANCEL", callback_data=cb_data)]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
