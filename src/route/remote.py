"""Composition root for remote-control HTTP routes and runtime."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI

from cyrene.runtime.remote_commands import RemoteCommandExecutor, RemoteControlRuntime
from cyrene.runtime.remote_control import RemoteControlStore
from cyrene.runtime.remote_services import (
    RemoteChatContextRepository,
    RemoteControlApplicationService,
    RemoteDeviceProjectionService,
)
from cyrene.workbench.control_ports import (
    WorkbenchChatApplicationPort,
    WorkbenchGoalLoopApplicationPort,
    WorkbenchProjectApplicationPort,
    WorkbenchTaskApplicationPort,
)
from route.remote_routes import (
    register_context_routes,
    register_pairing_routes,
    register_peer_routes,
    register_settings_routes,
)


def register_remote_routes(
    router: APIRouter,
    app: FastAPI,
    db_path: str,
    *,
    bot: Any,
    chat: WorkbenchChatApplicationPort,
    projects: WorkbenchProjectApplicationPort,
    tasks: WorkbenchTaskApplicationPort,
    goals: WorkbenchGoalLoopApplicationPort,
    utc_now,
) -> RemoteControlStore:
    store = RemoteControlStore(db_path)
    executor = RemoteCommandExecutor(
        store=store, bot=bot, db_path=db_path,
        chat=chat, projects=projects, tasks=tasks, goals=goals,
    )
    runtime = RemoteControlRuntime(db_path=db_path, store=store, executor=executor)
    projection = RemoteDeviceProjectionService(store, projects, runtime)
    service = RemoteControlApplicationService(
        store, projection, RemoteChatContextRepository(db_path, utc_now=utc_now), runtime
    )
    app.state.remote_control_store = store
    app.state.remote_control_runtime = runtime
    register_settings_routes(router, service)
    register_pairing_routes(router, service)
    register_peer_routes(router, service)
    register_context_routes(router, service)
    return store


__all__ = ["register_remote_routes"]
