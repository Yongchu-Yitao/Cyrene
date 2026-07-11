"""FastAPI app factory and WebBot adapter for the scheduler."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from cyrene.config import WEB_PORT
from cyrene.task_lifecycle import cancel_and_wait

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"
_WORKBENCH_UI_DIR = Path(__file__).parent.parent / "workbench-webui"


class WebBot:
    """Bot adapter for the scheduler in web-only mode.

    Implements send_message() so the scheduler, heartbeat, and steward
    can deliver proactive messages without a Telegram bot.
    """

    def __init__(self) -> None:
        self._pending: list[dict[str, Any]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self._pending.append({
            "chat_id": chat_id,
            "text": text,
            "timestamp": datetime.now().isoformat(),
        })

    def pop_pending(self, chat_id: int) -> list[dict[str, Any]]:
        matched = [m for m in self._pending if m["chat_id"] == chat_id]
        self._pending = [m for m in self._pending if m["chat_id"] != chat_id]
        return matched


def create_app(bot: Any, db_path: str, instance_id: str = "", ui_mode: str = "workbench") -> FastAPI:
    from cyrene.channels.wechat import setup_wechat as _setup_wechat
    from webui.routes import register_routes

    from webui.auth import LocalAuthMiddleware

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        await _start_workbench_chat_runs()
        await _start_wechat()
        await _migrate_knowledge_db()
        await _sync_knowledge_catalog()
        await _decouple_default_project_knowledge()
        try:
            yield
        finally:
            await _close_browser_session()

    app = FastAPI(title="Cyrene", lifespan=_lifespan)
    app.add_middleware(LocalAuthMiddleware)
    app.state.instance_id = instance_id
    app.state.ui_mode = ui_mode
    app.mount("/static/workbench-ui", StaticFiles(directory=str(_WORKBENCH_UI_DIR)), name="workbench-ui")
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/api/instance-id")
    async def api_instance_id() -> dict[str, str]:
        return {"instance_id": str(app.state.instance_id or "")}

    register_routes(app, bot, db_path)

    async def _start_workbench_chat_runs() -> None:
        from webui.routes_workbench_chat import startup_chat_runs

        startup_chat_runs()
        manager = getattr(app.state, "goal_loop_manager", None)
        if manager is not None:
            await manager.startup()

    async def _start_wechat() -> None:
        try:
            await _setup_wechat(app, db_path)
        except Exception:
            logger.warning("WeChat bot setup failed — check your config / proxy setup")

    async def _migrate_knowledge_db() -> None:
        try:
            from cyrene.config import migrate_knowledge_to_workspace_db
            result = await migrate_knowledge_to_workspace_db()
            if result["migrated"]:
                logger.info("Knowledge base migrated: %s", result["reason"])
        except Exception:
            logger.warning("Knowledge base migration failed (non-fatal)")

    async def _sync_knowledge_catalog() -> None:
        try:
            from cyrene.config import get_knowledge_db_path
            from cyrene.db import init_knowledge_db
            from cyrene.knowledge import store, ingest
            _kb_db_path = str(get_knowledge_db_path())
            await init_knowledge_db(_kb_db_path)
            await store.sync_filesystem(_kb_db_path)
            app.state._knowledge_sync_task = asyncio.create_task(
                ingest.process_pending(_kb_db_path)
            )
        except Exception:
            logger.warning("Knowledge catalog sync failed — check your knowledge base")

    async def _decouple_default_project_knowledge() -> None:
        # One-time: lift the Workbench default project's own knowledge docs out of
        # the shared legacy kb_default.db (which the catalog fills with every
        # project's files) into its id-scoped db. Idempotent, non-destructive.
        #
        # Runs in the BACKGROUND. The migration re-indexes every doc it moves —
        # vision analysis for images, embeddings for the rest — which is an
        # unbounded series of LLM calls. uvicorn only finishes startup (and our
        # launcher only then prints PORT=) once every startup handler returns,
        # and Electron gives up waiting after 30s. A default project with a few
        # images easily blows past that, leaving the desktop app unable to start
        # ("The Python backend did not start within 30 seconds"). Fire it off so
        # the server comes up immediately; keep a reference so the task isn't
        # garbage-collected mid-flight.
        async def _run() -> None:
            try:
                from cyrene.knowledge.workbench import migrate_default_project_knowledge

                result = await migrate_default_project_knowledge()
                if result.get("migrated"):
                    logger.info(
                        "Default project knowledge decoupled: %s docs -> kb_%s.db",
                        result.get("migrated"),
                        result.get("target"),
                    )
            except Exception:
                logger.warning("Default project knowledge decouple failed (non-fatal)")

        app.state._decouple_task = asyncio.create_task(_run())

    async def _close_browser_session() -> None:
        manager = getattr(app.state, "goal_loop_manager", None)
        if manager is not None:
            try:
                await manager.shutdown()
            except Exception:
                logger.warning("Goal-loop shutdown failed", exc_info=True)

        try:
            from webui.routes_workbench_chat import shutdown_chat_runs

            await shutdown_chat_runs()
        except Exception:
            logger.warning("Workbench chat run shutdown failed")

        try:
            from cyrene.browser import close_session
            await close_session()
        except Exception:
            logger.warning("Browser session shutdown failed")

        try:
            updater = getattr(app.state, "wechat_updater", None)
            if updater is not None:
                await updater.stop()
                app.state.wechat_updater = None
            from cyrene.channels.wechat import get_current_client, set_current_client

            client = get_current_client()
            set_current_client(None)
            if client is not None:
                await client.close()
        except Exception:
            logger.warning("WeChat shutdown failed", exc_info=True)

        app_tasks = {
            task
            for task in (
                getattr(app.state, "_knowledge_sync_task", None),
                getattr(app.state, "_decouple_task", None),
            )
            if isinstance(task, asyncio.Task)
        }
        await cancel_and_wait(app_tasks)

        # Cancel and await all agent/telemetry/indexing work while the event loop
        # and SQLite worker threads are still alive.
        try:
            from cyrene.runtime_lifecycle import shutdown_background_work

            await shutdown_background_work()
        except Exception:
            logger.warning("Runtime cleanup during shutdown failed", exc_info=True)

    return app


async def run_web(bot: Any, db_path: str, port: int = WEB_PORT, instance_id: str = "", ui_mode: str = "workbench") -> None:
    app = create_app(bot, db_path, instance_id=instance_id, ui_mode=ui_mode)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", loop="asyncio")
    server = uvicorn.Server(config)
    logger.info("Web UI at http://0.0.0.0:%d", port)
    await server.serve()
