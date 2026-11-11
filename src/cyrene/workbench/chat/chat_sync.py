"""Cheap conditional chat reads; tokens cover durable history and live presentation.

Context activity can change independently of the chat row. Observe its SQLite
commits on a bounded set of read-only connections, without projecting the tree.
Never hold a read transaction between polls (which would pin the WAL).
"""
from __future__ import annotations

import atexit
import hashlib
import json
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path
from uuid import uuid4


class ContextCommitVersions:
    def __init__(self, max_entries: int = 64):
        self._entries = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def read(self, path: Path) -> str:
        path = path.resolve()
        with self._lock:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            entry = self._entries.pop(path, None)
            if entry is not None and entry[0] != identity:
                entry[1].close()
                entry = None
            if entry is None:
                conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                       isolation_level=None, check_same_thread=False)
                # data_version is connection-local. A new connection must never
                # reuse a token issued by an evicted connection or earlier app.
                entry = (identity, conn, uuid4().hex)
            try:
                version = entry[1].execute("PRAGMA data_version").fetchone()[0]
                after = path.stat()
                if (after.st_dev, after.st_ino) != identity:
                    raise OSError("Context database replaced during revision read")
            except BaseException:
                entry[1].close()
                raise
            self._entries[path] = entry
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)[1][1].close()
            return f"{entry[2]}:{version}"

    def close(self):
        with self._lock:
            while self._entries:
                self._entries.popitem()[1][1].close()


CONTEXT_COMMIT_VERSIONS = ContextCommitVersions()
atexit.register(CONTEXT_COMMIT_VERSIONS.close)


def activity_revision(directory: Path, chat_id: str) -> str:
    index = directory / "index.sqlite3"
    if not index.is_file():
        return "absent"
    # Resolve through the index rather than deriving a tree filename from input.
    conn = sqlite3.connect(index.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT database_path FROM context_tree_index WHERE tree_id = ?", (chat_id,),
        ).fetchone()
    finally:
        conn.close()
    return CONTEXT_COMMIT_VERSIONS.read(directory / row[0]) if row else "absent"


def chat_sync_revision(context, chat_id: str) -> str:
    state = context.service.repository.sync_state(chat_id)
    if state is None:
        return ""  # Let the normal detail route produce the existing 404 response.
    metadata, stored_revision, parent_exists = state
    activity = activity_revision(context.conversation_context.agent_states.context_directory, chat_id)
    # public_chat_light includes live run status and effective composer settings,
    # which can change without a write to the conversation row.
    presentation = context.service.public_chat_light(metadata)
    encoded = json.dumps([stored_revision, metadata, parent_exists, presentation, activity],
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
