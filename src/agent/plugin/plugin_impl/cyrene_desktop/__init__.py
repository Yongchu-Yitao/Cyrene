"""Editable Cyrene desktop Plugin pack."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from agent.plugin import Plugin, PluginPack

from . import (
    app_ui_click,
    app_ui_double_click,
    app_ui_drag,
    app_ui_inspect,
    app_ui_scroll,
    app_ui_snapshot,
    app_ui_type,
    app_use,
)


def _plugin(module: ModuleType) -> Plugin:
    function = module.TOOL_DEF["function"]
    metadata: dict[str, Any] = {
        **dict(getattr(module, "TOOL_METADATA", {})),
        "main_only": True,
    }
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
    id="cyrene_desktop",
    description="Inspect and interact with desktop applications.",
    plugins=tuple(_plugin(module) for module in (
        app_use,
        app_ui_snapshot,
        app_ui_inspect,
        app_ui_click,
        app_ui_double_click,
        app_ui_type,
        app_ui_scroll,
        app_ui_drag,
    )),
)

__all__ = ["plugin_pack"]
