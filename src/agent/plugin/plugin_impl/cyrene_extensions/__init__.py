"""Editable Cyrene extensions Plugin pack."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from agent.plugin import Plugin, PluginPack

from . import list_environment, manage_extensions, search_environment


def _plugin(module: ModuleType) -> Plugin:
    function = module.TOOL_DEF["function"]
    metadata: dict[str, Any] = dict(getattr(module, "TOOL_METADATA", {}))
    if str(function["name"]) == "ManageExtensions":
        metadata["main_only"] = True
    return Plugin(
        name=str(function["name"]),
        description=str(function.get("description") or ""),
        input_schema=dict(function.get("parameters") or {"type": "object", "properties": {}}),
        handler=module.handler,
        allow_parallel=bool(metadata.get("allow_parallel", not metadata.get("requires_order", True))),
        timeout_seconds=float(metadata.get("timeout_seconds", 180.0)),
        metadata=metadata,
    )


plugin_pack = PluginPack(
    id="cyrene_extensions",
    description="Inspect and manage extensions, environments, and hooks.",
    plugins=tuple(_plugin(module) for module in (
        list_environment,
        search_environment,
        manage_extensions,
    )),
)

__all__ = ["plugin_pack"]
