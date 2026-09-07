"""Session pack attachment bookkeeping and reversible Hook setup."""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from .plugin import PluginPack


class SetupHookTracker:
    """Record Hooks a pack setup creates or rebinds without constraining it."""

    def __init__(self, hooks: Any) -> None:
        self._hooks = hooks
        self.touched: set[str] = set()
        self.created: set[str] = set()
        self._previous_plugins: dict[str, Any | None] = {}
        self._previous_configs: dict[str, Mapping[str, Any]] = {}
        self._previous_failure_policies: dict[str, str] = {}

    def register(self, *args: Any, **kwargs: Any) -> Any:
        before = {hook.id for hook in self._hooks.list()}
        unsubscribe = self._hooks.register(*args, **kwargs)
        created = {hook.id for hook in self._hooks.list() if hook.id not in before}
        self.created.update(created)
        self.touched.update(created)
        return unsubscribe

    def bind_plugin(self, plugin_id: str, *args: Any, **kwargs: Any) -> Any:
        normalized_id = str(plugin_id)
        if normalized_id not in self._previous_plugins:
            self._previous_plugins[normalized_id] = self._hooks._plugins.resolve(
                normalized_id
            )
        result = self._hooks.bind_plugin(plugin_id, *args, **kwargs)
        self.touched.update(
            hook.id
            for hook in self._hooks.list()
            if hook.plugin_id == str(plugin_id)
        )
        return result

    def update_config(self, hook_id: str, config: Mapping[str, Any]) -> None:
        normalized_id = str(hook_id)
        if normalized_id not in self._previous_configs:
            previous = next(
                (hook for hook in self._hooks.list() if hook.id == normalized_id),
                None,
            )
            if previous is not None:
                self._previous_configs[normalized_id] = dict(previous.config)
        self._hooks.update_config(normalized_id, config)
        self.touched.add(normalized_id)

    def update_failure_policy(self, hook_id: str, failure_policy: str) -> None:
        normalized_id = str(hook_id)
        if normalized_id not in self._previous_failure_policies:
            previous = next(
                (hook for hook in self._hooks.list() if hook.id == normalized_id),
                None,
            )
            if previous is not None:
                self._previous_failure_policies[normalized_id] = (
                    previous.failure_policy
                )
        self._hooks.update_failure_policy(normalized_id, failure_policy)
        self.touched.add(normalized_id)

    def rollback(self) -> None:
        """Undo a failed setup without deleting restored durable bindings."""

        for hook_id in self.created:
            self._hooks.unregister(hook_id)
        for hook_id, config in self._previous_configs.items():
            if hook_id not in self.created:
                self._hooks.update_config(hook_id, config)
        for hook_id, failure_policy in self._previous_failure_policies.items():
            if hook_id not in self.created:
                self._hooks.update_failure_policy(hook_id, failure_policy)
        for plugin_id, previous in self._previous_plugins.items():
            if previous is None:
                self._hooks._plugins.unregister(plugin_id)
            else:
                self._hooks.bind_plugin(plugin_id, previous, replace=True)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._hooks, name)


@dataclass(slots=True)
class SessionPackAttachment:
    pack: PluginPack
    source: str
    setup_fingerprint: tuple[Any, ...]
    hooks: set[str]
    previous_services: dict[str, tuple[bool, Any]]
    provided_services: dict[str, Any]
    driver: Any = None

