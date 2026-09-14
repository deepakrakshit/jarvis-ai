"""JARVIS Shared Test Fixtures and Configurations."""

from collections.abc import Generator
from pathlib import Path

import pytest
from pydantic import SecretStr

from jarvis.core.config import Settings
from jarvis.core.logging import bind_correlation, setup_logging
from jarvis.core.secrets.vault import LocalSecretVault
from jarvis.storage.db import DatabaseManager


@pytest.fixture(autouse=True)
def configure_test_logging() -> None:
    """Ensure structured logging is initialized for tests."""
    setup_logging(log_level="DEBUG", json_format=False)


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Provide isolated Settings pointing to a temporary test directory."""
    data_dir = tmp_path / "data"
    artifacts_dir = data_dir / "artifacts"
    db_path = data_dir / "test_jarvis.db"

    return Settings(
        ENVIRONMENT="test",
        DEBUG=True,
        BASE_DIR=tmp_path,
        DATA_DIR=data_dir,
        ARTIFACTS_DIR=artifacts_dir,
        SQLITE_DB_PATH=db_path,
        AUTONOMY_LEVEL=1,
        GEMINI_API_KEY=SecretStr("test-gemini-key"),
        GROQ_API_KEY=SecretStr("test-groq-key"),
    )


@pytest.fixture
def temp_db(tmp_path: Path) -> DatabaseManager:
    """Provide an isolated, transient SQLite database manager."""
    db_file = tmp_path / "test_database.db"
    return DatabaseManager(db_path=db_file)


@pytest.fixture
def test_vault() -> LocalSecretVault:
    """Provide an isolated in-memory secret vault with sample secrets."""
    return LocalSecretVault(
        initial_secrets={
            "TEST_API_KEY": SecretStr("test-secret-value-123"),
            "GEMINI_API_KEY": SecretStr("AIzaSyFakeGoogleApiKeyForTesting001"),
            "GROQ_API_KEY": SecretStr("gsk_FakeGroqApiKeyForTestingLongValue01"),
        }
    )


@pytest.fixture
def sample_correlation_ids() -> Generator[dict[str, str], None, None]:
    """Bind deterministic correlation IDs for trace verification."""
    corr = {
        "request_id": "req-test-001",
        "session_id": "sess-test-001",
        "task_id": "task-test-001",
        "agent_id": "agent-test-001",
        "attempt_id": "att-test-001",
        "logical_effect_id": "eff-test-001",
    }
    with bind_correlation(**corr):
        yield corr
