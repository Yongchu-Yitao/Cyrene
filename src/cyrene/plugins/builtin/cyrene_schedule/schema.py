"""SQLite schema owned exclusively by the Schedule Plugin."""

from __future__ import annotations

SCHEDULE_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    origin_session_id TEXT DEFAULT '',
    project_id TEXT DEFAULT 'default',
    prompt TEXT NOT NULL,
    action_type TEXT DEFAULT 'agent_task',
    schedule_type TEXT NOT NULL,
    schedule_value TEXT NOT NULL,
    schedule_timezone TEXT DEFAULT 'UTC',
    next_run TEXT,
    last_run TEXT,
    last_result TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT '',
    permission_mode TEXT DEFAULT 'workspace_only',
    definition_revision INTEGER NOT NULL DEFAULT 1,
    schedule_revision INTEGER NOT NULL DEFAULT 1,
    lease_token TEXT,
    lease_until TEXT,
    current_run_id TEXT,
    scheduled_for TEXT,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run
ON scheduled_tasks(next_run);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_status
ON scheduled_tasks(status);

CREATE TABLE IF NOT EXISTS task_run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    run_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    result TEXT,
    error TEXT,
    run_id TEXT NOT NULL DEFAULT '',
    scheduled_for TEXT,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_task_run_logs_task_id
ON task_run_logs(task_id);
"""


__all__ = ["SCHEDULE_SCHEMA"]
