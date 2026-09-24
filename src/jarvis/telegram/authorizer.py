"""Authorization policy and access control for native JARVIS Telegram interface."""

from __future__ import annotations

from typing import Optional

from jarvis.storage.database import DatabaseEngine, db


class TelegramAuthorizer:
    """Enforces operator whitelist authorization at the Telegram boundary."""

    def __init__(self, database: Optional[DatabaseEngine] = None) -> None:
        self.db = database or db

    async def is_authorized(self, telegram_user_id: Optional[int]) -> bool:
        """Verify whether the numeric Telegram user ID is an authorized operator."""
        if telegram_user_id is None:
            return False
        return self.db.is_telegram_user_authorized(telegram_user_id)

    async def authorize_user(self, telegram_user_id: int, username: Optional[str] = None) -> None:
        """Register and authorize an operator."""
        self.db.add_telegram_authorized_user(telegram_user_id=telegram_user_id, username=username)

    async def revoke_user(self, telegram_user_id: int) -> None:
        """Revoke operator authorization."""
        self.db.revoke_telegram_user(telegram_user_id)

    async def has_any_authorized_user(self) -> bool:
        """Check if any operator is currently authorized."""
        users = self.db.list_telegram_authorized_users()
        return len(users) > 0


# Default singleton instance
telegram_authorizer = TelegramAuthorizer()
