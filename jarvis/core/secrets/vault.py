"""JARVIS Secrets Vault Abstraction.

Guarantees secure secret retrieval, storage, and isolation outside model reasoning contexts.
"""

import os
from abc import ABC, abstractmethod

from pydantic import SecretStr

from jarvis.core.exceptions import SecretNotFoundError
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class SecretVault(ABC):
    """Abstract interface for secret storage and retrieval."""

    @abstractmethod
    def get_secret(self, key: str) -> SecretStr:
        """Retrieve a secret by key. Raises SecretNotFoundError if missing."""
        pass

    @abstractmethod
    def set_secret(self, key: str, value: SecretStr) -> None:
        """Store a secret in the vault."""
        pass

    @abstractmethod
    def has_secret(self, key: str) -> bool:
        """Check if a secret exists."""
        pass

    @abstractmethod
    def list_keys(self) -> list[str]:
        """List all available secret keys (never returns values)."""
        pass


class LocalSecretVault(SecretVault):
    """Local implementation storing secrets in-memory with environment variable fallback."""

    def __init__(self, initial_secrets: dict[str, SecretStr] | None = None) -> None:
        self._store: dict[str, SecretStr] = initial_secrets or {}

    def get_secret(self, key: str) -> SecretStr:
        """Retrieve a secret by key from internal store or environment."""
        # 1. Check in-memory store
        if key in self._store:
            logger.debug("secret_retrieved_from_memory", key=key)
            return self._store[key]

        # 2. Check environment variables
        env_val = os.environ.get(key)
        if env_val is not None:
            logger.debug("secret_retrieved_from_env", key=key)
            return SecretStr(env_val)

        # 3. Fail closed
        logger.warning("secret_not_found", key=key)
        raise SecretNotFoundError(f"Secret '{key}' not found in vault or environment.")

    def set_secret(self, key: str, value: SecretStr) -> None:
        """Store a secret in the in-memory vault."""
        self._store[key] = value
        logger.info("secret_stored", key=key)

    def has_secret(self, key: str) -> bool:
        """Check if a secret exists in memory or environment."""
        return key in self._store or key in os.environ

    def list_keys(self) -> list[str]:
        """Return list of keys currently stored in memory."""
        return list(self._store.keys())


_GLOBAL_VAULT: SecretVault | None = None


def get_vault() -> SecretVault:
    """Return the active singleton SecretVault instance."""
    global _GLOBAL_VAULT
    if _GLOBAL_VAULT is None:
        _GLOBAL_VAULT = LocalSecretVault()
    return _GLOBAL_VAULT
