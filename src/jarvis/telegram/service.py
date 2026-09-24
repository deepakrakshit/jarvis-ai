"""Native Telegram Remote Control Service for JARVIS Personal AI Operating System.

Provides bidirectional remote command execution, single-card task tracking,
privileged action authorization, and multimedia file transfer.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, Message, User

from jarvis.config import settings
from jarvis.contracts.task import TaskState
from jarvis.core.control_plane import control_plane
from jarvis.execution.windows.desktop import capture_screenshot
from jarvis.policy.approvals import approval_manager
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telegram.authorizer import TelegramAuthorizer, telegram_authorizer
from jarvis.telegram.cards import CardManager, card_manager
from jarvis.telegram.formatters import (
    format_approval_request_card,
    format_call_status_card,
    format_system_status,
)
from jarvis.telegram.keyboards import create_approval_keyboard
from jarvis.telegram.live_session import TelegramLiveSessionManager, telegram_live_manager
from jarvis.telegram.pairing import TelegramPairingManager, telegram_pairing_manager
from jarvis.telemetry import logger


class TelegramService:
    """Core daemon managing native Telegram long-polling and JARVIS integration."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        database: Optional[DatabaseEngine] = None,
        pairing_mgr: Optional[TelegramPairingManager] = None,
        authorizer: Optional[TelegramAuthorizer] = None,
        card_mgr: Optional[CardManager] = None,
        live_mgr: Optional[TelegramLiveSessionManager] = None,
    ) -> None:
        self._token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self._db = database or db
        self._pairing_mgr = pairing_mgr or telegram_pairing_manager
        self._authorizer = authorizer or telegram_authorizer
        self._card_mgr = card_mgr or card_manager
        self._live_mgr = live_mgr or telegram_live_manager

        self._bot: Optional[Bot] = None
        self._dp: Optional[Dispatcher] = None
        self._bot_info: Optional[User] = None
        self._polling_task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._operator_call_cards: Dict[int, int] = {}  # chat_id -> message_id

    @property
    def is_running(self) -> bool:
        """Check whether the Telegram service is actively polling."""
        return self._running

    @property
    def bot_username(self) -> Optional[str]:
        """Return the Telegram bot username if initialized."""
        return self._bot_info.username if self._bot_info else None

    @property
    def bot_id(self) -> Optional[int]:
        """Return the Telegram bot numeric ID if initialized."""
        return self._bot_info.id if self._bot_info else None

    async def start(self) -> bool:
        """Initialize the bot and launch the background long-polling loop."""
        if self._running:
            logger.warning("TelegramService is already running.")
            return True

        if not self._token:
            logger.info("Telegram bot token not configured; Telegram service idle.")
            return False

        try:
            self._bot = Bot(
                token=self._token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            self._bot_info = await self._bot.get_me()
            logger.info(
                f"Connected to Telegram Bot API as @{self._bot_info.username} (ID: {self._bot_info.id})"
            )

            # Drop webhooks to guarantee long-polling exclusivity
            await self._bot.delete_webhook(drop_pending_updates=False)

            self._dp = Dispatcher()
            self._register_handlers(self._dp)

            self._running = True
            self._polling_task = asyncio.create_task(self._run_polling())

            # Verify if an operator is paired; log onboarding link if unconfigured
            has_operator = await self._authorizer.has_any_authorized_user()
            if not has_operator and self.bot_username:
                nonce, deep_link = await self._pairing_mgr.create_pairing_challenge(
                    bot_username=self.bot_username
                )
                logger.info(
                    f"No authorized Telegram operator detected. Pair device using: {deep_link}"
                )

            return True
        except Exception as err:
            logger.error(f"Failed to start TelegramService: {err}")
            self._running = False
            if self._bot:
                await self._bot.session.close()
                self._bot = None
            return False

    async def stop(self) -> None:
        """Gracefully stop polling and terminate network sessions."""
        if not self._running:
            return

        logger.info("Shutting down TelegramService...")
        self._running = False

        if self._dp:
            await self._dp.stop_polling()

        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass

        try:
            await self._live_mgr.close_all()
        except Exception as live_close_err:
            logger.debug(f"Error closing Telegram live sessions: {live_close_err}")

        if self._bot:
            await self._bot.session.close()
            self._bot = None

        logger.info("TelegramService shutdown complete.")

    async def _run_polling(self) -> None:
        """Execute long polling with retry backoff for network resilience."""
        backoff = 1.0
        while self._running and self._dp and self._bot:
            try:
                await self._dp.start_polling(self._bot, handle_signals=False)
                backoff = 1.0
            except asyncio.CancelledError:
                break
            except Exception as err:
                if not self._running:
                    break
                logger.warning(
                    f"Telegram polling interrupted: {err}. Retrying in {backoff:.1f}s..."
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    def _register_handlers(self, dp: Dispatcher) -> None:
        """Register all route, command, callback, and message handlers."""

        @dp.message(CommandStart())
        async def on_command_start(message: Message, command: CommandObject) -> None:
            if not message.from_user:
                return

            user_id = message.from_user.id
            username = message.from_user.username
            args = command.args

            callsign = getattr(settings, "USER_CALLSIGN", "Sir")

            if args:
                # Pairing attempt with challenge nonce
                nonce = args.strip()
                success = await self._pairing_mgr.verify_and_pair(
                    nonce=nonce, telegram_user_id=user_id, username=username
                )
                if success:
                    await message.answer(
                        "✅ <b>JARVIS PAIRED SUCCESSFULLY</b>\n\n"
                        f"Welcome, {callsign}. You are now authorized to remotely control JARVIS.\n\n"
                        "Speak to me naturally at any time, or use /help to inspect capabilities."
                    )
                else:
                    await message.answer(
                        "❌ <b>PAIRING FAILED</b>\n\n"
                        "Invalid or expired pairing code. Please generate a new pairing link from the JARVIS host."
                    )
                return

            # Bare /start command
            if await self._authorizer.is_authorized(user_id):
                await message.answer(
                    "🤖 <b>JARVIS ONLINE</b>\n\n"
                    f"Good day, {callsign}. All systems are fully operational on your PC.\n"
                    "How may I be of service?"
                )
            else:
                await message.answer(
                    "🔒 <b>ACCESS DENIED</b>\n\n"
                    "This JARVIS instance is private and requires cryptographic pairing.\n"
                    "If you are the owner, generate a pairing link using <code>jarvis telegram pair</code>."
                )

        @dp.message(Command("status"))
        async def on_command_status(message: Message) -> None:
            if not message.from_user or not await self._authorizer.is_authorized(
                message.from_user.id
            ):
                await self._send_unauthorized_reply(message)
                return

            op_count = len(self._db.list_telegram_authorized_users())
            status_text = format_system_status(
                telegram_online=True,
                operator_count=op_count,
                bot_username=self.bot_username,
            )
            await message.answer(status_text)

        @dp.message(Command("screenshot"))
        async def on_command_screenshot(message: Message) -> None:
            if not message.from_user or not await self._authorizer.is_authorized(
                message.from_user.id
            ):
                await self._send_unauthorized_reply(message)
                return

            status_msg = await message.answer("📸 <i>Capturing desktop screenshot...</i>")
            try:
                res = await asyncio.to_thread(capture_screenshot)
                path_str = res.get("artifact_path")
                if path_str and Path(path_str).exists():
                    photo_file = FSInputFile(path_str)
                    await message.answer_photo(
                        photo=photo_file,
                        caption="🖥️ <b>Desktop Display Capture</b>",
                    )
                    await status_msg.delete()
                else:
                    await status_msg.edit_text("❌ Screenshot capture returned an invalid path.")
            except Exception as err:
                logger.error(f"Failed to capture screenshot: {err}")
                await status_msg.edit_text(f"❌ Failed to capture screenshot: {err}")

        @dp.message(Command("unpair"))
        async def on_command_unpair(message: Message) -> None:
            if not message.from_user or not await self._authorizer.is_authorized(
                message.from_user.id
            ):
                await self._send_unauthorized_reply(message)
                return

            await self._authorizer.revoke_user(message.from_user.id)
            await message.answer(
                "🔒 <b>DEVICE UNPAIRED</b>\n\n"
                "Your operator credentials have been revoked. JARVIS will ignore future requests until re-paired."
            )

        @dp.message(Command("help"))
        async def on_command_help(message: Message) -> None:
            if not message.from_user or not await self._authorizer.is_authorized(
                message.from_user.id
            ):
                await self._send_unauthorized_reply(message)
                return

            help_text = (
                "<b>JARVIS REMOTE CONTROL ASSISTANT</b>\n\n"
                "• <b>Natural Language:</b> Send any instruction to execute directly on the host.\n"
                "• <b>/screenshot:</b> Capture and receive current desktop screen.\n"
                "• <b>/status:</b> Check daemon and connectivity status.\n"
                "• <b>/unpair:</b> Deauthorize this device.\n"
                "• <b>Approvals:</b> Interactive inline authorization buttons for privileged actions.\n"
                "• <b>Files:</b> Send documents or photos to upload to JARVIS workspace."
            )
            await message.answer(help_text)

        @dp.callback_query()
        async def on_callback_query(query: CallbackQuery) -> None:
            if not query.from_user or not await self._authorizer.is_authorized(query.from_user.id):
                await query.answer("Unauthorized.", show_alert=True)
                return

            data = query.data or ""

            # Approval callback: appr:a:<token> or appr:d:<token>
            if data.startswith("appr:a:") or data.startswith("appr:d:"):
                is_approval = data.startswith("appr:a:")
                opaque_token = data.split(":", 2)[2]

                ticket = self._db.get_telegram_approval_ticket_by_opaque(opaque_token)
                if not ticket or ticket.get("status") != "PENDING":
                    await query.answer(
                        "This approval request is no longer pending or has expired.",
                        show_alert=True,
                    )
                    return

                ticket_id = ticket["ticket_id"]
                capability = ticket.get("capability", "privileged_operation")
                new_status = "APPROVED" if is_approval else "DENIED"

                # Update database ticket
                self._db.resolve_telegram_approval_ticket(ticket_id, new_status)

                # Resolve with in-memory approval manager
                approval_manager.resolve(
                    approval_id=ticket_id,
                    approved=is_approval,
                    operator_identity=f"telegram:{query.from_user.id}",
                )

                # Edit message to reflect decision
                verdict_icon = "✅" if is_approval else "❌"
                verdict_title = "ACTION AUTHORIZED" if is_approval else "ACTION DENIED"
                desc = (
                    "Execution permitted by operator."
                    if is_approval
                    else "Execution rejected by operator."
                )

                card_text = (
                    f"{verdict_icon} <b>{verdict_title}</b>\n"
                    f"<b>Ticket:</b> <code>{ticket_id}</code>\n"
                    f"<b>Capability:</b> <code>{capability}</code>\n\n"
                    f"<i>{desc}</i>"
                )

                if query.message:
                    await self._card_mgr.edit_card(
                        bot=self._bot,  # type: ignore[arg-type]
                        chat_id=query.message.chat.id,
                        message_id=query.message.message_id,
                        text=card_text,
                        reply_markup=None,
                        force=True,
                    )

                ack_text = "Action authorized." if is_approval else "Action rejected."
                await query.answer(ack_text)
                return

            if data.startswith("task:c:"):
                await query.answer("Cancellation request submitted.")
                return

            await query.answer()

        @dp.message(F.text)
        async def on_text_message(message: Message) -> None:
            if not message.from_user or not message.text:
                return

            if not await self._authorizer.is_authorized(message.from_user.id):
                await self._send_unauthorized_reply(message)
                return

            user_text = message.text.strip()
            chat_id = message.chat.id
            user_id = message.from_user.id
            callsign = getattr(settings, "USER_CALLSIGN", "Sir")

            session_id = self._db.get_or_create_telegram_chat_session(
                chat_id=chat_id, telegram_user_id=user_id
            )
            telegram_task_id = f"TG-{secrets.token_hex(4).upper()}"

            # Show native typing indicator in Telegram
            if self._bot:
                try:
                    await self._bot.send_chat_action(chat_id=chat_id, action="typing")
                except Exception:
                    pass

            # Primary Cognitive Engine: Dedicated Gemini 3.8 Live Session
            has_live_key = bool(
                getattr(settings, "TELEGRAM_GEMINI_API_KEY", None)
                or getattr(settings, "GEMINI_API_KEY", None)
            )
            if has_live_key:
                try:
                    logger.info(
                        f"Routing intent '{user_text}' through dedicated Telegram Gemini Live session"
                    )
                    live_res = await self._live_mgr.process_message(
                        chat_id=chat_id, user_text=user_text
                    )
                    if not live_res.error and (live_res.text or live_res.artifacts):
                        # Send any generated artifact images first (e.g. desktop screenshot)
                        for art_path in live_res.artifacts:
                            if art_path.exists():
                                try:
                                    await message.answer_photo(
                                        photo=FSInputFile(str(art_path)),
                                        caption=f"🖥️ Desktop Capture ({art_path.name})",
                                    )
                                except Exception as photo_err:
                                    logger.debug(f"Could not send artifact photo: {photo_err}")

                        # Deliver the model conversational response
                        if live_res.text:
                            try:
                                await message.answer(live_res.text)
                            except Exception:
                                await message.answer(live_res.text, parse_mode=None)
                        return
                    elif live_res.error:
                        logger.warning(
                            f"Telegram Live turn failed: {live_res.error}. Falling back to Control Plane."
                        )
                except Exception as live_err:
                    logger.warning(
                        f"Error in Telegram Live session processing: {live_err}. Falling back to Control Plane."
                    )

            # Fallback Engine: Authoritative JARVIS Control Plane
            try:
                task = await control_plane.submit_intent(
                    raw_intent=user_text,
                    session_id=session_id,
                )

                # Persist correlation mapping in DB
                self._db.save_telegram_task_mapping(
                    telegram_task_id=telegram_task_id,
                    jarvis_task_id=task.task_id,
                    chat_id=chat_id,
                    status_message_id=0,
                    status=task.state.value,
                )
                self._db.update_telegram_task_mapping_status(task.task_id, task.state.value)

                # If execution failed, provide clean, friendly explanation
                if task.state == TaskState.FAILED:
                    err = task.error_message or "Execution failed during processing."
                    await message.answer(f"⚠️ <b>Notice:</b> {err}")
                    return

                # If requires approval, inform user
                if task.state == TaskState.NEEDS_APPROVAL:
                    await message.answer(
                        "⚠️ <b>Approval Required:</b> A privileged action awaits your authorization."
                    )
                    return

                # Deliver natural, conversational response directly as JARVIS
                reply_text = task.result_summary or f"Action completed, {callsign}."
                try:
                    await message.answer(reply_text)
                except Exception:
                    await message.answer(reply_text, parse_mode=None)

                # If the action produced a screenshot or image artifact, send it as a photo
                img_match = re.search(r"([A-Za-z]:[\\/][^\s\n<>]+\.(?:png|jpg|jpeg))", reply_text)
                if img_match:
                    img_path = Path(img_match.group(1))
                    if img_path.exists():
                        try:
                            await message.answer_photo(
                                photo=FSInputFile(str(img_path)),
                                caption=f"🖥️ Desktop Capture ({img_path.name})",
                            )
                        except Exception as photo_err:
                            logger.debug(f"Could not send artifact photo: {photo_err}")

            except Exception as err:
                logger.error(f"Error executing task for intent '{user_text}': {err}")
                await message.answer(f"⚠️ <b>Error:</b> Could not execute instruction: {err}")

        @dp.message(F.document | F.photo)
        async def on_media_message(message: Message) -> None:
            if not message.from_user or not await self._authorizer.is_authorized(
                message.from_user.id
            ):
                await self._send_unauthorized_reply(message)
                return

            if not self._bot:
                return

            max_size = getattr(settings, "TELEGRAM_MAX_UPLOAD_BYTES", 52428800)
            file_id: Optional[str] = None
            filename: Optional[str] = None
            file_size: Optional[int] = None

            if message.document:
                file_id = message.document.file_id
                filename = message.document.file_name or "document.bin"
                file_size = message.document.file_size
            elif message.photo:
                largest = message.photo[-1]
                file_id = largest.file_id
                filename = f"photo_{secrets.token_hex(4)}.jpg"
                file_size = largest.file_size

            if not file_id:
                return

            if file_size and file_size > max_size:
                await message.answer(
                    f"⚠️ <b>FILE TOO LARGE</b>\n\nFile size ({file_size} bytes) exceeds limit ({max_size} bytes)."
                )
                return

            save_dir = settings.ARTIFACTS_DIR / "telegram_inbound"
            save_dir.mkdir(parents=True, exist_ok=True)
            safe_filename = Path(filename or "file.bin").name
            dest_path = save_dir / f"{secrets.token_hex(4)}_{safe_filename}"

            status_msg = await message.answer("📥 <i>Receiving file from Telegram...</i>")
            try:
                tg_file = await self._bot.get_file(file_id)
                if tg_file.file_path:
                    await self._bot.download_file(tg_file.file_path, destination=dest_path)
                    await status_msg.edit_text(
                        f"✅ <b>FILE RECEIVED</b>\n\n"
                        f"Saved to: <code>{dest_path.name}</code> ({dest_path.stat().st_size} bytes)\n"
                        f"Caption/Intent: <i>{message.caption or 'No caption'}</i>"
                    )
            except Exception as err:
                logger.error(f"Failed to download file from Telegram: {err}")
                await status_msg.edit_text(f"❌ Failed to receive file: {err}")

    async def _send_unauthorized_reply(self, message: Message) -> None:
        """Send standardized fail-closed denial to unauthorized users."""
        await message.answer(
            "🔒 <b>ACCESS DENIED</b>\n\n"
            "This JARVIS instance is private and requires cryptographic pairing.\n"
            "If you are the owner, pair this device using <code>jarvis telegram pair</code>."
        )

    async def send_approval_request(
        self,
        ticket_id: str,
        capability: str,
        risk_tier: str,
        target_resource: str,
        risk_summary: str,
        task_id: str = "",
        arguments_summary: Optional[Dict[str, Any]] = None,
    ) -> List[int]:
        """Proactively dispatch an interactive approval ticket to all authorized operators.

        Returns list of message IDs dispatched.
        """
        if not self._bot or not self._running:
            return []

        operators = self._db.list_telegram_authorized_users()
        if not operators:
            return []

        opaque_token = secrets.token_urlsafe(16)
        card_text = format_approval_request_card(
            ticket_id=ticket_id,
            capability=capability,
            risk_tier=risk_tier,
            target_resource=target_resource,
            risk_summary=risk_summary,
            arguments_summary=arguments_summary,
        )
        keyboard = create_approval_keyboard(opaque_token)

        sent_ids: List[int] = []
        for op in operators:
            uid = op["telegram_user_id"]
            try:
                msg = await self._bot.send_message(
                    chat_id=uid,
                    text=card_text,
                    reply_markup=keyboard,
                )
                sent_ids.append(msg.message_id)

                self._db.create_telegram_approval_ticket(
                    ticket_id=ticket_id,
                    opaque_token=opaque_token,
                    task_id=task_id,
                    telegram_user_id=uid,
                    chat_id=uid,
                    risk_tier=risk_tier,
                    capability=capability,
                    safe_display=card_text,
                    expires_at="",
                )
            except Exception as err:
                logger.warning(f"Failed to dispatch approval ticket to user {uid}: {err}")

        return sent_ids

    async def send_call_status(
        self,
        target: str,
        call_state: str,
        duration_seconds: Optional[int] = None,
        objective: Optional[str] = None,
        reply_preview: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> None:
        """Publish or update a live single editable VoIP status card for all operators."""
        if not self._bot or not self._running:
            return

        operators = self._db.list_telegram_authorized_users()
        if not operators:
            return

        card_text = format_call_status_card(
            target=target,
            call_state=call_state,
            duration_seconds=duration_seconds,
            objective=objective,
            reply_preview=reply_preview,
            summary=summary,
        )

        for op in operators:
            uid = op["telegram_user_id"]
            existing_msg_id = self._operator_call_cards.get(uid)

            if existing_msg_id:
                success = await self._card_mgr.edit_card(
                    bot=self._bot,
                    chat_id=uid,
                    message_id=existing_msg_id,
                    text=card_text,
                    force=True,
                )
                if not success:
                    # Message may have been deleted; recreate
                    try:
                        new_msg = await self._bot.send_message(chat_id=uid, text=card_text)
                        self._operator_call_cards[uid] = new_msg.message_id
                    except Exception as err:
                        logger.warning(f"Failed to send VoIP call card to user {uid}: {err}")
            else:
                try:
                    new_msg = await self._bot.send_message(chat_id=uid, text=card_text)
                    self._operator_call_cards[uid] = new_msg.message_id
                except Exception as err:
                    logger.warning(f"Failed to send VoIP call card to user {uid}: {err}")


# Global singleton instance
telegram_service = TelegramService()


def get_telegram_service() -> TelegramService:
    """Return the global Telegram service daemon instance."""
    return telegram_service
