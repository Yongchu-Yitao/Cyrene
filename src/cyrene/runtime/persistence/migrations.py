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
    AddColumnMigration("scheduled_tasks", "updated_at", "TEXT NOT NULL DEFAULT ''"),
    AddColumnMigration("scheduled_tasks", "definition_revision", "INTEGER NOT NULL DEFAULT 1"),
    AddColumnMigration("scheduled_tasks", "schedule_revision", "INTEGER NOT NULL DEFAULT 1"),
    AddColumnMigration("scheduled_tasks", "lease_token", "TEXT"),
    AddColumnMigration("scheduled_tasks", "lease_until", "TEXT"),
    AddColumnMigration("scheduled_tasks", "current_run_id", "TEXT"),
    AddColumnMigration("scheduled_tasks", "scheduled_for", "TEXT"),
    AddColumnMigration("scheduled_tasks", "last_error", "TEXT"),
    AddColumnMigration("task_run_logs", "run_id", "TEXT NOT NULL DEFAULT ''"),
    AddColumnMigration("task_run_logs", "scheduled_for", "TEXT"),
    AddColumnMigration("task_run_logs", "started_at", "TEXT"),
    AddColumnMigration("task_run_logs", "completed_at", "TEXT"),
    AddColumnMigration("kb_documents", "content_hash", "TEXT DEFAULT ''"),
)

_POST_MIGRATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_project_id "
    "ON scheduled_tasks(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_due_lease "
    "ON scheduled_tasks(status, next_run, lease_until)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_task_run_logs_run_id "
    "ON task_run_logs(task_id, run_id) WHERE run_id <> ''",
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
