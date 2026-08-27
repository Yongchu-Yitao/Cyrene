"""Idempotent database upgrades owned by the Schedule Plugin."""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from .schema import SCHEDULE_SCHEMA


@dataclass(frozen=True, slots=True)
class _AddColumnMigration:
    table: str
    column: str
    definition: str

    @property
    def statement(self) -> str:
        return f"ALTER TABLE {self.table} ADD COLUMN {self.column} {self.definition}"


_COLUMN_MIGRATIONS = (
    _AddColumnMigration("scheduled_tasks", "permission_mode", "TEXT DEFAULT 'workspace_only'"),
    _AddColumnMigration("scheduled_tasks", "project_id", "TEXT DEFAULT 'default'"),
    _AddColumnMigration("scheduled_tasks", "schedule_timezone", "TEXT DEFAULT 'UTC'"),
    _AddColumnMigration("scheduled_tasks", "origin_session_id", "TEXT DEFAULT ''"),
    _AddColumnMigration("scheduled_tasks", "action_type", "TEXT DEFAULT 'agent_task'"),
    _AddColumnMigration("scheduled_tasks", "updated_at", "TEXT NOT NULL DEFAULT ''"),
    _AddColumnMigration(
        "scheduled_tasks", "definition_revision", "INTEGER NOT NULL DEFAULT 1"
    ),
    _AddColumnMigration(
        "scheduled_tasks", "schedule_revision", "INTEGER NOT NULL DEFAULT 1"
    ),
    _AddColumnMigration("scheduled_tasks", "lease_token", "TEXT"),
    _AddColumnMigration("scheduled_tasks", "lease_until", "TEXT"),
    _AddColumnMigration("scheduled_tasks", "current_run_id", "TEXT"),
    _AddColumnMigration("scheduled_tasks", "scheduled_for", "TEXT"),
    _AddColumnMigration("scheduled_tasks", "last_error", "TEXT"),
    _AddColumnMigration("task_run_logs", "run_id", "TEXT NOT NULL DEFAULT ''"),
    _AddColumnMigration("task_run_logs", "scheduled_for", "TEXT"),
    _AddColumnMigration("task_run_logs", "started_at", "TEXT"),
    _AddColumnMigration("task_run_logs", "completed_at", "TEXT"),
)

_POST_MIGRATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_project_id "
    "ON scheduled_tasks(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_due_lease "
    "ON scheduled_tasks(status, next_run, lease_until)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_task_run_logs_run_id "
    "ON task_run_logs(task_id, run_id) WHERE run_id <> ''",
)


async def initialize_schedule_database(db_path: str) -> None:
    """Create and upgrade Schedule tables only when the Plugin is active."""

    async with aiosqlite.connect(db_path) as database:
        await database.execute("PRAGMA journal_mode = WAL")
        await database.executescript(SCHEDULE_SCHEMA)
        for migration in _COLUMN_MIGRATIONS:
            try:
                await database.execute(migration.statement)
            except aiosqlite.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        for statement in _POST_MIGRATION_INDEXES:
            await database.execute(statement)
        await database.commit()


__all__ = ["initialize_schedule_database"]
