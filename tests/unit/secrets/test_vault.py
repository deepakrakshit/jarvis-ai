"""Tests for JARVIS Secrets Vault Abstraction."""

import os
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from jarvis.core.exceptions import SecretNotFoundError
from jarvis.core.secrets.vault import LocalSecretVault


def test_vault_get_set_memory(test_vault: LocalSecretVault) -> None:
    """Verify storing and retrieving secrets in-memory."""
    key = "MY_SPECIAL_KEY"
    val = SecretStr("special-value-xyz")

    assert not test_vault.has_secret(key)
    test_vault.set_secret(key, val)

    assert test_vault.has_secret(key)
    retrieved = test_vault.get_secret(key)
    assert retrieved.get_secret_value() == "special-value-xyz"
    assert key in test_vault.list_keys()


def test_vault_fallback_to_environment(test_vault: LocalSecretVault) -> None:
    """Verify vault falls back to os.environ when not found in memory."""
    with patch.dict(os.environ, {"ENV_SECRET_TOKEN": "token_from_env_123"}):
        assert test_vault.has_secret("ENV_SECRET_TOKEN")
        secret = test_vault.get_secret("ENV_SECRET_TOKEN")
        assert secret.get_secret_value() == "token_from_env_123"


def test_vault_fail_closed_on_missing(test_vault: LocalSecretVault) -> None:
    """Verify that requesting a nonexistent secret raises SecretNotFoundError."""
    with pytest.raises(SecretNotFoundError) as exc_info:
        test_vault.get_secret("NONEXISTENT_KEY_12345")
    assert "not found in vault or environment" in str(exc_info.value)


def test_get_vault_singleton() -> None:
    """Verify global vault singleton factory."""
    from jarvis.core.secrets.vault import get_vault

    v1 = get_vault()
    v2 = get_vault()
    assert v1 is v2
