"""Process-shared user overrides for model-facing Plugin tools."""

from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any, Mapping


class PluginCustomizationState:
    """Share persisted tool edits between application and session registries."""

    def __init__(self, values: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self._lock = threading.RLock()
        self._values: dict[str, dict[str, Any]] = {}
        self.replace(values or {})

    @staticmethod
    def _entry(canonical_name: str, value: Mapping[str, Any]) -> dict[str, Any]:
        name = str(canonical_name or "").strip()
        if not name:
            raise ValueError("Plugin customization id cannot be empty")
        if not isinstance(value, Mapping):
            raise TypeError(f"Plugin customization must be an object: {name}")
        result: dict[str, Any] = {}
        if "name" in value:
            alias = str(value.get("name") or "").strip()
            if alias:
                result["name"] = alias
        if "description" in value:
            result["description"] = str(value.get("description") or "").strip()
        if "agent_exposure" in value:
            exposure = str(value.get("agent_exposure") or "").strip()
            if exposure not in {"direct", "discoverable", "hidden"}:
                raise ValueError(f"Invalid Plugin agent exposure: {exposure}")
            result["agent_exposure"] = exposure
        if value.get("deleted") is True:
            result["deleted"] = True
        return result

    def replace(self, values: Mapping[str, Mapping[str, Any]]) -> None:
        next_values = {
            str(name).strip(): self._entry(str(name), value)
            for name, value in values.items()
            if str(name or "").strip()
        }
        with self._lock:
            self._values = next_values

    def get(self, canonical_name: str) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._values.get(str(canonical_name), {}))

    def set(self, canonical_name: str, value: Mapping[str, Any]) -> None:
        normalized = self._entry(canonical_name, value)
        with self._lock:
            if normalized:
                self._values[str(canonical_name)] = normalized
            else:
                self._values.pop(str(canonical_name), None)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return deepcopy(self._values)


_ACTIVE_LOCK = threading.RLock()
_ACTIVE_STATE: PluginCustomizationState | None = None


def active_plugin_customization_state() -> PluginCustomizationState | None:
    with _ACTIVE_LOCK:
        return _ACTIVE_STATE


def set_active_plugin_customization_state(
    state: PluginCustomizationState | None,
) -> None:
    global _ACTIVE_STATE
    with _ACTIVE_LOCK:
        _ACTIVE_STATE = state


__all__ = [
    "PluginCustomizationState",
    "active_plugin_customization_state",
    "set_active_plugin_customization_state",
]
