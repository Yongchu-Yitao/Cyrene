"""Process-local resolution of persistent Plugin identifiers."""

from __future__ import annotations

import threading
from collections.abc import Mapping

from .errors import HookError
from .hook import HookPlugin


class PluginRegistry:
    """Map stable ``plugin_id`` values to executable implementations."""

    def __init__(self, plugins: Mapping[str, HookPlugin] | None = None) -> None:
        self._lock = threading.RLock()
        self._plugins: dict[str, HookPlugin] = {}
        for plugin_id, plugin in (plugins or {}).items():
            self.register(plugin_id, plugin)

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

    def unregister(self, plugin_id: str) -> bool:
        with self._lock:
            return self._plugins.pop(str(plugin_id), None) is not None

    def resolve(self, plugin_id: str) -> HookPlugin | None:
        with self._lock:
            return self._plugins.get(str(plugin_id))
