"""Durable per-conversation Hook outputs outside the observed context tree."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path


class HookActionStore:
    def __init__(self, path: Path):
        self.path = path

    def record(self, entries):
        if not entries:
            return []
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS user_hook_actions (
                id TEXT PRIMARY KEY, target TEXT NOT NULL, body TEXT NOT NULL,
                run TEXT NOT NULL, time TEXT NOT NULL, consumer TEXT)""")
            fresh = []
            for key, target, body, run, time in entries:
                row = db.execute("INSERT OR IGNORE INTO user_hook_actions VALUES(?,?,?,?,?,NULL)",
                                 (key, target, json.dumps(body, ensure_ascii=False), run, time))
                if row.rowcount:
                    fresh.append((key, target, body, run, time))
            return fresh

    def take(self, target, consumer):
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='user_hook_actions'").fetchone():
                return []
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE user_hook_actions SET consumer=? WHERE target=? AND consumer IS NULL", (consumer, target))
            return [json.loads(row[0]) for row in db.execute(
                "SELECT body FROM user_hook_actions WHERE target=? AND consumer=? ORDER BY rowid", (target, consumer))]

    def messages(self):
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='user_hook_actions'").fetchone():
                return []
            return [(key, run, time, json.loads(body)) for key, run, time, body in db.execute(
                "SELECT id,run,time,body FROM user_hook_actions WHERE target='message' ORDER BY rowid")]
