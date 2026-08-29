"""Editable Cyrene application Plugin pack."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from cyrene.core.plugin import Plugin, PluginPack

from . import (
    chats,
    data,
    lifecycle,
    projects,
    session_message,
    settings_describe,
    settings_read,
    settings_update,
    status,
    ui_click,
    ui_double_click,
    ui_drag,
    ui_inspect,
    ui_scroll,
    ui_snapshot,
    ui_type,
    updates,
    window,
)

_INTERNAL_PLUGIN_NAMES = frozenset({
    "CyreneSessionMessage",
    "CyreneProjectControl",
    "CyreneChatControl",
    "CyreneDataControl",
    "CyreneUpdateControl",
    "CyreneLifecycleControl",
})

def _plugin(module: ModuleType) -> Plugin:
    function = module.TOOL_DEF["function"]
    name = str(function["name"])
    metadata: dict[str, Any] = {
        **dict(getattr(module, "TOOL_METADATA", {})),
        "main_only": True,
    }
    if name in _INTERNAL_PLUGIN_NAMES:
        metadata["model_visible"] = False
    return Plugin(
        name=name,
        description=str(function.get("description") or ""),
        input_schema=dict(function.get("parameters") or {"type": "object", "properties": {}}),
        handler=module.handler,
        permission_boundary=getattr(module, "permission_boundary", None),
        allow_parallel=bool(metadata.get("allow_parallel", not metadata.get("requires_order", True))),
        timeout_seconds=float(metadata.get("timeout_seconds", 180.0)),
        metadata=metadata,
    )


plugin_pack = PluginPack(
    id="cyrene_application",
    description="Inspect and control the local Cyrene application.",
    plugins=tuple(_plugin(module) for module in (
        status,
        window,
        ui_snapshot,
        ui_inspect,
        ui_click,
        ui_double_click,
        ui_type,
        ui_scroll,
        ui_drag,
        session_message,
        settings_describe,
        settings_read,
        settings_update,
        projects,
        chats,
        data,
        updates,
        lifecycle,
    )),
)

__all__ = ["plugin_pack"]
