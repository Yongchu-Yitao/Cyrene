"""Application routes and worker lifecycle for the media Plugin pack."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from typing import Any

from cyrene.plugins.context import PluginApplicationContext


def _migrate_legacy_database(context: PluginApplicationContext) -> Path:
    """Move the pre-Plugin queue into the Plugin-owned data root."""

    plugin_root = context.data_directory / "plugin_data" / "cyrene_media"
    plugin_root.mkdir(parents=True, exist_ok=True)
    destination = plugin_root / "media_jobs.sqlite3"
    legacy = context.data_directory / "media_jobs.sqlite3"
    if not legacy.is_file() or legacy.resolve() == destination.resolve():
        return destination

    temporary = destination.with_suffix(".sqlite3.migrating")
    try:
        temporary.unlink(missing_ok=True)
        # The backup API includes committed WAL contents and safely handles a
        # legacy archive restored over an installation with Plugin data.
        with sqlite3.connect(legacy) as source, sqlite3.connect(temporary) as target:
            source.backup(target)
        os.replace(temporary, destination)
        legacy.unlink(missing_ok=True)
        legacy.with_name(f"{legacy.name}-wal").unlink(missing_ok=True)
        legacy.with_name(f"{legacy.name}-shm").unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def setup_application(context: PluginApplicationContext) -> None:
    from .daemon import MediaDaemon
    from .manager import MediaJobManager
    from .routes import register_media_routes
    from .settings_routes import register_media_settings_routes
    from .wake import MediaWakeBridge

    manager = MediaJobManager(_migrate_legacy_database(context))
    daemon = MediaDaemon(manager)
    wake_bridge = MediaWakeBridge(manager)
    register_media_routes(context.router, manager=manager, daemon=daemon)
    register_media_settings_routes(context.router)
    context.provide("media", manager)
    context.provide("media_daemon", daemon)
    context.provide("media_wake", wake_bridge)
    from cyrene.runtime.settings_service import (
        PluginSettingsContribution,
        SettingControlSpec,
    )

    context.provide(
        "media_settings_schema",
        PluginSettingsContribution(controls=(
            SettingControlSpec("media.generation", "media", "current_ui", "cyrene.ui.inspect", "R2"),
        )),
    )
    context.expose_frontend("media")

    async def dispatch(wake: dict[str, Any]) -> str:
        host = getattr(context.app.state, "plugin_application_host", None)
        chat = host.service("workbench_chat") if host is not None else None
        if chat is None:
            return "error"
        return await chat.service.dispatch_media_wake_run(
            wake,
            bot=context.bot,
            db_path=context.db_path,
        )

    async def start() -> None:
        host = getattr(context.app.state, "plugin_application_host", None)
        chat = host.service("workbench_chat") if host is not None else None
        if chat is None:
            raise RuntimeError("Media Plugin requires the Workbench chat service")
        wake_bridge.configure(
            dispatcher=dispatch,
            is_busy=lambda chat_id: (
                chat.run_manager.get(str(chat_id)) is not None
            ),
        )
        await daemon.start()
        await wake_bridge.start()

    async def stop() -> None:
        await wake_bridge.stop()
        await daemon.stop()

    context.on_startup(start)
    context.on_shutdown(stop)


__all__ = ["setup_application"]
