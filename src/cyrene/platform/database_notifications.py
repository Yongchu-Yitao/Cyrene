"""Wake durable queue owners after a local commit, including worker-thread writes."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
import threading

_lock = threading.Lock()
_listeners: dict[Path, set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}


def notify_database_commit(path: Path) -> None:
    with _lock:
        listeners = tuple(_listeners.get(Path(path).resolve(), ()))
    for loop, changed in listeners:
        try:
            loop.call_soon_threadsafe(changed.set)
        except RuntimeError:
            pass  # The owner loop is already shutting down.


@contextmanager
def database_changes(path: Path):
    key = Path(path).resolve()
    entry = (asyncio.get_running_loop(), asyncio.Event())
    with _lock:
        _listeners.setdefault(key, set()).add(entry)
    try:
        yield entry[1]
    finally:
        with _lock:
            _listeners[key].discard(entry)
            if not _listeners[key]:
                del _listeners[key]


async def wait_for_database_change(changed: asyncio.Event, *, timeout: float = 60) -> None:
    try:
        await asyncio.wait_for(changed.wait(), timeout)
    except TimeoutError:
        pass
