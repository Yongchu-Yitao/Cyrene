"""List Remote Desktop sessions visible to the current main conversation."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import json_result, run_context_value

from .service import RemoteDesktopError, remote_desktop_service


TOOL_NAME = "ListRemoteDesktopSessions"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "List connected Remote Desktop sessions that the user has authorized "
            "this main conversation to view by placing both cards in the same Pane workspace."
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
    "main_only": True,
    "resource_keys": ("desktop:authorized-sessions",),
    "requires_order": False,
    "i18n": {
        "zh": {
            "name": "列出可查看的远程桌面",
            "description": "列出用户通过同一 Pane 分屏授权当前主对话查看的远程桌面会话。",
        }
    },
}


async def handler(_args: dict[str, Any], context: PluginContext) -> str:
    chat_id = str(run_context_value(context, "session_id", "") or "").strip()
    if not chat_id:
        return json_result({"ok": False, "code": "no_authorized_desktop_session", "error": "The active conversation is unavailable."})
    try:
        sessions = remote_desktop_service().authorized_sessions(chat_id)
    except RemoteDesktopError as exc:
        return json_result({"ok": False, "code": exc.code, "error": exc.message})
    return json_result(
        {
            "sessions": [
                {
                    "session_id": item["session_id"],
                    "device_id": item["device_id"],
                    "device_name": item["device_name"],
                    "mode": item["mode"],
                    "state": item["state"],
                    "display": item.get("display") or {},
                    "secure_surface": bool(item.get("secure_surface")),
                    "audio_available_to_agent": False,
                }
                for item in sessions
            ]
        }
    )


__all__ = ["TOOL_DEF", "TOOL_METADATA", "TOOL_NAME", "handler"]
