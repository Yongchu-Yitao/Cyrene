"""Application service for deleting Cyrene-owned data and runtime state."""

from __future__ import annotations

import asyncio
import importlib
import shutil
from pathlib import Path
from typing import Any

import cyrene.agent.state as agent_state
from cyrene.config import (
    BASE_DIR,
    DATA_DIR,
    DB_PATH,
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    WORKSPACE_DIR,
    cyrene_dir,
)
from cyrene.runtime.memory.conversations import CONVERSATIONS_DIR


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


def reset_legacy_workspace_root_leftovers() -> None:
    from cyrene.runtime.cyrene_migration import (
        looks_like_cyrene_folder,
        looks_like_cyrene_soul,
    )

    for name in ("conversations", "patterns", "plan", "projects"):
        path = WORKSPACE_DIR / name
        if path.is_dir() and looks_like_cyrene_folder(WORKSPACE_DIR, name):
            remove_path_checked(path)
    scratch = WORKSPACE_DIR / "scratch"
    if scratch.is_dir():
        remove_path_checked(scratch)
    soul = WORKSPACE_DIR / "SOUL.md"
    if soul.is_file() and looks_like_cyrene_soul(soul):
        remove_path_checked(soul)


def remove_directory_children(
    root: Path, *, preserve: frozenset[str] = frozenset()
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for child in list(root.iterdir()):
        if child.name not in preserve:
            remove_path_checked(child)


async def reset_process_runtime_state() -> None:
    from cyrene.agent.session import shutdown_session_tasks
    from cyrene.model_runtime.client import reset_runtime_state
    from cyrene.runtime.shell_wake import get_shell_wake_service
    from cyrene.tooling.backends.mcp_manager import stop_mcp_async
    from cyrene.workbench import chat as workbench_chat

    goal_loop = importlib.import_module("cyrene.workbench.goal_loop")
    manager = workbench_chat._CHAT_RUN_MANAGER
    for chat_id in list(manager.runs):
        await manager.terminate(chat_id, termination_reason="application_data_reset")
    for task in list(manager._cleanup_tasks):
        task.cancel()
    if manager._cleanup_tasks:
        await asyncio.gather(*manager._cleanup_tasks, return_exceptions=True)
    manager._cleanup_tasks.clear()

    for goal_manager in list(goal_loop._MANAGERS.values()):
        await goal_manager.shutdown()
        goal_manager.closed = False
    get_shell_wake_service().clear_pending()

    await shutdown_session_tasks()
    agent_state._sessions.clear()
    await stop_mcp_async()
    await reset_runtime_state()


async def clear_knowledge_data(store_dir: Path) -> None:
    from cyrene.knowledge import ingest
    from cyrene.knowledge.workspace import clear_initialized_databases

    await ingest.cancel_pending_tasks()
    knowledge_paths: set[Path] = set()
    for pattern in ("kb_*.db", "kb_*.db-wal", "kb_*.db-shm", "kb_*.db-journal"):
        knowledge_paths.update(store_dir.glob(pattern))
    for path in knowledge_paths:
        remove_path(path)
    clear_initialized_databases()


class DataResetApplicationService:
    """Coordinate a complete reset against the configured application database."""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)

    async def reset_app_data(self) -> dict[str, Any]:
        from cyrene.browser import clear_browser_data
        from cyrene.config import CACHE_DIR, STORE_DIR, write_env_keys
        from cyrene.knowledge.local_models import delete_all_models
        from cyrene.runtime.database import init_db, init_knowledge_db
        from cyrene.runtime.inbox import clear_all_inboxes
        from cyrene.runtime.onboarding import get_onboarding_status, reset_onboarding_state
        from cyrene.runtime.settings_store import reset_all as reset_web_settings
        from cyrene.runtime.memory.soul import get_default_soul_content, get_soul_path
        from cyrene.workbench import presentation_runtime

        await reset_process_runtime_state()
        browser_result = await clear_browser_data()
        await delete_all_models()
        await clear_knowledge_data(STORE_DIR)
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
            cyrene_root / "conversations",
            cyrene_root / "patterns",
            cyrene_root / "plan",
            cyrene_root / "projects",
            cyrene_root / "scratch",
            BASE_DIR / "backups",
            CACHE_DIR / "voice",
        ):
            remove_path_checked(path)
        reset_legacy_workspace_root_leftovers()

        db_path = Path(self.db_path or str(DB_PATH))
        remove_path_checked(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        await init_db(str(db_path))
        await init_knowledge_db(str(STORE_DIR / "kb_default.db"))

        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
        soul_path = get_soul_path()
        soul_path.parent.mkdir(parents=True, exist_ok=True)
        soul_path.write_text(get_default_soul_content(), encoding="utf-8")

        write_env_keys(
            {
                "OPENAI_API_KEY": "",
                "OPENAI_BASE_URL": DEFAULT_OPENAI_BASE_URL,
                "OPENAI_MODEL": DEFAULT_OPENAI_MODEL,
                "TELEGRAM_BOT_TOKEN": "",
                "WECHAT_BOT_TOKEN": "",
                "WECHAT_OWNER_ID": "",
                "AMAP_API_KEY": "",
                "EMBEDDING_BASE_URL": "",
                "EMBEDDING_API_KEY": "",
                "EMBEDDING_MODEL": "",
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
            "sessions": presentation_runtime._build_sessions(),
        }


__all__ = [
    "DataResetApplicationService",
    "clear_knowledge_data",
    "reset_legacy_workspace_root_leftovers",
]
