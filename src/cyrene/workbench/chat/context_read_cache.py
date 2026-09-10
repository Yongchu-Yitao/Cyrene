"""Bounded, commit-aware cache for read-only ContextTree projections.

Each entry keeps an idle SQLite connection solely for PRAGMA data_version.
Unlike mtime or a TTL, this detects commits from other connections, including
WAL writes. No read transaction is retained between requests.
"""
from __future__ import annotations

import atexit
import pickle
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path


class ContextReadCache:
    def __init__(self, max_entries=256, max_bytes=16 * 1024 * 1024):
        self._entries = OrderedDict()
        self._lock = threading.RLock()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0

    def _discard(self, key):
        entry = self._entries.pop(key)
        entry[1].close()
        self._bytes -= len(entry[3])

    def read(self, path: Path, load, owner, *, variant="full"):
        # Also coalesce simultaneous identical reads. The lock only covers
        # context projection, never a running Agent or a database writer.
        with self._lock:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            key = (str(path.resolve()), variant)
            entry = self._entries.get(key)
            if entry is not None and entry[0] != identity:
                self._discard(key)
                entry = None
            if entry is None:
                conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True,
                                       isolation_level=None, check_same_thread=False)
                entry = [identity, conn, None, b'', {}]
                self._entries[key] = entry
            self._entries.move_to_end(key)
            conn = entry[1]
            before = conn.execute('PRAGMA data_version').fetchone()[0]
            if entry[2] == before and entry[3] and all(owner(k) == v for k, v in entry[4].items()):
                return pickle.loads(entry[3])
            dependencies = {}

            def record(name):
                value = owner(name)
                dependencies[name] = value
                return value

            try:
                result = load(record)
                after = conn.execute('PRAGMA data_version').fetchone()[0]
                # A concurrent write makes this result uncacheable; the normal
                # read still returns, and the next request projects fresh data.
                payload = pickle.dumps(result, protocol=5) if before == after else b''
                self._bytes -= len(entry[3])
                entry[2:] = [after, payload, dependencies]
                self._bytes += len(payload)
                while self._entries and (len(self._entries) > self._max_entries or self._bytes > self._max_bytes):
                    self._discard(next(iter(self._entries)))
                return result
            except BaseException:
                self._discard(key)
                raise

    def close(self):
        with self._lock:
            while self._entries:
                self._discard(next(iter(self._entries)))


CONTEXT_READ_CACHE = ContextReadCache()
atexit.register(CONTEXT_READ_CACHE.close)
