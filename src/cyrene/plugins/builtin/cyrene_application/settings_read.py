from __future__ import annotations

from typing import Any
from cyrene.core.plugin import PluginContext

from cyrene.runtime.host_bridge import HostBridgeError, call_host
from cyrene.runtime.settings_service import read_public
from cyrene.plugins.native_runtime import json_result
from cyrene.workbench.application.app_control import envelope

TOOL_NAME = "CyreneSettingsRead"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "Read public or masked values from one Cyrene settings namespace.",
    "parameters": {"type": "object", "properties": {"namespace": {"type": "string", "enum": ["runtime", "desktop", "appearance", "profile", "shortcuts"]}}, "additionalProperties": False},
}}
TOOL_METADATA = {"read_only": True, "resource_keys": ("cyrene:settings",), "requires_order": False}


async def handler(args: dict[str, Any], _context: PluginContext) -> str:
    if args.get("namespace") == "desktop":
        try:
            result = await call_host("desktop.settings.get", {})
        except HostBridgeError as exc:
            return json_result(envelope("unsupported", "cyrene.settings.read", str(exc), error_code=exc.code))
        if result.get("ok") is False:
            return json_result(envelope(
                "error", "cyrene.settings.read", "Cyrene desktop settings could not be read.",
                error_code=str(result.get("error") or "desktop_settings_error"),
            ))
        settings = dict(result.get("settings") or {})
        revision = settings.pop("settingsRevision", None)
        return json_result(envelope(
            "success", "cyrene.settings.read", "Cyrene desktop settings read.",
            revision=revision, settings=settings,
        ))
    values = read_public(args.get("namespace"))
    return json_result(envelope("success", "cyrene.settings.read", "Cyrene settings read.", revision=values["revision"], settings=values["values"]))


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
