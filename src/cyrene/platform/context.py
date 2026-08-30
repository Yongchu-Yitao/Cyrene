"""Application-level runtime ownership and immutable startup configuration.

``RuntimeContext`` owns resources whose lifetime matches one Cyrene host.  It
does not own per-session agent state, per-run ContextVars, tool-call context, or
persisted Workbench domain state.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from cyrene.platform.paths import AppPaths
from cyrene.platform.task_lifecycle import cancel_and_wait, track_task

HostMode = Literal["cli", "web", "electron", "gui", "bot", "test", "unknown"]
ResourceCloser = Callable[[], Any]


@dataclass(slots=True)
class _ManagedResource:
    value: Any
    close: ResourceCloser | None


@dataclass(slots=True)
class RuntimeContext:
    """Own application resources for exactly one running host."""

    paths: AppPaths
    database_path: Path
    inbox_path: Path
    host_mode: HostMode = "unknown"
    background_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    initialized_components: set[str] = field(default_factory=set)
    accepting_work: bool = True
    closed: bool = False
    _resources: dict[str, _ManagedResource] = field(default_factory=dict)
    _lifecycle_lock: asyncio.Lock | None = None
    _lifecycle_loop: asyncio.AbstractEventLoop | None = None

    def lifecycle_lock(self) -> asyncio.Lock:
        """Return the lifecycle lock bound to the active event loop."""
        loop = asyncio.get_running_loop()
        if self._lifecycle_lock is None or self._lifecycle_loop is not loop:
            if self._lifecycle_lock is not None and self._lifecycle_lock.locked():
                raise RuntimeError("RuntimeContext cannot move across event loops while lifecycle work is active")
            self._lifecycle_lock = asyncio.Lock()
            self._lifecycle_loop = loop
        return self._lifecycle_lock

    def create_task(
        self,
        awaitable: Awaitable[Any],
        *,
        logger: logging.Logger | None = None,
        label: str = "runtime background task",
    ) -> asyncio.Task[Any]:
        """Create and own a host-level task until completion or shutdown."""
        if not self.accepting_work or self.closed:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise RuntimeError("RuntimeContext is shutting down and cannot accept new work")
        task = asyncio.create_task(awaitable)
        return track_task(task, self.background_tasks, logger=logger, label=label)

    def own_task(
        self,
        task: asyncio.Task[Any],
        *,
        logger: logging.Logger | None = None,
        label: str = "runtime background task",
    ) -> asyncio.Task[Any]:
        """Adopt a task created by a compatibility API."""
        if not self.accepting_work or self.closed:
            task.cancel()
            raise RuntimeError("RuntimeContext is shutting down and cannot accept new work")
        return track_task(task, self.background_tasks, logger=logger, label=label)

    def register_manager(
        self,
        name: str,
        value: Any,
        *,
        close: ResourceCloser | None,
    ) -> Any:
        """Register one named application-level resource and its close action."""
        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("Runtime manager name must be non-empty")
        if self.closed:
            raise RuntimeError("Cannot register a manager on a closed RuntimeContext")
        existing = self._resources.get(normalized)
        if existing is not None and existing.value is not value:
            raise RuntimeError(f"Runtime manager {normalized!r} is already registered")
        self._resources[normalized] = _ManagedResource(value=value, close=close)
        return value

    def get_manager(self, name: str) -> Any | None:
        resource = self._resources.get(name)
        return resource.value if resource is not None else None

    async def cancel_background_tasks(self) -> None:
        """Cancel host-owned tasks before the event loop closes."""
        await cancel_and_wait(self.background_tasks)
        self.background_tasks.clear()

    async def close_managers(self) -> None:
        """Close managers once, in reverse registration order."""
        resources = list(reversed(self._resources.items()))
        self._resources.clear()
        for _name, resource in resources:
            if resource.close is None:
                continue
            result = resource.close()
            if inspect.isawaitable(result):
                await result

    def begin_shutdown(self) -> None:
        self.accepting_work = False

    def mark_closed(self) -> None:
        self.accepting_work = False
        self.closed = True


__all__ = [
    "HostMode",
    "RuntimeContext",
]
