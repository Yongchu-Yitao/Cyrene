"""Application service for deleting Cyrene-owned data and runtime state."""

from __future__ import annotations

import importlib
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
    from agent.plugin.mcp_service import get_mcp_service
    from cyrene.model_runtime.codex_provider import get_codex_provider
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

    await get_mcp_service().shutdown()
    await get_codex_provider().close()


class DataResetApplicationService:
    """Coordinate a complete reset against the configured application database."""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)

    async def reset_app_data(self) -> dict[str, Any]:
        from agent.plugin import active_plugin_application_host
        from cyrene.browser import clear_browser_data
        from cyrene.config import CACHE_DIR, STORE_DIR, write_env_keys
        from cyrene.runtime.database import init_db
        from cyrene.runtime.inbox import clear_all_inboxes
        from cyrene.runtime.onboarding import get_onboarding_status, reset_onboarding_state
        from cyrene.runtime.settings_store import reset_all as reset_web_settings
        from cyrene.workbench import presentation_runtime

        await reset_process_runtime_state(self.db_path)
        browser_result = await clear_browser_data()
        plugin_host = active_plugin_application_host()
        knowledge_service = (
            plugin_host.service("knowledge") if plugin_host is not None else None
        )
        memory_service = (
            plugin_host.service("memory") if plugin_host is not None else None
        )
        if knowledge_service is not None:
            await knowledge_service.delete_all_local_models()
            await knowledge_service.reset_data()
        importlib.import_module("cyrene.runtime.scheduler").reset_lottery()
        await clear_all_inboxes()

        remove_directory_children(
            DATA_DIR, preserve=frozenset({"config.enc", ".config_key"})
        )
        reset_web_settings()
        reset_onboarding_state()
        remove_directory_children(STORE_DIR)

        cyrene_root = cyrene_dir(WORKSPACE_DIR)
        for path in (
            cyrene_root / "patterns",
            cyrene_root / "plan",
            cyrene_root / "projects",
            cyrene_root / "scratch",
            BASE_DIR / "backups",
            CACHE_DIR / "voice",
        ):
            remove_path_checked(path)
        db_path = Path(self.db_path or str(DB_PATH))
        remove_path_checked(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        await init_db(str(db_path))
        from cyrene.workbench.chat_runs import startup_chat_runs

        startup_chat_runs(str(db_path))
        if knowledge_service is not None:
            await knowledge_service.startup()
        if memory_service is not None:
            await memory_service.reset_data()

        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

        write_env_keys(
            {
                "TELEGRAM_BOT_TOKEN": "",
                "WECHAT_BOT_TOKEN": "",
                "WECHAT_OWNER_ID": "",
                "AMAP_API_KEY": "",
            }
        )
        return {
            "ok": True,
            "cleared": {
                "settings": True,
                "local_models": True,
                "browser_logins": bool(browser_result.get("ok")),
                "runtime_state": True,
            },
            "onboarding": get_onboarding_status(),
            "sessions": presentation_runtime.build_sessions(db_path),
        }


__all__ = [
    "DataResetApplicationService",
]
