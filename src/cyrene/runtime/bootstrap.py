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
    SEARXNG_AUTO_START,
    SEARXNG_HOST,
    SEARXNG_PORT,
    STORE_DIR,
    TEMP_DIR,
    WORKSPACE_DIR,
)
from cyrene.runtime.context import HostMode, RuntimeConfigSnapshot, RuntimeContext
from cyrene.runtime.database import init_db
from cyrene.runtime.inbox import ensure_inbox
from cyrene.runtime.paths import PATHS, cleanup_temporary_artifacts
from cyrene.runtime.task_lifecycle import cancel_and_wait

logger = logging.getLogger(__name__)


def _native_mcp_service():
    """Return the application-published MCP service or its native singleton."""

    from agent.plugin import active_plugin_service
    from agent.plugin.mcp_service import get_mcp_service

    return active_plugin_service("mcp") or get_mcp_service()


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
        config=RuntimeConfigSnapshot(
            searxng_auto_start=bool(SEARXNG_AUTO_START),
            searxng_host=str(SEARXNG_HOST),
            searxng_port=int(SEARXNG_PORT),
        ),
        host_mode=host_mode,
    )


async def initialize_runtime(
    *,
    context: RuntimeContext | None = None,
    events: bool = False,
    learning: bool = False,
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
            from cyrene.runtime.host_actions import reconcile_startup
            reconcile_startup()
            context.initialized_components.add("core")

        if events and "events" not in context.initialized_components:
            from cyrene.observability.debug import enable_event_bus

            enable_event_bus()
            context.initialized_components.add("events")
        if learning and "learning" not in context.initialized_components:
            from cyrene.learning.orchestrator import init as initialize_learning

            await initialize_learning(context.paths.data, context.paths.workspace)
            context.initialized_components.add("learning")
            logger.info("Behavior learning initialized")
    return context


async def start_external_services(
    *,
    context: RuntimeContext | None = None,
    search: bool = True,
    mcp: bool = True,
) -> RuntimeContext:
    """Start optional local services without failing the host."""
    context = context or create_runtime_context()
    async with context.lifecycle_lock():
        if context.closed:
            raise RuntimeError("Cannot start services on a closed RuntimeContext")
        if (
            search
            and context.config.searxng_auto_start
            and "search" not in context.started_services
        ):
            from agent.plugin import active_plugin_service

            service = active_plugin_service("web_search")
            startup = getattr(service, "startup", None)
            if callable(startup):
                try:
                    url = await startup(
                        context.config.searxng_port,
                        context.config.searxng_host,
                    )
                    context.started_services.add("search")
                    if url:
                        logger.info("Plugin search service started at %s", url)
                except Exception as exc:
                    logger.warning("Plugin search service start failed: %s", exc)
            else:
                logger.debug(
                    "Search startup deferred until the content Plugin host starts"
                )

        if mcp and "mcp" not in context.started_services:
            try:
                await _native_mcp_service().startup()
                context.started_services.add("mcp")
                logger.info("MCP Plugin service started")
            except Exception as exc:
                logger.warning("MCP Plugin service start failed: %s", exc)

    return context


def stop_external_services(
    *,
    context: RuntimeContext | None = None,
    search: bool = True,
    mcp: bool = True,
) -> None:
    """Stop the selected local services and source watchers."""
    if search and (context is None or "search" in context.started_services):
        from agent.plugin import active_plugin_service

        service = active_plugin_service("web_search")
        shutdown = getattr(service, "shutdown", None)
        if callable(shutdown):
            shutdown()
        if context is not None:
            context.started_services.discard("search")
        logger.info("Stopped search service")
    if mcp and (context is None or "mcp" in context.started_services):
        _native_mcp_service().shutdown_sync()
        if context is not None:
            context.started_services.discard("mcp")
        logger.info("Stopped MCP Plugin service")


async def stop_external_services_async(
    *,
    context: RuntimeContext | None = None,
    search: bool = True,
    mcp: bool = True,
) -> None:
    """Stop local services while the application loop is still alive."""
    if search and (context is None or "search" in context.started_services):
        from agent.plugin import active_plugin_service

        service = active_plugin_service("web_search")
        shutdown = getattr(service, "shutdown", None)
        if callable(shutdown):
            result = shutdown()
            if asyncio.iscoroutine(result):
                await result
        if context is not None:
            context.started_services.discard("search")
        logger.info("Stopped search service")
    if mcp and (context is None or "mcp" in context.started_services):
        await _native_mcp_service().shutdown()
        if context is not None:
            context.started_services.discard("mcp")
        logger.info("Stopped MCP Plugin service")


def start_update_check() -> asyncio.Task[Any] | None:
    """Start the optional update check and return its owned task."""
    try:
        from cyrene.runtime.updater import background_check

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
    "start_external_services",
    "start_update_check",
    "stop_external_services",
    "stop_external_services_async",
    "stop_runtime_tasks",
]
