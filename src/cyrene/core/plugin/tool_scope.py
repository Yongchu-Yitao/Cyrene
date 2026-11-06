"""Session-local tool access over a live application Plugin registry.

A scope narrows model-facing discovery and execution without changing global
activation, Plugin metadata, model providers, or lifecycle Hooks.
"""
from dataclasses import dataclass, replace

from .registry import PluginRegistry, PluginUnavailableError
from .core_impl.toolbox import create_toolbox_plugin


@dataclass(frozen=True)
class PluginToolScope:
    tool_names: frozenset[str] = frozenset()
    pack_ids: frozenset[str] = frozenset()
    include_core: bool = True
    excluded_tool_names: frozenset[str] = frozenset()
    model_audience: str = "default"

    def allows(self, registered) -> bool:
        if registered.plugin.kind == "tool" and registered.plugin.name in self.excluded_tool_names:
            return False
        return (
            registered.plugin.kind != "tool"
            or (self.include_core and registered.source == "core")
            or registered.plugin.name in self.tool_names
            or registered.pack_id in self.pack_ids
        )


class ScopedPluginRegistry:
    """Live view: newly loaded tools and customization changes stay scoped.

    Lifecycle/catalog management delegates to the authoritative registry. Only
    tool access differs. In particular toolbox must bind to this view, not the
    shared registry; otherwise discovery and invoke would escape the scope.
    """

    def __init__(self, registry: PluginRegistry, scope: PluginToolScope):
        self._registry = registry
        self.scope = scope
        self._toolbox_handler = create_toolbox_plugin(self).handler

    @property
    def authoritative_registry(self):
        return self._registry

    def __getattr__(self, name):
        return getattr(self._registry, name)

    def _bind(self, registered):
        if registered.plugin.metadata.get("model_visible") == "assistant" and self.scope.model_audience == "assistant":
            registered = replace(registered, plugin=replace(registered.plugin, metadata={
                **registered.plugin.metadata, "model_visible": True,
            }))
        if registered.source == "core" and registered.plugin.name == "toolbox":
            return replace(registered, plugin=replace(registered.plugin, handler=self._toolbox_handler))
        return registered

    def registered(self, name):
        return self._bind(self._registry.registered(name))

    def list_plugins(self):
        return tuple(self._bind(item) for item in self._registry.list_plugins())

    def plugin_accessible(self, name, *, agent_id="main"):
        return self.scope.allows(self._registry.registered(name)) and self._registry.plugin_accessible(name, agent_id=agent_id, model_audience=self.scope.model_audience)

    def resolve(self, name, *, agent_id="main"):
        registered = self.registered(name)
        if not self.scope.allows(registered):
            raise PluginUnavailableError(f"Plugin is outside this session's tool scope: {name}")
        self._registry.resolve(name, agent_id=agent_id, model_audience=self.scope.model_audience)
        return registered.plugin

    def tool_definitions(self, *, agent_id="main"):
        return PluginRegistry.tool_definitions(self, agent_id=agent_id)

    def direct_tool_definitions(self, *, agent_id="main", read_only=False):
        return PluginRegistry.direct_tool_definitions(self, agent_id=agent_id, read_only=read_only)
