"""Editable Cyrene Microsoft Office Plugin pack."""

from collections.abc import Mapping
from copy import deepcopy
from types import ModuleType
from typing import Any

from agent.plugin import Plugin, PluginPack

from . import (
    apply_batch,
    get_context,
    inspect,
    kit,
    list_sessions,
    render_slide,
    setup,
)


def _plugin(
    definition: Mapping[str, Any],
    handler: Any,
    metadata: Mapping[str, Any],
) -> Plugin:
    function = definition["function"]
    plugin_metadata = deepcopy(dict(metadata))
    return Plugin(
        name=str(function["name"]),
        description=str(function.get("description") or ""),
        input_schema=deepcopy(
            dict(
                function.get("parameters")
                or {"type": "object", "properties": {}}
            )
        ),
        handler=handler,
        allow_parallel=bool(
            plugin_metadata.get(
                "allow_parallel",
                not plugin_metadata.get("requires_order", True),
            )
        ),
        timeout_seconds=float(plugin_metadata.get("timeout_seconds", 180.0)),
        metadata=plugin_metadata,
    )


def _module_plugin(module: ModuleType) -> Plugin:
    return _plugin(
        module.TOOL_DEF,
        module.handler,
        getattr(module, "TOOL_METADATA", {}),
    )


_deferred_definitions: list[dict[str, Any]] = []
_deferred_handlers: dict[str, Any] = {}
_deferred_metadata: dict[str, dict[str, Any]] = {}
kit.register_all(
    _deferred_definitions,
    _deferred_handlers,
    _deferred_metadata,
)
_deferred_plugins = tuple(
    _plugin(
        definition,
        _deferred_handlers[str(definition["function"]["name"])],
        _deferred_metadata[str(definition["function"]["name"])],
    )
    for definition in _deferred_definitions
)

plugin_pack = PluginPack(
    id="cyrene_office",
    description="Inspect, edit, render, and compose PowerPoint presentations.",
    plugins=(
        *(
            _module_plugin(module)
            for module in (
                setup,
                list_sessions,
                get_context,
                inspect,
                apply_batch,
                render_slide,
            )
        ),
        *_deferred_plugins,
    ),
)
if len(plugin_pack.plugins) != 50:
    raise RuntimeError("office pack must contain exactly 50 Plugins")

__all__ = ["plugin_pack"]
