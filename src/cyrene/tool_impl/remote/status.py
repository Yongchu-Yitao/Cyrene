"""Read-only typed operations against a selected remote Cyrene."""

from __future__ import annotations

from typing import Any

from cyrene.tool_impl.remote.common import request_remote_command
from cyrene.tooling.runtime_api import json_result

TOOL_NAME = "RemoteCyreneStatus"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Read status and records from a paired Cyrene device explicitly "
            "selected in the current chat. Use ListRemoteDevices first when needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Selected device id. Required when multiple devices are attached.",
                },
                "command": {
                    "type": "string",
                    "enum": [
                        "capabilities.read",
                        "projects.list",
                        "chats.list",
                        "chats.read",
                        "runs.read",
                        "runs.events",
                        "tasks.list",
                        "tasks.read",
                        "artifacts.list",
                        "artifacts.read",
                    ],
                },
                "project_id": {
                    "type": "string",
                    "description": "Remote project id for project-scoped operations.",
                },
                "payload": {
                    "type": "object",
                    "description": "Typed operation parameters such as chat_id, run_id, cursor, task_id, or artifact_id.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 120,
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}
TOOL_METADATA = {
    "read_only": True,
    "resource_keys": ("remote:{device_id}",),
    "requires_order": False,
}


async def handler(
    args: dict[str, Any],
    _bot: Any,
    chat_id: int,
    db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    try:
        result = await request_remote_command(
            args,
            db_path,
            fallback_chat_id=chat_id,
        )
        return json_result(result)
    except Exception as exc:
        return json_result({"ok": False, "error": str(exc)})


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
