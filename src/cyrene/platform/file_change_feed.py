"""Shared native file notifications with subscriptions armed before state reads."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from watchfiles import awatch


class FileChangeFeed:
    def __init__(self, paths: list[Path], *, keepalive: Callable | None = None) -> None:
        self.paths = {str(path.resolve()) for path in paths}
        self.keepalive = keepalive
        self._listeners: set[asyncio.Event] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._anchor = None

    async def _watch(self) -> None:
        async for _ in awatch(
            *{str(Path(path).parent) for path in self.paths},
            recursive=False, debounce=20, step=5,
            watch_filter=lambda _, path: str(Path(path).resolve()) in self.paths,
        ):
            for listener in self._listeners:
                listener.set()

    @asynccontextmanager
    async def listen(self):
        changed = asyncio.Event()
        async with self._lock:
            self._listeners.add(changed)
            try:
                if self._task is None and self.paths:
                    for path in self.paths:
                        Path(path).parent.mkdir(parents=True, exist_ok=True)
                    if self.keepalive:
                        self._anchor = await asyncio.to_thread(self.keepalive)
                    self._task = asyncio.create_task(self._watch())
                    # awatch constructs its native watcher before its first
                    # thread await. Arm it before the caller reads durable state.
                    await asyncio.sleep(0)
                    if self._task.done():
                        self._task.result()
            except BaseException:
                self._listeners.discard(changed)
                if self._anchor:
                    await asyncio.to_thread(self._anchor.close)
                    self._anchor = None
                self._task = None
                raise
        try:
            yield changed
        finally:
            async with self._lock:
                self._listeners.discard(changed)
                if not self._listeners:
                    if self._task:
                        self._task.cancel()
                        await asyncio.gather(self._task, return_exceptions=True)
                        self._task = None
                    if self._anchor:
                        await asyncio.to_thread(self._anchor.close)
                        self._anchor = None

    async def wait(self, changed: asyncio.Event, *, timeout: float | None = None, stop: asyncio.Event | None = None) -> None:
        waits = [asyncio.create_task(changed.wait())]
        if stop is not None:
            waits.append(asyncio.create_task(stop.wait()))
        targets = [*waits, *([self._task] if self._task else [])]
        try:
            done, _ = await asyncio.wait(targets, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if self._task in done:
                self._task.result()  # surface a failed watcher, never silently stall
        finally:
            for task in waits:
                task.cancel()
            await asyncio.gather(*waits, return_exceptions=True)
