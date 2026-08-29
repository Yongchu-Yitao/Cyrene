from __future__ import annotations

from typing import Any
from cyrene.core.plugin import PluginContext

from cyrene.runtime.host_bridge import HostBridgeError, call_host
from cyrene.runtime.settings_service import describe
from cyrene.plugins.native_runtime import json_result
from cyrene.workbench.application.app_control import envelope

TOOL_NAME = "CyreneSettingsDescribe"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "Describe agent-visible Cyrene settings, types, risks, limits and apply modes without reading secrets.",
    "parameters": {"type": "object", "properties": {"namespace": {"type": "string", "enum": ["runtime", "desktop", "appearance", "profile", "shortcuts"]}}, "additionalProperties": False},
}}
TOOL_METADATA = {"read_only": True, "resource_keys": ("cyrene:settings",), "requires_order": False}


async def handler(args: dict[str, Any], _context: PluginContext) -> str:
    namespace = args.get("namespace")
    schema = describe(namespace)
    if namespace == "desktop":
        try:
            host = await call_host("desktop.settings.get", {})
        except HostBridgeError as exc:
            return json_result(envelope(
                "unsupported", "cyrene.settings.describe", str(exc),
                error_code=exc.code,
            ))
        if host.get("ok") is False:
            return json_result(envelope(
                "error", "cyrene.settings.describe",
                "Cyrene desktop settings schema could not be described.",
                error_code=str(host.get("error") or "desktop_settings_error"),
            ))
        schema["revision"] = dict(host.get("settings") or {}).get("settingsRevision")
    return json_result(envelope("success", "cyrene.settings.describe", "Cyrene settings schema described.", revision=schema["revision"], schema=schema))


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
