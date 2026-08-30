"""One lifecycle coordinator shared by CLI, Web, Electron, and GUI hosts."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from cyrene.platform import bootstrap
from cyrene.platform.context import RuntimeContext

logger = logging.getLogger(__name__)


class ApplicationLifecycle:
    """Coordinate startup and idempotent shutdown for one host context."""

    def __init__(self, context: RuntimeContext):
        self.context = context

    async def initialize(
        self,
        *,
        events: bool = False,
        include_temp: bool = False,
        clean_temp: bool = False,
    ) -> RuntimeContext:
        return await bootstrap.initialize_runtime(
            context=self.context,
            events=events,
            include_temp=include_temp,
            clean_temp=clean_temp,
        )

    def create_task(
        self,
        awaitable: Awaitable[Any],
        *,
        label: str,
    ):
        return self.context.create_task(awaitable, logger=logger, label=label)

    def start_update_check(self):
        task = bootstrap.start_update_check()
        if task is not None:
            self.context.own_task(task, logger=logger, label="update check")
        return task

    def register_manager(
        self,
        name: str,
        value: Any,
        *,
        close: Callable[[], Any] | None,
    ) -> Any:
        return self.context.register_manager(name, value, close=close)

    async def shutdown(self) -> None:
        """Stop all resources while the owning event loop is still alive."""
        context = self.context
        async with context.lifecycle_lock():
            if context.closed:
                return
            context.begin_shutdown()

            from cyrene.platform.lifecycle import shutdown_background_work

            await shutdown_background_work()
            await context.cancel_background_tasks()
            await context.close_managers()
            context.mark_closed()


__all__ = ["ApplicationLifecycle"]
