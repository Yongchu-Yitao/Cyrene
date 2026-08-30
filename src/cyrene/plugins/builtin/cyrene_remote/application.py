"""Application routes and lifecycle for the remote-device Plugin pack."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI

from cyrene.plugins.context import PluginApplicationContext
from cyrene.workbench.control.control_ports import (
    WorkbenchChatApplicationPort,
    WorkbenchProjectApplicationPort,
)

from .commands import RemoteCommandExecutor, RemoteControlRuntime
from .control import RemoteControlStore
from .routes import (
    register_pairing_routes,
    register_peer_routes,
    register_settings_routes,
)
from .services import RemoteControlApplicationService, RemoteDeviceProjectionService


class _HostServiceProxy:
    """Resolve a Workbench port after the core composition phase completes."""

    def __init__(self, app: Any, name: str) -> None:
        self._app = app
        self._name = name

    def _service(self) -> Any:
        host = getattr(self._app.state, "plugin_application_host", None)
        service = host.service(self._name) if host is not None else None
        if service is None:
            raise RuntimeError(
                f"Remote Plugin dependency is unavailable: {self._name}"
            )
        return service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service(), name)


class _RemoteApplicationProxy:
    """Bind the route adapters before the enabled pack creates its database."""

    def __init__(self) -> None:
        self.service: Any = None
        self.store: Any = None
        self.runtime: Any = None

    def bind(self, *, service: Any, store: Any, runtime: Any) -> None:
        self.service = service
        self.store = store
        self.runtime = runtime

    def __getattr__(self, name: str) -> Any:
        if self.service is None:
            raise RuntimeError("Remote Plugin application is not running")
        return getattr(self.service, name)


def build_remote_application(
    db_path: str,
    *,
    bot: Any,
    chat: WorkbenchChatApplicationPort,
    projects: WorkbenchProjectApplicationPort,
) -> tuple[
    RemoteControlStore,
    RemoteControlRuntime,
    RemoteControlApplicationService,
]:
    """Compose the private Remote Plugin runtime around generic Workbench ports."""

    store = RemoteControlStore(db_path)
    executor = RemoteCommandExecutor(
        store=store,
        bot=bot,
        db_path=db_path,
        chat=chat,
        projects=projects,
    )
    runtime = RemoteControlRuntime(
        db_path=db_path,
        store=store,
        executor=executor,
    )
    projection = RemoteDeviceProjectionService(store, projects, runtime)
    service = RemoteControlApplicationService(store, projection, runtime)
    return store, runtime, service


def register_remote_route_adapters(
    router: APIRouter,
    service: RemoteControlApplicationService,
) -> None:
    """Attach only Remote-owned pairing, authorization, and settings APIs."""

    register_settings_routes(router, service)
    register_pairing_routes(router, service)
    register_peer_routes(router, service)


def register_remote_routes(
    router: APIRouter,
    app: FastAPI,
    db_path: str,
    *,
    bot: Any,
    chat: WorkbenchChatApplicationPort,
    projects: WorkbenchProjectApplicationPort,
) -> RemoteControlStore:
    """Test/embedded composition entrypoint kept inside the Plugin boundary."""

    store, runtime, service = build_remote_application(
        db_path,
        bot=bot,
        chat=chat,
        projects=projects,
    )
    register_remote_route_adapters(router, service)
    app.state.remote_control_runtime = runtime
    return store


def setup_application(context: PluginApplicationContext) -> None:
    proxy = _RemoteApplicationProxy()
    register_remote_route_adapters(context.router, proxy)
    context.provide("remote", proxy)
    context.expose_frontend("remote")
    from cyrene.platform.settings_service import (
        PluginSettingsContribution,
        SettingControlSpec,
    )

    context.provide(
        "remote_settings",
        PluginSettingsContribution(controls=(
            SettingControlSpec("remote.service", "remote", "existing_capability", "cyrene_remote", "R2"),
            SettingControlSpec("remote.pairing", "remote", "user_ceremony", "cyrene_remote", "R3", secret=True),
            SettingControlSpec("remote.peer_grants", "remote", "existing_capability", "cyrene_remote", "R3"),
        )),
    )

    async def start() -> None:
        if proxy.runtime is None:
            store, runtime, service = build_remote_application(
                context.db_path,
                bot=context.bot,
                chat=_HostServiceProxy(context.app, "workbench_chat"),
                projects=_HostServiceProxy(context.app, "workbench_projects"),
            )
            proxy.bind(service=service, store=store, runtime=runtime)
        await proxy.runtime.start()

    async def stop() -> None:
        if proxy.runtime is not None:
            await proxy.runtime.stop()

    context.on_startup(start)
    context.on_shutdown(stop)


__all__ = [
    "build_remote_application",
    "register_remote_route_adapters",
    "register_remote_routes",
    "setup_application",
]
