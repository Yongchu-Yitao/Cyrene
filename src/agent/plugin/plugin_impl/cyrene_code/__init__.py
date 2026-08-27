"""Editable Cyrene code Plugin pack."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import ModuleType
from typing import Any

from agent.plugin import Plugin, PluginPack

from . import (
    analysis,
    delete_shell,
    git,
    indexer,
    interrupt_shell,
    list_shells,
    read_shell,
    send_shell,
    show_shell,
    start_shell,
)
from .services import setup


def _plugin(
    definition: Mapping[str, Any],
    handler: Callable[..., Any],
    metadata: Mapping[str, Any] | None = None,
) -> Plugin:
    function = definition["function"]
    if not isinstance(function, Mapping):
        raise TypeError("code Plugin definition must contain a function mapping")
    values = dict(metadata or {})
    return Plugin(
        name=str(function["name"]),
        description=str(function.get("description") or ""),
        input_schema=dict(
            function.get("parameters")
            or {"type": "object", "properties": {}}
        ),
        handler=handler,
        allow_parallel=bool(
            values.get("allow_parallel", not values.get("requires_order", True))
        ),
        timeout_seconds=float(values.get("timeout_seconds", 180.0)),
        metadata=values,
    )


def _module_plugin(module: ModuleType) -> Plugin:
    metadata = dict(getattr(module, "TOOL_METADATA", {}))
    if str(module.TOOL_DEF["function"]["name"]) == "StartShell":
        metadata["main_only"] = True
    return _plugin(module.TOOL_DEF, module.handler, metadata)


_shell_modules = (
    start_shell,
    send_shell,
    list_shells,
    read_shell,
    interrupt_shell,
    show_shell,
    delete_shell,
)
_declarations = (
    *analysis.PLUGIN_DECLARATIONS,
    *git.PLUGIN_DECLARATIONS,
    *indexer.PLUGIN_DECLARATIONS,
)

plugin_pack = PluginPack(
    id="cyrene_code",
    description="Shell sessions, code analysis, Git, and workspace indexing.",
    plugins=tuple(_module_plugin(module) for module in _shell_modules)
    + tuple(_plugin(definition, handler) for definition, handler in _declarations),
    setup=setup,
)
if len(plugin_pack.plugins) != 19:
    raise RuntimeError("code pack must contain exactly 19 Plugins")

__all__ = ["plugin_pack"]
