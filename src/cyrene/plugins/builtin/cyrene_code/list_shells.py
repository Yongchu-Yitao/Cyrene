"""Tool implementation for ListShells."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import json_result, plugin_localized

from .definitions import get_native_tool_def
from .services import terminal_service

TOOL_NAME = 'ListShells'
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {"read_only": True}


async def _tool_list_shells(
    _args: dict[str, Any],
    context: PluginContext,
) -> str:
    terminals = terminal_service(context)
    bound_shells = await terminals.list_owned(context, include_exited=True)
    visible_shells = await terminals.list_visible(context)
    bound_ids = {str(item.get("id") or "") for item in bound_shells}
    visible_by_id = {
        str(item.get("id") or ""): item for item in visible_shells
    }
    shells = list(bound_shells)
    shells.extend(
        item for item in visible_shells
        if str(item.get("id") or "") not in bound_ids
    )
    if not shells:
        return plugin_localized(
            context,
            "No terminals are bound to this conversation or visible in the current split.",
            "当前会话未绑定终端，当前分屏中也没有可见终端。",
        )
    return json_result([
        {
            "shell_id": item.get("id", ""),
            "title": item.get("title") or plugin_localized(
                context,
                "Independent terminal",
                "独立终端",
            ),
            "cwd": (
                item.get("remoteCwd", "") or item.get("cwd", ".")
                if item.get("connectionKind") == "ssh"
                else item.get("cwd", ".")
            ),
            "status": item.get("status", ""),
            "exit_code": item.get("exitCode"),
            "wake_id": item.get("wakeId", ""),
            "created_by": item.get("createdBy", ""),
            "last_actor": item.get("lastActor", ""),
            "last_input_at": item.get("lastInputAt", ""),
            "input_event_count": item.get("inputEventCount", 0),
            "bound_to_conversation": str(item.get("id") or "") in bound_ids,
            "visible_in_current_split": str(item.get("id") or "") in visible_by_id,
            "visible_side": str(
                (visible_by_id.get(str(item.get("id") or "")) or {}).get("visibleSide") or ""
            ),
            **({
                "connection_kind": "ssh",
                "connection_status": item.get("connectionStatus", ""),
                "ssh_target": item.get("sshTarget", ""),
                "remote_cwd": item.get("remoteCwd", ""),
                "tmux_session": item.get("tmuxSession", ""),
                "disconnect_reason": item.get("disconnectReason", ""),
            } if item.get("connectionKind") == "ssh" else {}),
        }
        for item in shells
    ])


handler = _tool_list_shells

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_list_shells"]
