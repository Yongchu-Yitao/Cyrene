"""Return one fresh, user-visible Remote Desktop frame to the main model."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import json_result, run_context_value

from .contracts import SnapshotRegion
from .service import RemoteDesktopError, remote_desktop_service


TOOL_NAME = "InspectRemoteDesktop"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Get one fresh image of the display currently selected by the user in an authorized "
            "Remote Desktop Pane. This V1 tool is view-only and never returns audio or clipboard data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "minLength": 1, "maxLength": 100},
                "region": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "minimum": 0},
                        "y": {"type": "integer", "minimum": 0},
                        "width": {"type": "integer", "minimum": 1},
                        "height": {"type": "integer", "minimum": 1},
                    },
                    "required": ["x", "y", "width", "height"],
                    "additionalProperties": False,
                },
                "reason": {"type": "string", "minLength": 1, "maxLength": 300},
            },
            "required": ["session_id", "reason"],
            "additionalProperties": False,
        },
    },
}
TOOL_METADATA = {
    "read_only": True,
    "main_only": True,
    "resource_keys": ("desktop:{session_id}",),
    "requires_order": True,
    "timeout_seconds": 20.0,
    "i18n": {
        "zh": {
            "name": "查看远程桌面",
            "description": "获取用户当前选择显示器的一张新鲜画面；仅查看，不包含音频或剪贴板。",
        }
    },
}


async def handler(args: dict[str, Any], context: PluginContext) -> Any:
    chat_id = str(run_context_value(context, "session_id", "") or "").strip()
    if not chat_id:
        return json_result({"ok": False, "code": "no_authorized_desktop_session", "error": "The active conversation is unavailable."})
    raw_region = args.get("region")
    region = None
    if isinstance(raw_region, dict):
        region = SnapshotRegion(
            x=int(raw_region["x"]),
            y=int(raw_region["y"]),
            width=int(raw_region["width"]),
            height=int(raw_region["height"]),
        )
    try:
        return await remote_desktop_service().request_agent_snapshot(
            str(args.get("session_id") or ""),
            chat_id,
            reason=str(args.get("reason") or ""),
            region=region,
            tool_call_id=str(context.node_id or ""),
        )
    except RemoteDesktopError as exc:
        return json_result({"ok": False, "code": exc.code, "error": exc.message})


__all__ = ["TOOL_DEF", "TOOL_METADATA", "TOOL_NAME", "handler"]
