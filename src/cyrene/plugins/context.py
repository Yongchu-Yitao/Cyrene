"""Workbench-facing application contribution context for Cyrene plugins."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, MutableMapping, MutableSequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias

PluginLifecycleHandler: TypeAlias = Callable[[], Any | Awaitable[Any]]
PluginSearchHandler: TypeAlias = Callable[[str, int], Any | Awaitable[Any]]
PluginFrontendHandler: TypeAlias = Callable[
    [Any, Mapping[str, Any]],
    Any | Awaitable[Any],
]


@dataclass(slots=True)
class PluginApplicationContext:
    """Workbench capabilities exposed while an application plugin is attached.

    This context deliberately lives outside :mod:`cyrene.core`: HTTP routing,
    frontend modules, search, and process lifecycle are Workbench/product
    concerns rather than requirements of the host-neutral agent runtime.
    """

    app: Any
    router: Any
    bot: Any
    db_path: str
    data_directory: Path
    plugin_directory: Path
    services: MutableMapping[str, Any]
    frontend_modules: MutableSequence[str]
    search_providers: MutableMapping[str, PluginSearchHandler]
    startup_handlers: MutableSequence[PluginLifecycleHandler]
    shutdown_handlers: MutableSequence[PluginLifecycleHandler]
    frontend_methods: MutableMapping[str, PluginFrontendHandler] = field(
        default_factory=dict
    )
    registry: Any | None = None

    @staticmethod
    def _name(value: str, label: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{label} cannot be empty")
        return normalized

    def provide(self, name: str, service: Any, *, replace: bool = False) -> None:
        normalized = self._name(name, "Plugin application service name")
        if normalized in self.services and not replace:
            raise ValueError(f"Plugin application service already exists: {normalized}")
        self.services[normalized] = service

    def expose_frontend(self, module: str) -> None:
        normalized = self._name(module, "Plugin frontend module")
        if normalized not in self.frontend_modules:
            self.frontend_modules.append(normalized)

    def provide_frontend_method(
        self,
        name: str,
        handler: PluginFrontendHandler,
        *,
        replace: bool = False,
    ) -> None:
        normalized = self._name(name, "Plugin frontend method name")
        if not callable(handler):
            raise TypeError("Plugin frontend method handler must be callable")
        if normalized in self.frontend_methods and not replace:
            raise ValueError(f"Plugin frontend method already exists: {normalized}")
        self.frontend_methods[normalized] = handler

    def provide_search(
        self,
        result_type: str,
        handler: PluginSearchHandler,
        *,
        replace: bool = False,
    ) -> None:
        normalized = self._name(result_type, "Plugin search result type")
        if not callable(handler):
            raise TypeError("Plugin search handler must be callable")
        if normalized in self.search_providers and not replace:
            raise ValueError(f"Plugin search provider already exists: {normalized}")
        self.search_providers[normalized] = handler

    def on_startup(self, handler: PluginLifecycleHandler) -> None:
        if not callable(handler):
            raise TypeError("Plugin startup handler must be callable")
        self.startup_handlers.append(handler)

    def on_shutdown(self, handler: PluginLifecycleHandler) -> None:
        if not callable(handler):
            raise TypeError("Plugin shutdown handler must be callable")
        self.shutdown_handlers.append(handler)


__all__ = [
    "PluginApplicationContext",
    "PluginFrontendHandler",
    "PluginLifecycleHandler",
    "PluginSearchHandler",
]
