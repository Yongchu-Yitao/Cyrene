"""Idempotent runtime-database migrations.

Schema creation and compatibility upgrades are kept separate from repository
queries so opening the database has one explicit migration boundary.
"""

from __future__ import annotations

import aiosqlite


async def initialize_runtime_database(db_path: str, schema_sql: str) -> None:
    """Create the current schema and apply upgrades for existing databases."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode = WAL")
        await db.executescript(schema_sql)
        await db.commit()
