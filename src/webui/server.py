"""FastAPI app factory and WebBot adapter for the scheduler."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from cyrene.config import DATA_DIR, WEB_PORT


class NoCacheStaticFiles(StaticFiles):
    """Static assets without explicit cache headers fall back to Chromium's
    heuristic caching, which made Electron renderers keep stale JSX builds
    after `npm run build` (the index.html ?v= query never changes). Force
    revalidation so dev reloads always see the newest frontend."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await super().__call__(scope, receive, send)

    def file_response(
        self,
        full_path: str,
        stat_result: Any,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "no-cache"
        return response


logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


def _configure_app(app: FastAPI, middleware: Any, register_routes: Any, bot: Any, db_path: str, instance_id: str) -> None:
    app.add_middleware(middleware)
    app.state.instance_id = instance_id
    app.state.ui_mode = "workbench"
    app.state.web_port = WEB_PORT
    app.mount("/static", NoCacheStaticFiles(directory=str(_STATIC_DIR)), name="static")
    register_routes(app, bot, db_path)


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


def create_app(
    bot: Any,
    db_path: str,
    instance_id: str = "",
    ui_mode: str = "workbench",
    *,
    enable_background_plugins: bool = False,
) -> FastAPI:
    from route.registry import register_routes

    from webui.auth import LocalAuthMiddleware

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        await plugin_application_host.startup()
        scheduler = getattr(_app.state, "plugin_background_scheduler", None)
        try:
            if scheduler is not None:
                scheduler.start()
            await _start_workbench_chat_runs()
            yield
        finally:
            # Drain native Agent/Chat work while Plugin services and their
            # model/provider ports are still available.
            await _shutdown_native_runs()
            if scheduler is not None and scheduler.running:
                scheduler.shutdown(wait=False)
            await plugin_application_host.shutdown()
            from agent.plugin import (
                active_plugin_application_host,
                set_active_plugin_application_host,
            )

            if active_plugin_application_host() is plugin_application_host:
                set_active_plugin_application_host(None)

    app = FastAPI(title="Cyrene", lifespan=_lifespan)
    from agent.plugin import (
        PluginApplicationHost,
        set_active_plugin_application_host,
    )

    plugin_application_host = PluginApplicationHost.load_user_plugins(
        app=app,
        bot=bot,
        db_path=db_path,
        data_directory=DATA_DIR,
    )
    app.state.plugin_application_host = plugin_application_host
    set_active_plugin_application_host(plugin_application_host)
    # ``ui_mode`` remains in the Python call signature for historical callers,
    # but Workbench is now the only served UI.
    _configure_app(app, LocalAuthMiddleware, register_routes, bot, db_path, instance_id)
    if enable_background_plugins:
        from agent.plugin.background import setup_background_plugin_scheduler

        # Build the clock only after register_routes attached the authoritative
        # Plugin host, then start it inside the lifespan after pack startup.
        app.state.plugin_background_scheduler = setup_background_plugin_scheduler(
            str(db_path)
        )

    async def _start_workbench_chat_runs() -> None:
        from cyrene.workbench.chat_runs import startup_chat_runs
        from cyrene.workbench.task_runs import recover_interrupted_task_runs

        startup_chat_runs(db_path)
        task_context = getattr(app.state, "task_session_context", None)
        if task_context is not None:
            await recover_interrupted_task_runs(
                db_path,
                task_context.resume_interrupted_run,
            )
        manager = getattr(app.state, "goal_loop_manager", None)
        if manager is not None:
            await manager.startup()

    async def _shutdown_native_runs() -> None:
        manager = getattr(app.state, "goal_loop_manager", None)
        if manager is not None:
            try:
                await manager.shutdown()
            except Exception:
                logger.warning("Goal-loop shutdown failed", exc_info=True)

        try:
            from cyrene.workbench.task_runs import shutdown_task_runs

            await shutdown_task_runs(db_path)
        except Exception:
            logger.warning("Workbench task run shutdown failed", exc_info=True)

        try:
            from cyrene.workbench.chat_runs import shutdown_chat_runs
            from cyrene.workbench.chat_service import shutdown_chat_services

            try:
                await shutdown_chat_runs()
            finally:
                await shutdown_chat_services()
        except Exception:
            logger.warning("Workbench chat run shutdown failed", exc_info=True)

        # Cancel and await all agent/telemetry/indexing work while the event loop
        # and SQLite worker threads are still alive.
        try:
            from cyrene.runtime.lifecycle import shutdown_background_work

            await shutdown_background_work()
        except Exception:
            logger.warning("Runtime cleanup during shutdown failed", exc_info=True)

    return app


async def run_web(bot: Any, db_path: str, port: int = WEB_PORT, instance_id: str = "", ui_mode: str = "workbench") -> None:
    app = create_app(
        bot,
        db_path,
        instance_id=instance_id,
        ui_mode=ui_mode,
        enable_background_plugins=True,
    )
    app.state.web_port = int(port)
    from cyrene.agent_runtime.model_gateway import configure_model_gateway
    configure_model_gateway(port)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", loop="asyncio")
    server = uvicorn.Server(config)
    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    logger.info("Web UI at http://0.0.0.0:%d", port)
    await server.serve()
