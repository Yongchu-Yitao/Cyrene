"""Workbench SQLite schema and connection lifecycle."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workbench_state (
    key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workbench_task_sessions (
    session_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workbench_task_sessions_project
    ON workbench_task_sessions(project_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS workbench_chats (
    chat_id TEXT PRIMARY KEY,
    ordinal INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workbench_chats_ordinal
    ON workbench_chats(ordinal);
CREATE TABLE IF NOT EXISTS workbench_chat_messages (
    chat_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    message_id TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    PRIMARY KEY(chat_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_workbench_chat_messages_id
    ON workbench_chat_messages(chat_id, message_id);
CREATE TABLE IF NOT EXISTS workbench_chat_change_sets (
    change_set_id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT '',
    diff_chars INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workbench_chat_change_sets_chat
    ON workbench_chat_change_sets(chat_id, completed_at DESC);
CREATE TABLE IF NOT EXISTS workbench_chat_change_files (
    change_set_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    diff_text TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(change_set_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_workbench_chat_change_files_path
    ON workbench_chat_change_files(change_set_id, path);
"""

_INBOX_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS workbench_agent_inbox (
        event_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        round_id TEXT NOT NULL DEFAULT '',
        event_type TEXT NOT NULL,
        status TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 0,
        dedupe_key TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        completed_at TEXT NOT NULL DEFAULT '',
        run_id TEXT NOT NULL DEFAULT '',
        batch_id TEXT NOT NULL DEFAULT '',
        termination_reason TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workbench_agent_run_events (
        event_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        round_id TEXT NOT NULL DEFAULT '',
        batch_id TEXT NOT NULL DEFAULT '',
        event_type TEXT NOT NULL,
        tool_call_id TEXT NOT NULL DEFAULT '',
        tool_name TEXT NOT NULL DEFAULT '',
        queue_length INTEGER NOT NULL DEFAULT 0,
        duration_ms REAL,
        tool_queue_wait_ms REAL,
        tool_execution_ms REAL,
        agent_wait_ms REAL,
        result_wait_ms REAL,
        result_queue_delay_ms REAL,
        termination_reason TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
)

_INBOX_INDEXES = (
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_workbench_agent_inbox_dedupe
       ON workbench_agent_inbox(session_id, dedupe_key) WHERE dedupe_key <> ''""",
    """CREATE INDEX IF NOT EXISTS idx_workbench_agent_inbox_pending
       ON workbench_agent_inbox(session_id, status, priority, created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_workbench_agent_inbox_completed
       ON workbench_agent_inbox(status, completed_at)""",
    """CREATE INDEX IF NOT EXISTS idx_workbench_agent_run_events_run
       ON workbench_agent_run_events(session_id, run_id, created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_workbench_agent_run_events_created
       ON workbench_agent_run_events(created_at)""",
)

_INBOX_COLUMNS = (
    ("run_id", "TEXT NOT NULL DEFAULT ''"),
    ("batch_id", "TEXT NOT NULL DEFAULT ''"),
    ("termination_reason", "TEXT NOT NULL DEFAULT ''"),
)

_RUN_EVENT_COLUMNS = (
    ("tool_queue_wait_ms", "REAL"),
    ("tool_execution_ms", "REAL"),
    ("agent_wait_ms", "REAL"),
    ("result_wait_ms", "REAL"),
    ("result_queue_delay_ms", "REAL"),
)

_INBOX_DURABLE_RETENTION_DAYS = 30

_SCHEMA_LOCK = threading.Lock()

_SCHEMA_READY: set[str] = set()


def _add_missing_columns(
    conn: sqlite3.Connection,
    table: str,
    definitions: tuple[tuple[str, str], ...],
) -> None:
    columns = {
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")
    }
    for name, definition in definitions:
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _ensure_inbox_schema(conn: sqlite3.Connection) -> None:
    for statement in _INBOX_TABLES:
        conn.execute(statement)
    _add_missing_columns(conn, "workbench_agent_inbox", _INBOX_COLUMNS)
    _add_missing_columns(conn, "workbench_agent_run_events", _RUN_EVENT_COLUMNS)
    for statement in _INBOX_INDEXES:
        conn.execute(statement)

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(days=_INBOX_DURABLE_RETENTION_DAYS)
    ).isoformat()
    conn.execute(
        """
        DELETE FROM workbench_agent_inbox
        WHERE status IN ('completed', 'failed', 'cancelled')
          AND completed_at GLOB '????-??-*'
          AND completed_at < ?
        """,
        (cutoff,),
    )
    conn.execute(
        """
        DELETE FROM workbench_agent_run_events
        WHERE created_at GLOB '????-??-*' AND created_at < ?
        """,
        (cutoff,),
    )


def ensure_schema(db_path: str | Path) -> None:
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    cache_key = str(path)
    if cache_key in _SCHEMA_READY and path.exists():
        return
    with _SCHEMA_LOCK:
        if cache_key in _SCHEMA_READY and path.exists():
            return
        for attempt, delay in enumerate((0.0, 0.05, 0.1, 0.2, 0.4, 0.8)):
            if delay:
                time.sleep(delay)
            try:
                with sqlite3.connect(path, timeout=5) as conn:
                    conn.execute("PRAGMA busy_timeout = 5000")
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.execute("PRAGMA synchronous = NORMAL")
                    # Include BEGIN in the script: sqlite3.executescript()
                    # otherwise commits a pending transaction before running.
                    # IMMEDIATE serializes schema inspection and ALTER TABLE
                    # across both threads and processes.
                    conn.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA)
                    chat_columns = {
                        str(row[1])
                        for row in conn.execute("PRAGMA table_info(workbench_chats)")
                    }
                    if "summary_json" not in chat_columns:
                        conn.execute(
                            "ALTER TABLE workbench_chats "
                            "ADD COLUMN summary_json TEXT NOT NULL DEFAULT '{}'"
                        )
                    _ensure_inbox_schema(conn)
                    conn.commit()
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 5:
                    raise
        _SCHEMA_READY.add(cache_key)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


SCHEMA_READY = _SCHEMA_READY
connect = _connect
