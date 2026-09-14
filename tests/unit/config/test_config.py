"""Tests for JARVIS Typed Configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.core.config import Settings, get_settings


def test_default_settings(test_settings: Settings) -> None:
    """Verify default settings instantiation and types."""
    assert test_settings.ENVIRONMENT == "test"
    assert test_settings.DEBUG is True
    assert test_settings.AUTONOMY_LEVEL == 1
    assert test_settings.GRAPH_STEP_BUDGET == 50
    assert test_settings.PRIMARY_MODEL_ID == "gemini-2.5-flash"
    assert test_settings.FAST_MODEL_ID == "qwen/qwen3.8-27b"


def test_autonomy_level_bounds() -> None:
    """Verify AUTONOMY_LEVEL is strictly bounded between 0 and 5."""
    # Valid bounds
    for level in range(6):
        s = Settings(AUTONOMY_LEVEL=level)
        assert level == s.AUTONOMY_LEVEL

    # Invalid bounds
    with pytest.raises(ValidationError):
        Settings(AUTONOMY_LEVEL=-1)

    with pytest.raises(ValidationError):
        Settings(AUTONOMY_LEVEL=6)


def test_directory_creation(tmp_path: Path) -> None:
    """Verify that ensure_directories creates the required storage folders."""
    data_dir = tmp_path / "custom_data"
    artifacts_dir = data_dir / "custom_artifacts"
    db_file = data_dir / "custom.db"

    settings = Settings(
        DATA_DIR=data_dir,
        ARTIFACTS_DIR=artifacts_dir,
        SQLITE_DB_PATH=db_file,
    )
    settings.ensure_directories()

    assert data_dir.exists() and data_dir.is_dir()
    assert artifacts_dir.exists() and artifacts_dir.is_dir()


def test_get_settings_caching() -> None:
    """Verify that get_settings returns a cached instance."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
