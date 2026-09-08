"""Application-owned HTTP clients, leased and closed on their owning loop."""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Callable, Hashable

import httpx

logger = logging.getLogger(__name__)
MODEL_KEEPALIVE_SECONDS = 60.0
MODEL_HTTP_LIMITS = httpx.Limits(
    max_connections=16, max_keepalive_connections=4,
    keepalive_expiry=MODEL_KEEPALIVE_SECONDS,
)


@dataclass(eq=False)
class _Entry:
    scope: Hashable
    key: Hashable
    client: httpx.AsyncClient
    users: int = 0
    timer: asyncio.TimerHandle | None = None
    retired: bool = False
    closing: bool = False


class _LoopClients:
    def __init__(self, idle_seconds: float, capacity: int):
        self.idle_seconds = idle_seconds
        self.capacity = capacity
        self.entries: list[_Entry] = []
        self.closing: set[asyncio.Task] = set()
        self.closed = False

    def retire(self, entry: _Entry) -> None:
        entry.retired = True
        if entry.timer is not None:
            entry.timer.cancel()
            entry.timer = None
        if entry.users == 0 and not entry.closing:
            entry.closing = True
            if entry in self.entries:
                self.entries.remove(entry)
            task = asyncio.create_task(self.close_client(entry.client))
            self.closing.add(task)
            task.add_done_callback(self.closing.discard)

    @staticmethod
    async def close_client(client: httpx.AsyncClient) -> None:
        try:
            await client.aclose()
        except Exception:
            logger.warning("Failed to close a model HTTP client", exc_info=True)

    @asynccontextmanager
    async def lease(self, scope: Hashable, key: Hashable, factory: Callable):
        if self.closed:
            raise RuntimeError("Model HTTP client pool is closed")
        for old in tuple(self.entries):
            if old.scope == scope and old.key != key and not old.retired:
                self.retire(old)
        entry = next((e for e in self.entries
                      if e.scope == scope and e.key == key and not e.retired
                      and not e.client.is_closed), None)
        if entry is None:
            # Evict an idle client first. If all clients are leased, use a
            # temporary client rather than interrupting another request.
            if len(self.entries) >= self.capacity:
                idle = next((e for e in self.entries if e.users == 0), None)
                if idle is not None:
                    self.retire(idle)
            entry = _Entry(scope, key, factory())
            entry.retired = len(self.entries) >= self.capacity
            self.entries.append(entry)
        if entry.timer is not None:
            entry.timer.cancel()
            entry.timer = None
        entry.users += 1
        try:
            yield entry.client
        finally:
            entry.users -= 1
            if entry.users == 0:
                if entry.retired or self.closed:
                    self.retire(entry)
                else:
                    entry.timer = asyncio.get_running_loop().call_later(
                        self.idle_seconds, self.retire, entry,
                    )

    async def close(self) -> None:
        self.closed = True
        entries, self.entries = self.entries, []
        for entry in entries:
            if entry.timer is not None:
                entry.timer.cancel()
                entry.timer = None
            entry.retired = True
            entry.closing = True
        await asyncio.gather(*(self.close_client(e.client) for e in entries))
        if self.closing:
            await asyncio.gather(*tuple(self.closing))


class ModelHttpClients:
    """One service per application; a separate pool for every event loop.

    No requests are retried here. HTTPX handles expired keep-alive sockets;
    ambiguous failures after sending remain the provider's responsibility.
    """

    def __init__(self, *, idle_seconds: float = MODEL_KEEPALIVE_SECONDS,
                 capacity: int = 16):
        self.idle_seconds = idle_seconds
        self.capacity = capacity
        self._lock = threading.Lock()
        self._loops: dict[asyncio.AbstractEventLoop, _LoopClients] = {}
        self._closed = False

    @asynccontextmanager
    async def lease(self, scope: Hashable, key: Hashable, factory: Callable):
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._closed:
                raise RuntimeError("Model HTTP client pool is closed")
            pool = self._loops.get(loop)
            if pool is None:
                pool = self._loops[loop] = _LoopClients(self.idle_seconds, self.capacity)
        async with pool.lease(scope, key, factory) as client:
            yield client

    async def aclose(self) -> None:
        current = asyncio.get_running_loop()
        with self._lock:
            self._closed = True
            loops, self._loops = self._loops, {}
        pending = []
        for loop, pool in loops.items():
            if loop is current:
                pending.append(pool.close())
            elif loop.is_running():
                pending.append(asyncio.wrap_future(
                    asyncio.run_coroutine_threadsafe(pool.close(), loop),
                ))
            else:
                # Never move a transport to a different loop during teardown.
                # Hosts must close this service before stopping worker loops.
                logger.warning("Model HTTP owner loop stopped before pool shutdown")
        if pending:
            await asyncio.gather(*pending)
