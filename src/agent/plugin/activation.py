"""Process-shared activation overrides for executable Plugins."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class PluginActivationSnapshot:
    """A stable copy of the persisted pack and Plugin switches."""

    plugins: dict[str, bool]
    packs: dict[str, bool]


class PluginActivationState:
    """Share live settings between registries without sharing registrations."""

    def __init__(
        self,
        *,
        plugins: Mapping[str, bool] | None = None,
        packs: Mapping[str, bool] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._plugins: dict[str, bool] = {}
        self._packs: dict[str, bool] = {}
        self.replace(plugins=plugins or {}, packs=packs or {})

    @staticmethod
    def _normalize(values: Mapping[str, bool]) -> dict[str, bool]:
        normalized: dict[str, bool] = {}
        for raw_name, enabled in values.items():
            name = str(raw_name or "").strip()
            if not name:
                continue
            if not isinstance(enabled, bool):
                raise TypeError(f"Plugin activation value must be boolean: {name}")
            normalized[name] = enabled
        return normalized

    def replace(
        self,
        *,
        plugins: Mapping[str, bool],
        packs: Mapping[str, bool],
    ) -> None:
        next_plugins = self._normalize(plugins)
        next_packs = self._normalize(packs)
        with self._lock:
            self._plugins = next_plugins
            self._packs = next_packs

    def plugin_enabled(self, name: str) -> bool:
        with self._lock:
            return self._plugins.get(str(name), True)

    def pack_enabled(self, pack_id: str) -> bool:
        with self._lock:
            return self._packs.get(str(pack_id), True)

    def snapshot(self) -> PluginActivationSnapshot:
        with self._lock:
            return PluginActivationSnapshot(
                plugins=dict(self._plugins),
                packs=dict(self._packs),
            )


_ACTIVE_LOCK = threading.RLock()
_ACTIVE_STATE: PluginActivationState | None = None


def active_plugin_activation_state() -> PluginActivationState | None:
    with _ACTIVE_LOCK:
        return _ACTIVE_STATE


def set_active_plugin_activation_state(
    state: PluginActivationState | None,
) -> None:
    global _ACTIVE_STATE
    with _ACTIVE_LOCK:
        _ACTIVE_STATE = state


__all__ = [
    "PluginActivationSnapshot",
    "PluginActivationState",
    "active_plugin_activation_state",
    "set_active_plugin_activation_state",
]
