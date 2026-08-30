"""Shared startup and shutdown primitives for every Cyrene host."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from cyrene.config import (
    DATA_DIR,
    DB_PATH,
    INBOX_DIR,
    STORE_DIR,
    TEMP_DIR,
    WORKSPACE_DIR,
)
from cyrene.platform.context import HostMode, RuntimeContext
from cyrene.platform.database import init_db
from cyrene.platform.inbox import ensure_inbox
from cyrene.platform.paths import PATHS, cleanup_temporary_artifacts
from cyrene.platform.task_lifecycle import cancel_and_wait

logger = logging.getLogger(__name__)


def create_runtime_context(*, host_mode: HostMode = "unknown") -> RuntimeContext:
    """Build a context snapshot from the current runtime configuration."""
    paths = replace(
        PATHS,
        workspace=WORKSPACE_DIR,
        store=STORE_DIR,
        data=DATA_DIR,
        temp=TEMP_DIR,
    )
    return RuntimeContext(
        paths=paths,
        database_path=DB_PATH,
        inbox_path=INBOX_DIR,
        host_mode=host_mode,
    )


async def initialize_runtime(
    *,
    context: RuntimeContext | None = None,
    events: bool = False,
    include_temp: bool = False,
    clean_temp: bool = False,
) -> RuntimeContext:
    """Create runtime state shared by CLI, web, Electron, and bot hosts."""
    context = context or create_runtime_context()
    async with context.lifecycle_lock():
        if context.closed:
            raise RuntimeError("Cannot initialize a closed RuntimeContext")

        directories = [
            context.paths.workspace,
            context.paths.store,
            context.paths.data,
            context.inbox_path,
        ]
        if include_temp:
            directories.append(context.paths.temp)
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        if clean_temp and "temp_cleanup" not in context.initialized_components:
            cleanup_temporary_artifacts(context.paths.temp)
            context.initialized_components.add("temp_cleanup")

        if "core" not in context.initialized_components:
            await init_db(str(context.database_path))
            ensure_inbox("cyrene")
            from cyrene.platform.host_actions import reconcile_startup
            reconcile_startup()
            context.initialized_components.add("core")

        if events and "events" not in context.initialized_components:
            from cyrene.observability.debug import enable_event_bus

            enable_event_bus()
            context.initialized_components.add("events")
    return context


def start_update_check() -> asyncio.Task[Any] | None:
    """Start the optional update check and return its owned task."""
    try:
        from cyrene.platform.updater import background_check

        return asyncio.create_task(background_check())
    except Exception:
        logger.debug("Unable to start background update check", exc_info=True)
        return None


async def stop_runtime_tasks(*tasks: asyncio.Task[Any] | None) -> None:
    """Cancel and await host-owned background tasks before closing its loop."""
    await cancel_and_wait(task for task in tasks if task is not None)


__all__ = [
    "create_runtime_context",
    "initialize_runtime",
    "start_update_check",
    "stop_runtime_tasks",
]
