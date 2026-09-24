"""Unit and Integration Tests for Native JARVIS Telegram Remote Control Subsystem.

Tests cryptographic pairing, operator authorization, 64-byte callback payloads,
single editable status cards, database persistence, and service routing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, Message, User

from jarvis.execution.windows.filesystem import is_safe_artifact_path, resolve_path
from jarvis.storage.database import DatabaseEngine
from jarvis.telegram.authorizer import TelegramAuthorizer
from jarvis.telegram.cards import CardManager
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
from jarvis.telegram.pairing import TelegramPairingManager
from jarvis.telegram.service import TelegramService

# ---------------------------------------------------------------------------
# Keyboards & 64-Byte Payload Constraints
# ---------------------------------------------------------------------------


def test_approval_keyboard_payload_ceiling() -> None:
    """Ensure approval keyboard callback data remains strictly under 64 bytes."""
    opaque_token = "tok_abc12345_XYZ"
    keyboard = create_approval_keyboard(opaque_token)
    assert len(keyboard.inline_keyboard) == 1
    buttons = keyboard.inline_keyboard[0]
    assert len(buttons) == 2

    auth_cb = buttons[0].callback_data
    deny_cb = buttons[1].callback_data
    assert auth_cb is not None and deny_cb is not None
    assert auth_cb.startswith("appr:a:")
    assert deny_cb.startswith("appr:d:")

    assert len(auth_cb.encode("utf-8")) <= 64
    assert len(deny_cb.encode("utf-8")) <= 64


def test_approval_keyboard_rejects_oversized_payload() -> None:
    """Ensure ValueError is raised if token exceeds Telegram's 64-byte payload limit."""
    giant_token = "x" * 70
    with pytest.raises(ValueError, match="exceeds Telegram 64-byte payload limit"):
        create_approval_keyboard(giant_token)


def test_cancel_keyboard_format() -> None:
    """Ensure task cancellation keyboard conforms to expected callback structure."""
    keyboard = create_cancel_keyboard("TG-TASK-1234567890")
    assert len(keyboard.inline_keyboard) == 1
    btn = keyboard.inline_keyboard[0][0]
    assert btn.callback_data is not None
    assert btn.callback_data.startswith("task:c:")
    assert len(btn.callback_data.encode("utf-8")) <= 64


# ---------------------------------------------------------------------------
# Formatters & Sensitive Data Masking
# ---------------------------------------------------------------------------


def test_redact_sensitive_tokens() -> None:
    """Verify sensitive bot tokens, API keys, and bearer tokens are masked."""
    raw = (
        "Bot token: 123456789:ABCdefGhI_jklMNOpqrSTUvwx-yz12345, "
        "Gemini key: AIzaSyD9x7a6B5c4E3f2G1h0J-kLmNoPqRsTuVw, "
        "Groq key: gsk_1234567890abcdefghijklmnopqrstuvwxyz12, "
        "Authorization: Bearer secret_access_token_12345678"
    )
    redacted = redact_sensitive_data(raw)
    assert "123456789:ABCdefGhI" not in redacted
    assert "[REDACTED_BOT_TOKEN]" in redacted
    assert "AIzaSyD9x7a6B5c4E" not in redacted
    assert "[REDACTED_API_KEY]" in redacted
    assert "gsk_1234567890abc" not in redacted
    assert "[REDACTED_GROQ_KEY]" in redacted
    assert "secret_access_token_12345678" not in redacted
    assert "Bearer [REDACTED_TOKEN]" in redacted


def test_format_task_status_card() -> None:
    """Verify single editable task status card formatting."""
    card = format_task_status_card(
        task_id="TASK-8F7A1B2C",
        status_indicator="⏳",
        title="TASK PROCESSING",
        detail="Executing command...",
        intent="open browser",
    )
    assert "⏳ <b>TASK PROCESSING</b>" in card
    assert "TASK-8F7A1B2C" in card
    assert "open browser" in card
    assert "Executing command..." in card


def test_format_approval_request_card() -> None:
    """Verify approval request card contains risk information and arguments."""
    card = format_approval_request_card(
        ticket_id="APPR-9988",
        capability="filesystem.delete",
        risk_tier="HIGH",
        target_resource="/path/to/important_file.txt",
        risk_summary="Permanent deletion of data file",
        arguments_summary={"path": "/path/to/important_file.txt"},
    )
    assert "APPROVAL REQUIRED" in card
    assert "APPR-9988" in card
    assert "filesystem.delete" in card
    assert "HIGH" in card
    assert "/path/to/important_file.txt" in card


def test_format_call_status_card() -> None:
    """Verify VoIP call status card transitions and content."""
    ringing_card = format_call_status_card(
        target="+1234567890",
        call_state="RINGING",
    )
    assert "RINGING" in ringing_card
    assert "+1234567890" in ringing_card

    completed_card = format_call_status_card(
        target="+1234567890",
        call_state="COMPLETED",
        duration_seconds=42,
        objective="Deliver test notice",
        reply_preview="Confirmed received",
        summary="Recipient acknowledged notification.",
    )
    assert "COMPLETED (42s)" in completed_card
    assert "Confirmed received" in completed_card
    assert "Recipient acknowledged notification." in completed_card


def test_format_system_status() -> None:
    """Verify system diagnostics card output."""
    status_card = format_system_status(
        telegram_online=True,
        operator_count=1,
        bot_username="JarvisAIBot",
    )
    assert "@JarvisAIBot" in status_card
    assert "1 operator" in status_card
    assert "Integrated" in status_card


# ---------------------------------------------------------------------------
# Rate-Limited Card Manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_card_manager_immediate_and_deferred_edits() -> None:
    """Verify CardManager executes first edit immediately and defers subsequent rapid edits."""
    mgr = CardManager(edit_interval=0.2)
    mock_bot = MagicMock()
    mock_bot.edit_message_text = AsyncMock(return_value=True)

    # First edit: immediate
    res1 = await mgr.edit_card(
        bot=mock_bot,
        chat_id=1001,
        message_id=501,
        text="Card 1",
    )
    assert res1 is True
    assert mock_bot.edit_message_text.call_count == 1

    # Second rapid edit: deferred
    res2 = await mgr.edit_card(
        bot=mock_bot,
        chat_id=1001,
        message_id=501,
        text="Card 2",
    )
    assert res2 is True
    # Still 1 call because second is throttled
    assert mock_bot.edit_message_text.call_count == 1

    # Wait for deferred edit to resolve
    await asyncio.sleep(0.3)
    assert mock_bot.edit_message_text.call_count == 2


@pytest.mark.asyncio
async def test_card_manager_force_bypass() -> None:
    """Verify force=True bypasses the rate limiter."""
    mgr = CardManager(edit_interval=10.0)
    mock_bot = MagicMock()
    mock_bot.edit_message_text = AsyncMock(return_value=True)

    await mgr.edit_card(mock_bot, 1001, 501, "Initial")
    assert mock_bot.edit_message_text.call_count == 1

    # Immediate forced edit
    await mgr.edit_card(mock_bot, 1001, 501, "Forced Final", force=True)
    assert mock_bot.edit_message_text.call_count == 2


# ---------------------------------------------------------------------------
# Cryptographic Pairing Manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pairing_challenge_creation_and_consumption(test_db: DatabaseEngine) -> None:
    """Verify cryptographic nonce generation, consumption, and single-use enforcement."""
    pairing_mgr = TelegramPairingManager(database=test_db)
    authorizer = TelegramAuthorizer(database=test_db)

    nonce, deep_link = await pairing_mgr.create_pairing_challenge(
        bot_username="JarvisBot", ttl_seconds=300
    )
    assert len(nonce) == 64  # 32 bytes hex = 64 characters
    assert f"start={nonce}" in deep_link
    assert "https://t.me/JarvisBot?start=" in deep_link

    # Operator is not yet authorized
    assert await authorizer.is_authorized(987654321) is False

    # Consume nonce
    success = await pairing_mgr.verify_and_pair(
        nonce=nonce, telegram_user_id=987654321, username="test_operator"
    )
    assert success is True

    # Operator is now authorized
    assert await authorizer.is_authorized(987654321) is True

    # Reusing the consumed nonce must fail
    reuse_success = await pairing_mgr.verify_and_pair(
        nonce=nonce, telegram_user_id=111111111, username="attacker"
    )
    assert reuse_success is False
    assert await authorizer.is_authorized(111111111) is False


@pytest.mark.asyncio
async def test_pairing_challenge_expiration(test_db: DatabaseEngine) -> None:
    """Verify expired pairing nonces are rejected."""
    pairing_mgr = TelegramPairingManager(database=test_db)

    # Insert an expired session manually
    expired_nonce = "expired_nonce_1234567890abcdef"
    past_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    test_db.create_telegram_pairing_session(nonce=expired_nonce, expires_at=past_time)

    success = await pairing_mgr.verify_and_pair(
        nonce=expired_nonce, telegram_user_id=12345, username="operator"
    )
    assert success is False


# ---------------------------------------------------------------------------
# Telegram Authorizer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorizer_lifecycle(test_db: DatabaseEngine) -> None:
    """Verify operator registration, whitelist verification, and revocation."""
    authorizer = TelegramAuthorizer(database=test_db)

    assert await authorizer.has_any_authorized_user() is False
    assert await authorizer.is_authorized(42) is False

    await authorizer.authorize_user(42, username="alice")
    assert await authorizer.has_any_authorized_user() is True
    assert await authorizer.is_authorized(42) is True
    assert await authorizer.is_authorized(99) is False

    await authorizer.revoke_user(42)
    assert await authorizer.is_authorized(42) is False


# ---------------------------------------------------------------------------
# Database Telegram Operations
# ---------------------------------------------------------------------------


def test_database_telegram_crud(test_db: DatabaseEngine) -> None:
    """Verify database idempotency, session tracking, task mapping, and tickets."""
    # 1. Idempotency updates
    assert test_db.is_telegram_update_processed(9001) is False
    test_db.mark_telegram_update_processed(9001)
    assert test_db.is_telegram_update_processed(9001) is True

    # 2. Chat sessions
    sess1 = test_db.get_or_create_telegram_chat_session(chat_id=555, telegram_user_id=777)
    assert sess1.startswith("SESSION-TG-")
    sess2 = test_db.get_or_create_telegram_chat_session(chat_id=555, telegram_user_id=777)
    assert sess1 == sess2

    # 3. Task mappings
    test_db.save_telegram_task_mapping(
        telegram_task_id="TG-001",
        jarvis_task_id="TASK-999",
        chat_id=555,
        status_message_id=888,
        status="PROCESSING",
    )
    mapping = test_db.get_telegram_task_mapping_by_tg_id("TG-001")
    assert mapping is not None
    assert mapping["jarvis_task_id"] == "TASK-999"
    assert mapping["status"] == "PROCESSING"

    test_db.update_telegram_task_mapping_status("TASK-999", "COMPLETED")
    updated = test_db.get_telegram_task_mapping_by_jarvis_id("TASK-999")
    assert updated is not None
    assert updated["status"] == "COMPLETED"

    # 4. Approval tickets
    test_db.create_telegram_approval_ticket(
        ticket_id="TICK-101",
        opaque_token="opaque_tok_abc",
        task_id="TASK-999",
        telegram_user_id=777,
        chat_id=555,
        risk_tier="HIGH",
        capability="shell.execute",
        safe_display="Execute shell command",
        expires_at="",
    )
    ticket = test_db.get_telegram_approval_ticket_by_opaque("opaque_tok_abc")
    assert ticket is not None
    assert ticket["ticket_id"] == "TICK-101"
    assert ticket["status"] == "PENDING"

    resolved = test_db.resolve_telegram_approval_ticket("TICK-101", "APPROVED")
    assert resolved is True

    # Cannot resolve again once decided
    second_resolve = test_db.resolve_telegram_approval_ticket("TICK-101", "DENIED")
    assert second_resolve is False


# ---------------------------------------------------------------------------
# Telegram Service Integration & Dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telegram_service_fail_closed_for_unauthorized_user(
    test_db: DatabaseEngine,
) -> None:
    """Ensure unauthorized users receive fail-closed responses without leaking system details."""
    pairing_mgr = TelegramPairingManager(database=test_db)
    authorizer = TelegramAuthorizer(database=test_db)
    card_mgr = CardManager()

    service = TelegramService(
        bot_token="dummy_token_123456",
        database=test_db,
        pairing_mgr=pairing_mgr,
        authorizer=authorizer,
        card_mgr=card_mgr,
    )

    # Mock dispatcher setup without real network polling
    from aiogram import Dispatcher

    dp = Dispatcher()
    service._register_handlers(dp)

    # Simulate message from unauthorized user
    mock_msg = MagicMock(spec=Message)
    mock_msg.from_user = User(id=99999, is_bot=False, first_name="Stranger")
    mock_msg.text = "Hello JARVIS"
    mock_msg.chat = MagicMock(id=99999)
    mock_msg.answer = AsyncMock()

    # Dispatch text message
    handlers = dp.message.handlers
    for h in handlers:
        if "on_text_message" in getattr(h.callback, "__name__", ""):
            await h.callback(mock_msg)
            break

    mock_msg.answer.assert_called_once()
    reply_text = mock_msg.answer.call_args[0][0]
    assert "ACCESS DENIED" in reply_text
    assert "private and requires cryptographic pairing" in reply_text


@pytest.mark.asyncio
async def test_telegram_service_approval_callback_flow(test_db: DatabaseEngine) -> None:
    """Verify approval callbacks resolve approval tickets and update message cards."""
    pairing_mgr = TelegramPairingManager(database=test_db)
    authorizer = TelegramAuthorizer(database=test_db)
    await authorizer.authorize_user(777, "operator")
    card_mgr = CardManager()

    service = TelegramService(
        bot_token="dummy_token_123456",
        database=test_db,
        pairing_mgr=pairing_mgr,
        authorizer=authorizer,
        card_mgr=card_mgr,
    )

    from aiogram import Dispatcher

    dp = Dispatcher()
    service._register_handlers(dp)

    # Seed an approval ticket in db
    test_db.create_telegram_approval_ticket(
        ticket_id="APPR-FLOW-1",
        opaque_token="safe_token_xyz",
        task_id="TASK-001",
        telegram_user_id=777,
        chat_id=777,
        risk_tier="HIGH",
        capability="system.volume",
        safe_display="Adjust system volume",
        expires_at="",
    )

    # Seed in approval_manager
    from jarvis.policy.approvals import approval_manager

    req = approval_manager.create_request(
        action_id="ACT-1",
        task_id="TASK-001",
        session_id="SESS-1",
        capability="system.volume",
        target_resource="audio",
        risk_summary="Adjust volume",
    )
    # Match approval_id to ticket_id
    approval_manager._pending_approvals["APPR-FLOW-1"] = req

    # Mock callback query for AUTHORIZE
    mock_cb = MagicMock(spec=CallbackQuery)
    mock_cb.from_user = User(id=777, is_bot=False, first_name="Operator")
    mock_cb.data = "appr:a:safe_token_xyz"
    mock_cb.message = MagicMock()
    mock_cb.message.chat = MagicMock(id=777)
    mock_cb.message.message_id = 404
    mock_cb.answer = AsyncMock()

    # Mock card_mgr.edit_card
    service._card_mgr.edit_card = AsyncMock(return_value=True)  # type: ignore[method-assign]

    handlers = dp.callback_query.handlers
    for h in handlers:
        if "on_callback_query" in getattr(h.callback, "__name__", ""):
            await h.callback(mock_cb)
            break

    # Verify ticket transitioned in DB
    ticket_after = test_db.get_telegram_approval_ticket_by_opaque("safe_token_xyz")
    assert ticket_after is not None
    assert ticket_after["status"] == "APPROVED"

    # Verify ticket resolved in approval_manager
    assert req.status.value == "APPROVED"
    mock_cb.answer.assert_called_with("Action authorized.")


def test_filesystem_resolve_path_aliases() -> None:
    """Verify alias normalization for common Windows folders."""
    home = Path.home()
    assert resolve_path("downloads") == home / "Downloads"
    assert resolve_path("Downloads") == home / "Downloads"
    assert resolve_path("documents") == home / "Documents"
    assert resolve_path("desktop") == home / "Desktop"
    assert resolve_path("~/test.txt") == home / "test.txt"


def test_filesystem_is_safe_artifact_path(tmp_path: Path) -> None:
    """Verify security boundaries reject sensitive files and allow safe deliverables."""
    # Disallowed files
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=secret")
    is_safe_env, reason_env = is_safe_artifact_path(env_file)
    assert is_safe_env is False
    assert "prohibited" in str(reason_env).lower()

    pem_file = tmp_path / "server.pem"
    pem_file.write_text("CERT")
    is_safe_pem, _ = is_safe_artifact_path(pem_file)
    assert is_safe_pem is False

    key_file = tmp_path / "id_rsa"
    key_file.write_text("KEY")
    is_safe_key, _ = is_safe_artifact_path(key_file)
    assert is_safe_key is False

    # Allowed safe files
    pptx_file = tmp_path / "presentation.pptx"
    pptx_file.write_bytes(b"PK\x03\x04fake_powerpoint")
    is_safe_pptx, _ = is_safe_artifact_path(pptx_file)
    assert is_safe_pptx is True

    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4")
    is_safe_pdf, _ = is_safe_artifact_path(pdf_file)
    assert is_safe_pdf is True


@pytest.mark.asyncio
async def test_telegram_service_delivers_document_artifact(tmp_path: Path) -> None:
    """Verify non-image artifacts (e.g. .pptx) are sent via send_document with upload_document action."""
    mock_live_mgr = MagicMock()
    fake_pptx = tmp_path / "JAVA_PBL.pptx"
    fake_pptx.write_bytes(b"PK\x03\x04powerpoint_data")

    from jarvis.telegram.live_session import TelegramLiveTurnResult

    mock_live_mgr.process_message = AsyncMock(
        return_value=TelegramLiveTurnResult(
            text="Here is your presentation, Sir.",
            artifacts=[fake_pptx],
            tools_called=["deliver_artifact"],
        )
    )

    mock_authorizer = AsyncMock()
    mock_authorizer.is_authorized = AsyncMock(return_value=True)

    mock_db = MagicMock()
    mock_db.get_or_create_telegram_chat_session = MagicMock(return_value="SESS-TG-DOC")

    service = TelegramService(
        bot_token="dummy_token_pptx",
        database=mock_db,
        authorizer=mock_authorizer,
        live_mgr=mock_live_mgr,
    )
    service._bot = MagicMock()
    service._bot.send_chat_action = AsyncMock()

    from aiogram import Dispatcher

    dp = Dispatcher()
    service._register_handlers(dp)

    mock_message = MagicMock(spec=Message)
    mock_message.text = "Send me that pptx file"
    mock_message.chat = MagicMock(id=888)
    mock_message.from_user = User(id=888, is_bot=False, first_name="Deepak")
    mock_message.answer = AsyncMock()
    mock_message.answer_document = AsyncMock()
    mock_message.answer_photo = AsyncMock()

    with patch("jarvis.config.settings.TELEGRAM_GEMINI_API_KEY", "dummy_key"):
        for h in dp.message.handlers:
            if "on_text_message" in getattr(h.callback, "__name__", ""):
                await h.callback(mock_message)
                break

    # Verify upload_document action was sent
    service._bot.send_chat_action.assert_any_call(chat_id=888, action="upload_document")
    # Verify answer_document was called
    mock_message.answer_document.assert_called_once()
    mock_message.answer_photo.assert_not_called()
