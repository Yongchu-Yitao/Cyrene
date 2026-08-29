"""SQLite connections and schemas used by context storage."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager, suppress
from collections.abc import Iterator
from pathlib import Path


def connect(database: str | Path) -> sqlite3.Connection:
    path = Path(database).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(path),
        check_same_thread=False,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Run one immediate transaction without masking lock errors on rollback."""

    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        with suppress(sqlite3.OperationalError):
            connection.execute("ROLLBACK")
        raise
    else:
        try:
            connection.execute("COMMIT")
        except Exception:
            with suppress(sqlite3.OperationalError):
                connection.execute("ROLLBACK")
            raise


def ensure_tree_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS context_tree_metadata (
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            tree_id TEXT NOT NULL UNIQUE,
            root_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS context_nodes (
            node_id TEXT PRIMARY KEY,
            parent_id TEXT,
            value_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(parent_id)
                REFERENCES context_nodes(node_id)
                ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_context_tree_root
            ON context_nodes((1))
            WHERE parent_id IS NULL;

        CREATE INDEX IF NOT EXISTS idx_context_nodes_parent
            ON context_nodes(parent_id, created_at, node_id);

        CREATE TABLE IF NOT EXISTS hook_bindings (
            hook_id TEXT PRIMARY KEY,
            event TEXT NOT NULL,
            plugin_id TEXT NOT NULL,
            root_only INTEGER NOT NULL DEFAULT 0 CHECK(root_only IN (0, 1)),
            matcher TEXT,
            failure_policy TEXT NOT NULL DEFAULT 'open'
                CHECK(failure_policy IN ('open', 'block', 'closed')),
            config_json TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_hook_bindings_event
            ON hook_bindings(event, enabled, root_only, created_at, hook_id);

        CREATE TABLE IF NOT EXISTS hook_queue (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            hook_id TEXT NOT NULL,
            event TEXT NOT NULL,
            tree_id TEXT NOT NULL,
            event_time TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            node_id TEXT,
            is_root INTEGER NOT NULL DEFAULT 0 CHECK(is_root IN (0, 1)),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'running', 'blocked', 'failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(hook_id) REFERENCES hook_bindings(hook_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_hook_queue_status_sequence
            ON hook_queue(status, sequence);
        """
    )
    _ensure_closed_hook_failure_policy(connection)


def _ensure_closed_hook_failure_policy(connection: sqlite3.Connection) -> None:
    """Upgrade pre-closed-policy trees without losing queued Hook deliveries."""

    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'hook_bindings'"
    ).fetchone()
    schema = str(row["sql"] or "") if row is not None else ""
    if "'closed'" in schema:
        return

    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        with transaction(connection):
            connection.execute("DROP INDEX IF EXISTS idx_hook_bindings_event")
            connection.execute("DROP INDEX IF EXISTS idx_hook_queue_status_sequence")
            connection.execute(
                """CREATE TABLE hook_bindings_new (
                    hook_id TEXT PRIMARY KEY,
                    event TEXT NOT NULL,
                    plugin_id TEXT NOT NULL,
                    root_only INTEGER NOT NULL DEFAULT 0 CHECK(root_only IN (0, 1)),
                    matcher TEXT,
                    failure_policy TEXT NOT NULL DEFAULT 'open'
                        CHECK(failure_policy IN ('open', 'block', 'closed')),
                    config_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE hook_queue_new (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    hook_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    tree_id TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    node_id TEXT,
                    is_root INTEGER NOT NULL DEFAULT 0 CHECK(is_root IN (0, 1)),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'running', 'blocked', 'failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(hook_id)
                        REFERENCES hook_bindings_new(hook_id) ON DELETE CASCADE
                )"""
            )
            connection.execute(
                "INSERT INTO hook_bindings_new SELECT * FROM hook_bindings"
            )
            connection.execute("INSERT INTO hook_queue_new SELECT * FROM hook_queue")
            connection.execute("DROP TABLE hook_queue")
            connection.execute("DROP TABLE hook_bindings")
            connection.execute(
                "ALTER TABLE hook_bindings_new RENAME TO hook_bindings"
            )
            connection.execute("ALTER TABLE hook_queue_new RENAME TO hook_queue")
            connection.execute(
                """CREATE INDEX idx_hook_bindings_event
                ON hook_bindings(event, enabled, root_only, created_at, hook_id)"""
            )
            connection.execute(
                """CREATE INDEX idx_hook_queue_status_sequence
                ON hook_queue(status, sequence)"""
            )
    finally:
        connection.execute("PRAGMA foreign_keys = ON")



def ensure_index_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS context_tree_index (
            tree_id TEXT PRIMARY KEY,
            root_id TEXT NOT NULL,
            database_path TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
