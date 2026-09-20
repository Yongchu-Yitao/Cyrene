"""Plugin-owned durable outbound messages, independent of Agent lifetimes."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class MessageStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS messages (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE, effect TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL, sender TEXT NOT NULL,
                recipient TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', error TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
                next_attempt REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0
            )""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def accept(self, sender: str, recipient: str, content: str, title: str,
               effect: str, recipient_created_at: str) -> dict:
        fingerprint = hashlib.sha256(json.dumps(
            [sender, recipient, content], ensure_ascii=False,
        ).encode()).hexdigest()
        key = f"{sender}:{effect}" if effect else uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM messages WHERE effect=?", (key,)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise ValueError("The same tool call cannot send different messages.")
                return dict(existing)
            pending = db.execute(
                "SELECT count(*) FROM messages WHERE recipient=? AND status='queued'",
                (recipient,),
            ).fetchone()[0]
            if pending >= 100:
                raise ValueError("The target session inbox is full.")
            # Bound automatic ping-pong without requiring a new public tool argument.
            recent = db.execute(
                "SELECT count(*) FROM messages WHERE sender=? AND created>?",
                (sender, time.time() - 60),
            ).fetchone()[0]
            if recent >= 20:
                raise ValueError("Session message rate limit reached; try again later.")
            message_id = "session_msg_" + uuid4().hex
            payload = json.dumps({"content": content, "title": title,
                                  "recipient_created_at": recipient_created_at}, ensure_ascii=False)
            db.execute("""INSERT INTO messages
                (id,effect,fingerprint,sender,recipient,payload,created)
                VALUES (?,?,?,?,?,?,?)""",
                (message_id, key, fingerprint, sender, recipient, payload, time.time()))
            return dict(db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())

    def pending(self) -> list[dict]:
        with self.connect() as db:
            # One oldest message per recipient: a blocked session cannot starve others.
            return [dict(row) for row in db.execute("""SELECT * FROM messages m
                WHERE status='queued' AND next_attempt<=? AND lease_until<=? AND NOT EXISTS (
                    SELECT 1 FROM messages older WHERE older.recipient=m.recipient
                    AND older.status='queued' AND older.sequence<m.sequence)
                ORDER BY next_attempt, sequence LIMIT 32""", (time.time(), time.time()))]

    def claim(self, message_id: str) -> bool:
        with self.connect() as db:
            return db.execute("""UPDATE messages SET lease_until=?
                WHERE id=? AND status='queued' AND lease_until<=?""",
                (time.time() + 120, message_id, time.time())).rowcount == 1

    def release(self, message_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE messages SET lease_until=0, next_attempt=max(next_attempt, ?) WHERE id=?",
                       (time.time(), message_id,))

    def update(self, message_id: str, status: str, error: str = "", *, retry: bool = False) -> None:
        with self.connect() as db:
            db.execute("""UPDATE messages SET status=?, error=?, attempts=attempts+?,
                next_attempt=? WHERE id=?""",
                (status, error, int(retry), time.time() + (5 if retry else 1), message_id))

    def get(self, message_id: str) -> dict:
        with self.connect() as db:
            return dict(db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())
