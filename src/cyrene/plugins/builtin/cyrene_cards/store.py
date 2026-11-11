"""Plugin-owned SQLite state; every mutation and replay receipt commits together."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from cyrene.platform.database_notifications import notify_database_commit


class Store:
    def __init__(self, directory):
        self.path = Path(directory) / "cards.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cards (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, context_id TEXT,
                    status TEXT NOT NULL, pinned INTEGER NOT NULL, document TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS cards_owner ON cards(owner,context_id,status);
                CREATE TABLE IF NOT EXISTS effects (
                    owner TEXT NOT NULL, effect TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY(owner,effect));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
                    event_id TEXT NOT NULL, card_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    document TEXT NOT NULL, UNIQUE(owner,event_id));
                CREATE INDEX IF NOT EXISTS events_card ON events(card_id,seq);
                CREATE TABLE IF NOT EXISTS versions (
                    card_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    document TEXT NOT NULL, PRIMARY KEY(card_id,revision));
                CREATE TABLE IF NOT EXISTS deliveries (
                    owner TEXT NOT NULL, event_id TEXT NOT NULL, version TEXT NOT NULL,
                    state TEXT NOT NULL, detail TEXT NOT NULL,
                    PRIMARY KEY(owner,event_id));
            """)

    @contextmanager
    def connect(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                if write:
                    db.execute("BEGIN IMMEDIATE")
                yield db
            if db.total_changes:
                notify_database_commit(self.path)
        finally:
            db.close()
