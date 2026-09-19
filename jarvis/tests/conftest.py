"""Pytest Fixtures for JARVIS Test Suite."""

import tempfile
from pathlib import Path
from typing import Generator

import pytest

from jarvis.config import JarvisSettings
from jarvis.storage.database import DatabaseEngine


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Provide an isolated temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def test_db(temp_dir: Path) -> DatabaseEngine:
    """Provide an isolated test database instance."""
    db_path = temp_dir / "test_jarvis.db"
    return DatabaseEngine(db_path=db_path)


@pytest.fixture
def test_settings(temp_dir: Path) -> JarvisSettings:
    """Provide isolated settings pointing to temp paths."""
    return JarvisSettings(
        WORKSPACE_DIR=temp_dir,
        DATA_DIR=temp_dir / "data",
        DATABASE_PATH=temp_dir / "data" / "test.db",
        LOG_DIR=temp_dir / "logs",
        ARTIFACTS_DIR=temp_dir / "artifacts",
    )
