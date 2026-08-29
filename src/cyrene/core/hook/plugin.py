"""Process-local resolution of persistent Plugin identifiers."""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping

from ..observability import log_operation
from .errors import HookError
from .hook import HookPlugin

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Map stable ``plugin_id`` values to executable implementations."""

    def __init__(self, plugins: Mapping[str, HookPlugin] | None = None) -> None:
        self._lock = threading.RLock()
        self._plugins: dict[str, HookPlugin] = {}
        for plugin_id, plugin in (plugins or {}).items():
            self.register(plugin_id, plugin)
        log_operation(
            logger,
            "hook.plugin_registry",
            "initialize",
            phase="completed",
            plugin_ids=tuple(self._plugins),
        )

    def register(self, plugin_id: str, plugin: HookPlugin, *, replace: bool = False) -> None:
        normalized_id = str(plugin_id).strip()
        if not normalized_id:
            raise ValueError("plugin_id cannot be empty")
        if not callable(plugin):
            raise TypeError("Plugin must be callable")
        with self._lock:
            existing = self._plugins.get(normalized_id)
            if existing is not None and existing is not plugin and not replace:
                raise HookError(f"Plugin id already has an implementation: {normalized_id}")
            self._plugins[normalized_id] = plugin
        log_operation(
            logger,
            "hook.plugin_registry",
            "register",
            phase="completed",
            plugin_id=normalized_id,
            replace=replace,
            implementation=getattr(plugin, "__qualname__", type(plugin).__qualname__),
        )

    def unregister(self, plugin_id: str) -> bool:
        with self._lock:
            removed = self._plugins.pop(str(plugin_id), None) is not None
        log_operation(
            logger,
            "hook.plugin_registry",
            "unregister",
            phase="completed",
            plugin_id=plugin_id,
            removed=removed,
        )
        return removed

    def resolve(self, plugin_id: str) -> HookPlugin | None:
        with self._lock:
            plugin = self._plugins.get(str(plugin_id))
        log_operation(
            logger,
            "hook.plugin_registry",
            "resolve",
            phase="completed" if plugin is not None else "missing",
            plugin_id=plugin_id,
            found=plugin is not None,
        )
        return plugin
