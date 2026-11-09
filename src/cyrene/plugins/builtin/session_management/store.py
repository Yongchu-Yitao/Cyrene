"""Durable ownership, paused queues and replay receipts for managed sessions."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS task_presentation (
                    task_id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS effects (
                    key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    owner TEXT NOT NULL, target TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', error TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, target TEXT NOT NULL,
                    payload TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS messages_target_status ON messages(target,status,seq);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def replay(self, key, fingerprint):
        with self.connect() as db:
            row = db.execute("SELECT * FROM effects WHERE key=?", (key,)).fetchone()
        if row:
            if row["fingerprint"] != fingerprint:
                raise ValueError("Tool replay arguments conflict with the original call")
            return json.loads(row["result"])

    def save(self, key, fingerprint, result):
        with self.connect() as db:
            db.execute("UPDATE effects SET result=? WHERE key=? AND fingerprint=?", (json.dumps(result), key, fingerprint))

    def reserve(self, key, fingerprint):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO effects VALUES (?,?,?)", (key, fingerprint, "null"))
        self.replay(key, fingerprint)

    def register(self, session_id, owner):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO sessions(id,owner) VALUES (?,?)", (session_id, owner))

    def sessions(self, owner):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM sessions WHERE owner=? ORDER BY rowid", (owner,))]

    def owned(self, owner, target):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=? AND owner=?", (target, owner)).fetchone()
        if not row:
            raise ValueError("Task is not owned by this assistant")
        return dict(row)

    def pause(self, target):
        with self.connect() as db:
            db.execute("UPDATE sessions SET paused=1 WHERE id=?", (target,))

    def enqueue(self, owner, target, message_id, payload):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id FROM messages WHERE id=?", (message_id,)).fetchone()
            if not existing:
                count = db.execute("SELECT count(*) FROM messages WHERE target=? AND status='queued'", (target,)).fetchone()[0]
                if count >= 100:
                    raise ValueError("Task inbox is full")
                db.execute("INSERT INTO messages(id,owner,target,payload) VALUES (?,?,?,?)", (message_id, owner, target, json.dumps(payload, ensure_ascii=False)))
                # Only a new explicit message resumes the queue, never replay.
                db.execute("UPDATE sessions SET paused=0 WHERE id=?", (target,))
                db.execute("UPDATE messages SET next_attempt=0 WHERE target=? AND status='queued'", (target,))

    def message(self, message_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return dict(row) if row else None

    def pending(self):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    """SELECT m.* FROM messages m
                JOIN sessions s ON s.id=m.target AND s.owner=m.owner
                WHERE m.status='queued' AND s.paused=0 AND m.next_attempt<=?
                AND NOT EXISTS (SELECT 1 FROM messages older WHERE older.target=m.target
                    AND older.status='queued' AND older.seq<m.seq)
                ORDER BY m.seq LIMIT 32""",
                    (time.time(),),
                )
            ]

    def update(self, message_id, status, error=""):
        with self.connect() as db:
            db.execute(
                "UPDATE messages SET status=?,error=?,attempts=attempts+?,next_attempt=? WHERE id=?", (status, error, int(bool(error)), time.time() + 5 if error else 0, message_id)
            )

    def pending_count(self, target):
        with self.connect() as db:
            return db.execute("SELECT count(*) FROM messages WHERE target=? AND status='queued'", (target,)).fetchone()[0]

    def delivery_error(self, target):
        with self.connect() as db:
            row = db.execute("SELECT id,status,error FROM messages WHERE target=? AND error<>'' ORDER BY seq DESC LIMIT 1", (target,)).fetchone()
        return dict(row) if row else None

    def defer(self, message_id):
        with self.connect() as db:
            db.execute("UPDATE messages SET next_attempt=? WHERE id=?", (time.time() + 2, message_id))

    def session(self, target):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (target,)).fetchone()
        return dict(row) if row else None

    def notify(self, event_id, owner, target, payload):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO notifications(id,owner,target,payload) VALUES (?,?,?,?)", (event_id, owner, target, json.dumps(payload, ensure_ascii=False)))

    def notifications(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM notifications WHERE delivered=0 ORDER BY rowid")]

    def acknowledge(self, event_id):
        with self.connect() as db:
            db.execute("UPDATE notifications SET delivered=1 WHERE id=?", (event_id,))

    def presentation(self, target):
        with self.connect() as db:
            row = db.execute("SELECT document FROM task_presentation WHERE task_id=?", (target,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_presentation(self, target, changes):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT document FROM task_presentation WHERE task_id=?", (target,)).fetchone()
            document = json.loads(row[0]) if row else {}
            document.update(changes)
            db.execute("INSERT OR REPLACE INTO task_presentation VALUES (?,?)", (target, json.dumps(document, ensure_ascii=False)))
        return document

    def reset_owner(self, owner, targets):
        with self.connect() as db:
            for target in targets:
                db.execute("DELETE FROM task_presentation WHERE task_id=?", (target,))
            db.execute("DELETE FROM messages WHERE owner=?", (owner,))
            db.execute("DELETE FROM notifications WHERE owner=?", (owner,))
            db.execute("DELETE FROM sessions WHERE owner=?", (owner,))
            prefix = owner + ":"
            db.execute("DELETE FROM effects WHERE substr(key, 1, ?)=?", (len(prefix), prefix))
