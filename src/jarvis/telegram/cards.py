"""Rate-limited single editable status card manager for Telegram chats."""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

from jarvis.config import settings
from jarvis.telemetry import logger


class CardManager:
    """Manages rate-limited edits to maintain a single live status card per task."""

    def __init__(self, edit_interval: Optional[float] = None) -> None:
        self.interval: float = float(
            edit_interval
            if edit_interval is not None
            else getattr(settings, "TELEGRAM_RATE_LIMIT_EDIT_INTERVAL", 1.0) or 1.0
        )
        self._last_edit_time: Dict[Tuple[int, int], float] = {}
        self._pending_tasks: Dict[Tuple[int, int], asyncio.Task[None]] = {}
        self._pending_payloads: Dict[
            Tuple[int, int], Tuple[str, Optional[InlineKeyboardMarkup]]
        ] = {}

    async def edit_card(
        self,
        bot: Bot,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
        force: bool = False,
    ) -> bool:
        """Edit an existing card with rate limiting to avoid Telegram 429 flood errors."""
        key = (chat_id, message_id)
        now = time.monotonic()
        last_time = self._last_edit_time.get(key, 0.0)
        elapsed = now - last_time

        if force or elapsed >= self.interval:
            if key in self._pending_tasks:
                self._pending_tasks[key].cancel()
                self._pending_tasks.pop(key, None)
            self._pending_payloads.pop(key, None)

            return await self._execute_edit(bot, chat_id, message_id, text, reply_markup)

        self._pending_payloads[key] = (text, reply_markup)
        if key not in self._pending_tasks or self._pending_tasks[key].done():
            delay = self.interval - elapsed
            self._pending_tasks[key] = asyncio.create_task(
                self._deferred_edit(bot, chat_id, message_id, delay)
            )

        return True

    async def _deferred_edit(self, bot: Bot, chat_id: int, message_id: int, delay: float) -> None:
        """Execute a scheduled edit after cooldown delay."""
        try:
            await asyncio.sleep(delay)
            key = (chat_id, message_id)
            payload = self._pending_payloads.pop(key, None)
            if payload:
                text, reply_markup = payload
                await self._execute_edit(bot, chat_id, message_id, text, reply_markup)
        except asyncio.CancelledError:
            pass
        except Exception as err:
            logger.warning(f"Deferred card edit error: {err}")
        finally:
            self._pending_tasks.pop((chat_id, message_id), None)

    async def _execute_edit(
        self,
        bot: Bot,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Optional[InlineKeyboardMarkup],
    ) -> bool:
        """Directly call Telegram API to edit message text."""
        key = (chat_id, message_id)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            self._last_edit_time[key] = time.monotonic()
            return True
        except TelegramBadRequest as err:
            if "message is not modified" in str(err).lower():
                return True
            logger.warning(f"TelegramBadRequest editing message {message_id}: {err}")
            return False
        except TelegramRetryAfter as retry:
            logger.warning(f"Telegram rate limit hit. Waiting {retry.retry_after}s...")
            await asyncio.sleep(retry.retry_after)
            return await self._execute_edit(bot, chat_id, message_id, text, reply_markup)
        except Exception as err:
            logger.error(f"Failed to edit card {message_id}: {err}")
            return False


# Global singleton instance
card_manager = CardManager()
