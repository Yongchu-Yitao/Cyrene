"""Application host for process-level contributions from loaded Plugin packs."""

from __future__ import annotations

import inspect
import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI

from .activation import PluginActivationState, set_active_plugin_activation_state
from .model_gateway import PluginModelGateway, ensure_model_router
from .native_tools import BuiltinPluginSeedResult, seed_builtin_plugin_directory
from .plugin import PluginApplicationContext, PluginLifecycleHandler, PluginSearchHandler
from .registry import PluginLoadFailure, PluginRegistry, default_plugin_impl_directory
from .runtime import PluginRuntime

logger = logging.getLogger(__name__)


class PluginApplicationHost:
    """Attach application contributions from the same registry used by Agents."""

    def __init__(
        self,
        *,
        app: FastAPI,
        registry: PluginRegistry,
        bot: Any,
        db_path: str,
        data_directory: str | Path,
        plugin_directory: str | Path,
        load_failures: tuple[PluginLoadFailure, ...] = (),
    ) -> None:
        self.app = app
        self.registry = registry
        self.bot = bot
        self.db_path = str(db_path or "")
        self.data_directory = Path(data_directory).expanduser().resolve()
        self.plugin_directory = Path(plugin_directory).expanduser().resolve()
        self.load_failures = tuple(load_failures)
        ensure_model_router(self.registry)
        self.runtime = PluginRuntime(self.registry)
        self.model_gateway = PluginModelGateway(self.registry, self.runtime)
        self.services: dict[str, Any] = {"model": self.model_gateway}
        self.search_providers: dict[str, PluginSearchHandler] = {}
        self.frontend_modules: list[str] = []
        self._startup_handlers: list[PluginLifecycleHandler] = []
        self._shutdown_handlers: list[PluginLifecycleHandler] = []
        self._attached_packs: list[str] = []
        self._setup_failures: dict[str, str] = {}
        self._attached = False
        self._started = False

    @classmethod
    def load_user_plugins(
        cls,
        *,
        app: FastAPI,
        bot: Any,
        db_path: str,
        data_directory: str | Path,
        plugin_directory: str | Path | None = None,
    ) -> "PluginApplicationHost":
        root = Path(plugin_directory or default_plugin_impl_directory()).expanduser().resolve()
        seed_builtin_plugin_directory(root)
        registry = PluginRegistry(activation=PluginActivationState())
        ensure_model_router(registry)
        failures = registry.load_directory(root)
        from cyrene.runtime import settings_store

        registry.configure_activation(
            plugins=settings_store.get_enabled_plugins(),
            packs=settings_store.get_enabled_plugin_packs(),
        )
        if failures:
            logger.warning(
                "Some Plugin application contributions failed to load: %s",
                "; ".join(f"{item.path}: {item.error}" for item in failures),
            )
        return cls(
            app=app,
            registry=registry,
            bot=bot,
            db_path=db_path,
            data_directory=data_directory,
            plugin_directory=root,
            load_failures=failures,
        )

    def reload_user_plugins(
        self,
    ) -> tuple[BuiltinPluginSeedResult, tuple[PluginLoadFailure, ...]]:
        """Refresh canonical defaults and every user-owned contribution."""

        seeded = seed_builtin_plugin_directory(self.plugin_directory)
        failures = self.registry.refresh_directory(self.plugin_directory)
        self.load_failures = tuple(failures)
        return seeded, self.load_failures

    @property
    def attached_packs(self) -> tuple[str, ...]:
        return tuple(self._attached_packs)

    @property
    def setup_failures(self) -> dict[str, str]:
        return dict(self._setup_failures)

    def attach(self, parent_router: APIRouter) -> None:
        """Run each pack setup against an isolated router and commit atomically."""

        if self._attached:
            raise RuntimeError("Plugin application host is already attached")
        self._attached = True
        for pack in self.registry.list_packs():
            if pack.application_setup is None:
                continue
            child_router = APIRouter()
            inherited_services = dict(self.services)
            services: dict[str, Any] = dict(inherited_services)
            frontend_modules: list[str] = []
            search_providers: dict[str, PluginSearchHandler] = {}
            startup_handlers: list[PluginLifecycleHandler] = []
            shutdown_handlers: list[PluginLifecycleHandler] = []
            context = PluginApplicationContext(
                app=self.app,
                router=child_router,
                bot=self.bot,
                db_path=self.db_path,
                data_directory=self.data_directory,
                plugin_directory=self.plugin_directory,
                services=services,
                frontend_modules=frontend_modules,
                search_providers=search_providers,
                startup_handlers=startup_handlers,
                shutdown_handlers=shutdown_handlers,
            )
            try:
                pack.application_setup(context)
                service_collisions = {name for name, value in services.items() if name in inherited_services and value is not inherited_services[name]}
                search_collisions = set(search_providers) & set(self.search_providers)
                if service_collisions:
                    raise ValueError("application service collision: " + ", ".join(sorted(service_collisions)))
                if search_collisions:
                    raise ValueError("search provider collision: " + ", ".join(sorted(search_collisions)))
            except Exception as exc:
                self._setup_failures[pack.id] = str(exc)
                logger.exception("Failed to attach Plugin application pack %s", pack.id)
                continue
            parent_router.include_router(child_router)
            self.services.update({name: value for name, value in services.items() if name not in inherited_services})
            self.search_providers.update(search_providers)
            for module in frontend_modules:
                if module not in self.frontend_modules:
                    self.frontend_modules.append(module)
            self._startup_handlers.extend(startup_handlers)
            self._shutdown_handlers.extend(shutdown_handlers)
            self._attached_packs.append(pack.id)

    async def startup(self) -> None:
        if self._started:
            return
        self._started = True
        for handler in self._startup_handlers:
            try:
                result = handler()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Plugin application startup handler failed")

    async def shutdown(self) -> None:
        if not self._started:
            return
        self._started = False
        for handler in reversed(self._shutdown_handlers):
            try:
                result = handler()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Plugin application shutdown handler failed")

    def service(self, name: str) -> Any | None:
        return self.services.get(str(name or "").strip())


_ACTIVE_LOCK = threading.RLock()
_ACTIVE_HOST: PluginApplicationHost | None = None


def set_active_plugin_application_host(
    host: PluginApplicationHost | None,
) -> None:
    global _ACTIVE_HOST
    with _ACTIVE_LOCK:
        _ACTIVE_HOST = host
        set_active_plugin_activation_state(host.registry.activation if host is not None else None)


def active_plugin_application_host() -> PluginApplicationHost | None:
    with _ACTIVE_LOCK:
        return _ACTIVE_HOST


def active_plugin_service(name: str) -> Any | None:
    host = active_plugin_application_host()
    return host.service(name) if host is not None else None


__all__ = [
    "PluginApplicationHost",
    "active_plugin_application_host",
    "active_plugin_service",
    "set_active_plugin_application_host",
]
