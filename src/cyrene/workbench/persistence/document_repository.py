"""Generic SQLite Workbench document repository."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from cyrene.workbench.persistence.document_merge import (
    baseline,
    plain as _plain,
    three_way_merge as _three_way_merge,
    tracked as _tracked,
)
from cyrene.workbench.persistence.schema import connect as _connect

T = TypeVar("T")

@dataclass(frozen=True, slots=True)
class DocumentPorts:
    document_write_lock: Any
    read_chat_bundle: Any
    write_chat_bundle: Any


class DocumentRepository:
    def __init__(self, ports: DocumentPorts):
        self.ports = ports

    def _load_row(self, conn: sqlite3.Connection, key: str) -> Any | None:
        row = conn.execute('SELECT payload_json FROM workbench_state WHERE key = ?', (key,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(str(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f'invalid Workbench SQLite payload for {key}') from exc

    def _initial_value(self, default_factory: Callable[[], T]) -> T:
        return default_factory()

    def _write_row(self, conn: sqlite3.Connection, key: str, value: Any) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute('\n        INSERT INTO workbench_state (key, payload_json, updated_at)\n        VALUES (?, ?, ?)\n        ON CONFLICT(key) DO UPDATE SET\n            payload_json = excluded.payload_json,\n            updated_at = excluded.updated_at\n        ', (key, json.dumps(_plain(value), ensure_ascii=False), now))

    def read_document(self, db_path: str | Path, key: str, default_factory: Callable[[], T]) -> T:
        """Read one document from SQLite, creating its current default once."""
        if key == 'chats':
            return self.ports.read_chat_bundle(db_path, default_factory)
        conn = _connect(db_path)
        try:
            value = self._load_row(conn, key)
            if value is None:
                conn.execute('BEGIN IMMEDIATE')
                value = self._load_row(conn, key)
                if value is None:
                    value = self._initial_value(default_factory)
                    self._write_row(conn, key, value)
                conn.commit()
            return _tracked(value, key)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _write_document_locked(self, db_path: str | Path, key: str, value: T, default_factory: Callable[[], T], *, base_value: Any | None=None) -> T:
        """Merge and commit a document in one SQLite write transaction."""
        local = _plain(value)
        base = _plain(base_value) if base_value is not None else baseline(value)
        conn = _connect(db_path)
        try:
            conn.execute('BEGIN IMMEDIATE')
            remote = self._load_row(conn, key)
            if remote is None:
                remote = self._initial_value(default_factory)
            merged = local if base is None else _three_way_merge(base, local, remote)
            self._write_row(conn, key, merged)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return _tracked(merged, key)

    def write_document(self, db_path: str | Path, key: str, value: T, default_factory: Callable[[], T], *, base_value: Any | None=None) -> T:
        """Merge and commit one document without racing another local writer."""
        if key == 'chats':
            return self.ports.write_chat_bundle(db_path, value, default_factory, base_value=base_value)
        with self.ports.document_write_lock:
            result = self._write_document_locked(db_path, key, value, default_factory, base_value=base_value)
        return result

    def patch_document_fields(self, db_path: str | Path, key: str, fields: dict[str, Any], default_factory: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Atomically update a few top-level fields without a read/repair cycle.
    
        Selection-like state changes should not have to load a document through a
        domain layer that may perform expensive invariant repair or workspace
        scanning.  Read the latest committed row inside the write transaction so a
        concurrent document writer is preserved, then patch only the requested
        scalar fields.
        """
        if key == 'chats':
            current = self.ports.read_chat_bundle(db_path, default_factory)
            current.update(_plain(fields))
            updated = self.ports.write_chat_bundle(db_path, current, default_factory)
            return {name: _plain(updated.get(name)) for name in fields}
        updates = _plain(fields)
        with self.ports.document_write_lock:
            conn = _connect(db_path)
            try:
                conn.execute('BEGIN IMMEDIATE')
                current = self._load_row(conn, key)
                if current is None:
                    current = self._initial_value(default_factory)
                if not isinstance(current, dict):
                    raise TypeError(f'Workbench document {key} is not an object')
                current.update(updates)
                self._write_row(conn, key, current)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return {name: _plain(current.get(name)) for name in updates}

    def delete_document(self, db_path: str | Path, key: str) -> None:
        conn = _connect(db_path)
        try:
            conn.execute('BEGIN IMMEDIATE')
            if key == 'chats':
                conn.execute('DELETE FROM workbench_chat_messages')
                conn.execute('DELETE FROM workbench_chats')
            elif key == 'chat_changes':
                conn.execute('DELETE FROM workbench_chat_change_files')
                conn.execute('DELETE FROM workbench_chat_change_sets')
            conn.execute('DELETE FROM workbench_state WHERE key = ?', (key,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_document_keys(self, db_path: str | Path, *, prefix: str='') -> list[str]:
        conn = _connect(db_path)
        try:
            if prefix:
                rows = conn.execute('SELECT key FROM workbench_state WHERE key LIKE ? ORDER BY key', (prefix + '%',)).fetchall()
            else:
                rows = conn.execute('SELECT key FROM workbench_state ORDER BY key').fetchall()
            return [str(row[0]) for row in rows]
        finally:
            conn.close()

    def has_document_data(self, db_path: str | Path, key: str) -> bool:
        conn = _connect(db_path)
        try:
            if key == 'chats':
                row = conn.execute('SELECT 1 FROM workbench_chats LIMIT 1').fetchone()
                if row is not None:
                    return True
            elif key == 'chat_changes':
                row = conn.execute('SELECT 1 FROM workbench_chat_change_sets LIMIT 1').fetchone()
                if row is not None:
                    return True
            row = conn.execute('SELECT payload_json FROM workbench_state WHERE key = ?', (key,)).fetchone()
            if row is None:
                return False
            value = json.loads(str(row[0]))
            if isinstance(value, dict):
                return any((bool(item) for name, item in value.items() if name not in {'normalizedVersion', 'chatIds'}))
            return bool(value)
        finally:
            conn.close()
