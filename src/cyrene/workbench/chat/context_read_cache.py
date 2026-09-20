"""Bounded, commit-aware cache with per-key loading and leased connections.

SQLite data_version observes WAL commits; each caller receives an isolated value.
The LRU lock protects metadata only, never a loader or pickle conversion.
"""
from __future__ import annotations

import atexit
import pickle
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class _Entry:
    identity: tuple
    connection: sqlite3.Connection
    version: int | None = None
    payload: bytes = b''
    dependencies: dict = field(default_factory=dict)
    leases: int = 0
    retired: bool = False


class ContextReadCache:
    def __init__(self, max_entries=256, max_bytes=16 * 1024 * 1024):
        self._entries = OrderedDict()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._gates = {}
        self._active = 0
        self._closing = 0
        self._local = threading.local()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0

    def _discard(self, key):
        entry = self._entries.pop(key)
        self._bytes -= len(entry.payload)
        entry.retired = True
        if not entry.leases:
            entry.connection.close()

    def read(self, path: Path, load, owner, *, variant="full"):
        # A loader may read another tree. Do not acquire a second key gate:
        # two such loaders could otherwise wait on each other in reverse order.
        if getattr(self._local, 'depth', 0):
            return load(owner)
        key = (str(path.resolve()), variant)
        with self._condition:
            while self._closing and not getattr(self._local, 'depth', 0):
                self._condition.wait()
            gate = self._gates.setdefault(key, [threading.RLock(), 0])
            gate[1] += 1
            self._active += 1
        self._local.depth = getattr(self._local, 'depth', 0) + 1
        try:
            # Same-key readers still coalesce; unrelated trees never wait on load.
            with gate[0]:
                return self._read_key(path, key, load, owner)
        finally:
            self._local.depth -= 1
            with self._condition:
                gate[1] -= 1
                if not gate[1]:
                    del self._gates[key]
                self._active -= 1
                self._condition.notify_all()

    def _read_key(self, path, key, load, owner):
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.identity != identity:
                self._discard(key)
                entry = None
            if entry is None:
                conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True,
                                       isolation_level=None, check_same_thread=False)
                entry = _Entry(identity, conn)
                self._entries[key] = entry
            self._entries.move_to_end(key)
            entry.leases += 1
            while len(self._entries) > self._max_entries and self._entries:
                self._discard(next(iter(self._entries)))
        try:
            conn = entry.connection
            before = conn.execute('PRAGMA data_version').fetchone()[0]
            if entry.version == before and entry.payload and all(owner(k) == v for k, v in entry.dependencies.items()):
                return pickle.loads(entry.payload)
            dependencies = {}

            def record(name):
                value = owner(name)
                dependencies[name] = value
                return value

            result = load(record)
            after = conn.execute('PRAGMA data_version').fetchone()[0]
            payload = pickle.dumps(result, protocol=5) if before == after else b''
            with self._lock:
                # An LRU eviction can detach an in-flight entry. Its lease keeps
                # the connection alive but does not resurrect its cache slot.
                if not entry.retired:
                    self._bytes -= len(entry.payload)
                    entry.version, entry.payload, entry.dependencies = after, payload, dependencies
                    self._bytes += len(payload)
                    while self._entries and (len(self._entries) > self._max_entries or self._bytes > self._max_bytes):
                        self._discard(next(iter(self._entries)))
            return result
        except BaseException:
            with self._lock:
                if self._entries.get(key) is entry:
                    self._discard(key)
            raise
        finally:
            with self._lock:
                entry.leases -= 1
                if entry.retired and not entry.leases:
                    entry.connection.close()

    def close(self):
        with self._condition:
            self._closing += 1
            try:
                # Reentrant close retires entries; leases release their handles
                # when the current reads finish, without waiting on this caller.
                while self._active and not getattr(self._local, 'depth', 0):
                    self._condition.wait()
                while self._entries:
                    self._discard(next(iter(self._entries)))
            finally:
                self._closing -= 1
                self._condition.notify_all()


CONTEXT_READ_CACHE = ContextReadCache()
atexit.register(CONTEXT_READ_CACHE.close)
