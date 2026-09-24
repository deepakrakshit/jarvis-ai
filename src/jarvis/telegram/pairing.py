"""Cryptographic operator pairing manager for native JARVIS Telegram interface."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from jarvis.config import settings
from jarvis.storage.database import DatabaseEngine, db
from jarvis.telemetry import logger


class TelegramPairingManager:
    """Manages cryptographic pairing challenge nonces and deep link onboarding."""

    def __init__(self, database: Optional[DatabaseEngine] = None) -> None:
        self.db = database or db

    async def create_pairing_challenge(
        self, bot_username: str, ttl_seconds: Optional[int] = None
    ) -> Tuple[str, str]:
        """Generate a single-use 256-bit challenge nonce and Telegram deep link.

        Returns:
            Tuple of (nonce, deep_link_url)
        """
        ttl = float(ttl_seconds or getattr(settings, "TELEGRAM_PAIRING_TTL_SECONDS", 300) or 300)
        nonce = secrets.token_hex(32)  # 256 bits of cryptographic entropy
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=ttl)).isoformat()

        self.db.create_telegram_pairing_session(nonce=nonce, expires_at=expires_at)
        clean_bot_username = bot_username.lstrip("@")
        deep_link = f"https://t.me/{clean_bot_username}?start={nonce}"

        logger.info(f"Generated new Telegram pairing challenge (expires in {ttl}s)")
        return nonce, deep_link

    async def verify_and_pair(
        self, nonce: str, telegram_user_id: int, username: Optional[str] = None
    ) -> bool:
        """Validate single-use challenge nonce and authorize the operator account."""
        clean_nonce = nonce.strip()
        if not clean_nonce:
            return False

        success = self.db.consume_telegram_pairing_nonce(
            nonce=clean_nonce, telegram_user_id=telegram_user_id
        )
        if not success:
            logger.warning(
                f"Telegram pairing failed for user {telegram_user_id} with nonce {clean_nonce[:8]}..."
            )
            return False

        self.db.add_telegram_authorized_user(telegram_user_id=telegram_user_id, username=username)
        logger.info(
            f"Successfully paired and authorized operator: ID={telegram_user_id}, username={username}"
        )
        return True


# Default singleton instance
telegram_pairing_manager = TelegramPairingManager()
