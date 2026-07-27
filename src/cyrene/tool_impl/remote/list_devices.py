"""List paired remote devices explicitly attached to the current chat."""

from __future__ import annotations

from typing import Any

from cyrene.tool_impl.remote.common import selected_remote_devices
from cyrene.tooling.runtime_api import json_result

TOOL_NAME = "ListRemoteDevices"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "List paired Cyrene devices that the user explicitly added to the "
            "current chat with Add Context. Devices outside this chat are never returned."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}
TOOL_METADATA = {
    "read_only": True,
    "resource_keys": ("remote:chat-context",),
    "requires_order": False,
}


async def handler(
    _args: dict[str, Any],
    _bot: Any,
    chat_id: int,
    db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    try:
        _chat, devices = selected_remote_devices(
            db_path,
            fallback_chat_id=chat_id,
        )
        return json_result(
            {
                "devices": [
                    {
                        "device_id": device["device_id"],
                        "device_name": device["display_name"],
                        "fingerprint": device["fingerprint"],
                        "capabilities": device["received_capabilities"],
                        "project_scopes": device["received_project_scopes"],
                    }
                    for device in devices
                ]
            }
        )
    except Exception as exc:
        return json_result({"ok": False, "error": str(exc)})


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
