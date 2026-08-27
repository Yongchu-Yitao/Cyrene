"""Application service for deleting Cyrene-owned data and runtime state."""

from __future__ import annotations

import importlib
import inspect
import shutil
from pathlib import Path
from typing import Any

from cyrene.config import (
    BASE_DIR,
    DATA_DIR,
    DB_PATH,
    WORKSPACE_DIR,
    cyrene_dir,
)


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def remove_path_checked(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def remove_directory_children(
    root: Path, *, preserve: frozenset[str] = frozenset()
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for child in list(root.iterdir()):
        if child.name not in preserve:
            remove_path_checked(child)


async def reset_process_runtime_state(db_path: str = "") -> None:
    from cyrene.runtime.shell_wake import get_shell_wake_service
    from cyrene.workbench.chat_runs import get_chat_run_manager, shutdown_chat_runs
    from cyrene.workbench.task_runs import shutdown_task_runs

    goal_loop = importlib.import_module("cyrene.workbench.goal_loop")
    manager = get_chat_run_manager()
    for chat_id in list(manager.runs):
        await manager.terminate(chat_id, termination_reason="application_data_reset")
    await shutdown_chat_runs()
    manager.runs.clear()
    await shutdown_task_runs(str(db_path or DB_PATH))

    for goal_manager in list(goal_loop._MANAGERS.values()):
        await goal_manager.shutdown()
        goal_manager.closed = False
    get_shell_wake_service().clear_pending()



async def prepare_plugin_data_reset(plugin_host: Any | None) -> dict[str, bool]:
    """Ask installed application Plugins to clear state outside core roots."""

    cleared: dict[str, bool] = {}
    seen: set[int] = set()
    if plugin_host is None:
        services = ()
    else:
        # A full reset clears every installed application contribution,
        # including packs that are currently disabled. Disabled packs do not
        # run lifecycle work, but their persisted credentials/data must not
        # survive a reset and unexpectedly return when re-enabled.
        services = plugin_host.services.values()
    for service in services:
        if id(service) in seen:
            continue
        seen.add(id(service))
        callback = getattr(service, "prepare_data_reset", None)
        if not callable(callback):
            continue
        result = callback()
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            cleared.update({
                str(key): bool(value)
                for key, value in result.items()
                if str(key).strip()
            })
    return cleared


class DataResetApplicationService:
    """Coordinate a complete reset against the configured application database."""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)

    async def reset_app_data(self) -> dict[str, Any]:
        from agent.plugin import active_plugin_application_host
        from cyrene.config import CACHE_DIR, STORE_DIR
        from cyrene.runtime.database import init_db
        from cyrene.runtime.inbox import clear_all_inboxes
        from cyrene.runtime.onboarding import get_onboarding_status, reset_onboarding_state
        from cyrene.runtime import settings_store
        from cyrene.workbench import presentation_runtime

        await reset_process_runtime_state(self.db_path)
        plugin_host = active_plugin_application_host()
        plugin_cleared = await prepare_plugin_data_reset(plugin_host)
        if plugin_host is not None:
            await plugin_host.shutdown()
        await clear_all_inboxes()

        remove_directory_children(
            DATA_DIR, preserve=frozenset({"config.enc", ".config_key"})
        )
        settings_store.reset_all()
        reset_onboarding_state()
        remove_directory_children(STORE_DIR)
        # CACHE_DIR is an application-scoped disposable root. Clearing it as a
        # unit also covers data owned by an installed but currently disabled
        # Plugin, whose application service is intentionally not constructed.
        remove_directory_children(CACHE_DIR)

        cyrene_root = cyrene_dir(WORKSPACE_DIR)
        for path in (
            cyrene_root / "patterns",
            cyrene_root / "plan",
            cyrene_root / "projects",
            cyrene_root / "scratch",
            BASE_DIR / "backups",
        ):
            remove_path_checked(path)
        db_path = Path(self.db_path or str(DB_PATH))
        remove_path_checked(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        await init_db(str(db_path))
        from cyrene.workbench.chat_runs import startup_chat_runs

        startup_chat_runs(str(db_path))
        if plugin_host is not None:
            plugin_host.registry.configure_customizations(
                settings_store.get("plugin_tool_customizations", {}) or {}
            )
            await plugin_host.reload_user_plugins()
            plugin_host.registry.configure_activation(
                plugins=settings_store.get_enabled_plugins(),
                packs=settings_store.get_enabled_plugin_packs(),
            )
            await plugin_host.startup()

        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

        return {
            "ok": True,
            "cleared": {
                "settings": True,
                **plugin_cleared,
                "runtime_state": True,
            },
            "onboarding": get_onboarding_status(),
            "sessions": presentation_runtime.build_sessions(db_path),
        }


__all__ = [
    "DataResetApplicationService",
]
