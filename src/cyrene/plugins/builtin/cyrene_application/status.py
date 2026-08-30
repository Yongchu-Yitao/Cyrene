from __future__ import annotations

from typing import Any
from cyrene.core.plugin import PluginContext

from cyrene.platform.config_store import get_settings_revision
from cyrene.platform.host_bridge import HostBridgeError, call_host
from cyrene.platform.host_actions import list_actions
from cyrene.platform.version import get_version
from cyrene.plugins.native_runtime import json_result
from cyrene.workbench.application.app_control import envelope

TOOL_NAME = "CyreneAppStatus"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "Read local Cyrene backend, settings-revision, host, current-surface and window status.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}}
TOOL_METADATA = {"read_only": True, "resource_keys": ("cyrene:status",), "requires_order": False}


async def handler(_args: dict[str, Any], _context: PluginContext) -> str:
    host: dict[str, Any]
    try:
        host = await call_host("host.status", {})
    except HostBridgeError as exc:
        host = {"ok": False, "error": exc.code, "hostKind": "web"}
    return json_result(envelope(
        "success", "cyrene.app.status", "Cyrene application status read.",
        revision=get_settings_revision(),
        backend={"available": True, "version": get_version()},
        host=host,
        pending_actions=list_actions(),
    ))


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
