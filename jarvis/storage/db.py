"""JARVIS Development Database Abstraction.

Provides a hardened SQLite connection manager with WAL mode, foreign keys, and transaction safety.
"""

import asyncio
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """Manages SQLite connections with WAL mode and foreign key enforcement."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            settings = get_settings()
            db_path = settings.SQLITE_DB_PATH
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_pragmas()

    def _init_pragmas(self) -> None:
        """Initialize database file with required pragmas."""
        with self.get_connection() as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager yielding a configured SQLite connection."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    async def execute_async(self, query: str, parameters: tuple[Any, ...] = ()) -> int:
        """Execute a query asynchronously in a thread pool and return rowcount."""

        def _exec() -> int:
            with self.get_connection() as conn:
                cursor = conn.execute(query, parameters)
                return cursor.rowcount

        return await asyncio.to_thread(_exec)

    async def fetchall_async(
        self, query: str, parameters: tuple[Any, ...] = ()
    ) -> list[sqlite3.Row]:
        """Execute a query and fetch all rows asynchronously."""

        def _fetch() -> list[sqlite3.Row]:
            with self.get_connection() as conn:
                cursor = conn.execute(query, parameters)
                return cast("list[sqlite3.Row]", cursor.fetchall())

        return await asyncio.to_thread(_fetch)

    async def fetchone_async(
        self, query: str, parameters: tuple[Any, ...] = ()
    ) -> sqlite3.Row | None:
        """Execute a query and fetch a single row asynchronously."""

        def _fetch() -> sqlite3.Row | None:
            with self.get_connection() as conn:
                cursor = conn.execute(query, parameters)
                row = cursor.fetchone()
                if row is None:
                    return None
                return cast("sqlite3.Row", row)

        return await asyncio.to_thread(_fetch)

    def execute_script(self, script: str) -> None:
        """Execute a multi-statement SQL script."""
        with self.get_connection() as conn:
            conn.executescript(script)


_GLOBAL_DB: DatabaseManager | None = None


def get_db(db_path: Path | None = None) -> DatabaseManager:
    """Return singleton or configured DatabaseManager."""
    global _GLOBAL_DB
    if db_path is not None:
        return DatabaseManager(db_path)
    if _GLOBAL_DB is None:
        _GLOBAL_DB = DatabaseManager()
    return _GLOBAL_DB
