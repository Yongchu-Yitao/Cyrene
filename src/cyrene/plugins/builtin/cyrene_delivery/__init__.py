"""Editable Cyrene delivery Plugin pack."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from cyrene.core.plugin import Plugin, PluginPack

from . import (
    send_file,
    send_message,
    send_message_to_user,
    send_notification,
    send_telegram,
    send_wechat_file,
)

_MAIN_ONLY = {"send_telegram", "send_message", "send_file", "send_wechat_file"}


def _plugin(module: ModuleType) -> Plugin:
    function = module.TOOL_DEF["function"]
    name = str(function["name"])
    metadata: dict[str, Any] = dict(getattr(module, "TOOL_METADATA", {}))
    if name == "send_telegram":
        metadata["default_enabled"] = False
    if name in _MAIN_ONLY:
        metadata["main_only"] = True
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
    id="cyrene_delivery",
    description="Deliver messages, files, and notifications.",
    plugins=tuple(_plugin(module) for module in (
        send_telegram,
        send_message,
        send_message_to_user,
        send_file,
        send_wechat_file,
        send_notification,
    )),
)

__all__ = ["plugin_pack"]
