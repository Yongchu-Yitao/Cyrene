"""Durable graph metadata; execution history remains owned by ContextTree.

One SQLite transaction performs each version check and metadata write. The
operation journal makes branch materialization idempotent across restarts.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from contextlib import contextmanager

from cyrene.workbench.persistence.schema import connect


class GraphConflict(ValueError):
    pass


class ContextGraphStore:
    def __init__(self, db_path):
        self.db_path = db_path
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS workbench_context_graph (key TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)')

    @contextmanager
    def connection(self):
        db = connect(self.db_path)
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def operation_gate(self, source_id):
        """An OS-owned gate cannot outlive a crashed branch-copy process."""
        directory = Path(self.db_path).parent / "context-graph-locks"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (hashlib.sha256(source_id.encode()).hexdigest() + ".lock")
        with path.open("a+b") as stream:
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise GraphConflict("A branch operation is still in progress") from exc
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def read(self, key):
        with self.connection() as db:
            row = db.execute('SELECT revision,payload FROM workbench_context_graph WHERE key=?', (key,)).fetchone()
        return {"revision": row[0], "value": json.loads(row[1])} if row else {"revision": 0, "value": {}}

    def write(self, key, value, expected):
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded) > 4_000_000:
            raise ValueError('Graph metadata is too large')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM workbench_context_graph WHERE key=?', (key,)).fetchone()
            revision = row[0] if row else 0
            if expected != revision:
                raise GraphConflict('内容已更新，请刷新后重试 / Revision conflict')
            db.execute('INSERT INTO workbench_context_graph VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET revision=excluded.revision,payload=excluded.payload', (key, revision + 1, encoded))
        return {"revision": revision + 1, "value": value}

    def entries(self, prefix):
        with self.connection() as db:
            rows = db.execute('SELECT key,revision,payload FROM workbench_context_graph WHERE substr(key,1,?)=?', (len(prefix), prefix)).fetchall()
        return {key[len(prefix):]: {"revision": revision, "value": json.loads(payload)} for key, revision, payload in rows}
