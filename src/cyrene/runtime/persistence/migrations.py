"""Idempotent runtime-database migrations.

Schema creation and compatibility upgrades are kept separate from repository
queries so opening the database has one explicit migration boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite


@dataclass(frozen=True, slots=True)
class AddColumnMigration:
    table: str
    column: str
    definition: str

    @property
    def statement(self) -> str:
        return f"ALTER TABLE {self.table} ADD COLUMN {self.column} {self.definition}"


_COLUMN_MIGRATIONS = (
    AddColumnMigration("scheduled_tasks", "permission_mode", "TEXT DEFAULT 'workspace_only'"),
    AddColumnMigration("scheduled_tasks", "project_id", "TEXT DEFAULT 'default'"),
    AddColumnMigration("scheduled_tasks", "schedule_timezone", "TEXT DEFAULT 'UTC'"),
    AddColumnMigration("scheduled_tasks", "origin_session_id", "TEXT DEFAULT ''"),
    AddColumnMigration("scheduled_tasks", "action_type", "TEXT DEFAULT 'agent_task'"),
    AddColumnMigration("entities", "project_id", "TEXT DEFAULT 'default'"),
    AddColumnMigration("entity_candidates", "project_id", "TEXT DEFAULT 'default'"),
    AddColumnMigration("kb_documents", "content_hash", "TEXT DEFAULT ''"),
)

_POST_MIGRATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_project_id "
    "ON scheduled_tasks(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_entities_project_id ON entities(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_entity_candidates_project_id "
    "ON entity_candidates(project_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_documents_content_hash "
    "ON kb_documents(content_hash) WHERE content_hash <> ''",
)


async def initialize_runtime_database(db_path: str, schema_sql: str) -> None:
    """Create the current schema and apply upgrades for existing databases."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode = WAL")
        await db.executescript(schema_sql)
        for migration in _COLUMN_MIGRATIONS:
            try:
                await db.execute(migration.statement)
            except aiosqlite.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        for statement in _POST_MIGRATION_INDEXES:
            await db.execute(statement)
        await db.commit()
