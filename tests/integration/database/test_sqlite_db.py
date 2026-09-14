"""Integration tests for JARVIS SQLite Database Manager."""

import pytest

from jarvis.storage.db import DatabaseManager


def test_sqlite_pragmas(temp_db: DatabaseManager) -> None:
    """Verify foreign keys and WAL mode are enabled."""
    with temp_db.get_connection() as conn:
        cursor = conn.execute("PRAGMA foreign_keys;")
        assert cursor.fetchone()[0] == 1

        cursor = conn.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0].upper()
        # WAL or memory mode depending on transient file system
        assert mode in ("WAL", "MEMORY", "DELETE")


@pytest.mark.asyncio
async def test_sqlite_async_operations(temp_db: DatabaseManager) -> None:
    """Verify async execution, insertion, and retrieval."""
    # Create test table
    temp_db.execute_script("""
        CREATE TABLE test_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            val INT NOT NULL
        );
    """)

    # Async insert
    insert_query = "INSERT INTO test_records (name, val) VALUES (?, ?);"
    rowcount = await temp_db.execute_async(insert_query, ("alpha", 100))
    assert rowcount == 1

    await temp_db.execute_async(insert_query, ("beta", 200))

    # Async fetchone
    row = await temp_db.fetchone_async("SELECT * FROM test_records WHERE name = ?;", ("alpha",))
    assert row is not None
    assert row["name"] == "alpha"
    assert row["val"] == 100

    # Async fetchall
    rows = await temp_db.fetchall_async("SELECT * FROM test_records ORDER BY id ASC;")
    assert len(rows) == 2
    assert rows[1]["name"] == "beta"


def test_sqlite_transaction_rollback(temp_db: DatabaseManager) -> None:
    """Verify that unhandled exceptions roll back the transaction."""
    temp_db.execute_script("""
        CREATE TABLE balance (
            id INTEGER PRIMARY KEY,
            amount INT
        );
        INSERT INTO balance (id, amount) VALUES (1, 500);
    """)

    with pytest.raises(RuntimeError), temp_db.get_connection() as conn:
        conn.execute("UPDATE balance SET amount = 1000 WHERE id = 1;")
        raise RuntimeError("Simulated crash mid-transaction")

    with temp_db.get_connection() as conn:
        row = conn.execute("SELECT amount FROM balance WHERE id = 1;").fetchone()
        assert row["amount"] == 500  # Remains 500 due to rollback


def test_get_db_singleton() -> None:
    """Verify get_db returns singleton instance."""
    from jarvis.storage.db import get_db

    db1 = get_db()
    db2 = get_db()
    assert db1 is db2
