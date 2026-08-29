"""Read authorized public snapshots from peer chats in the current group."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from ._native import create_tool, service as memory_service
from .definitions import get_native_tool_def
from cyrene.plugins.native_runtime import json_result, plugin_localized

TOOL_NAME = "ReadChatGroupSessions"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_read_chat_group_sessions(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    from cyrene.workbench.chat import chat_groups

    memory = memory_service(context)
    if not memory.is_main:
        return json_result({
            "status": "error",
            "type": "permission_denied",
            "message": plugin_localized(
                context,
                "Chat-group session reads are available only to the main Agent.",
                "仅主 Agent 可以读取对话组会话。",
            ),
        })
    current_session_id = memory.session_id
    requested = args.get("session_ids")
    if requested is not None and not isinstance(requested, list):
        return json_result({
            "status": "error",
            "type": "invalid_arguments",
            "message": plugin_localized(
                context,
                "session_ids must be an array of strings.",
                "session_ids 必须是字符串数组。",
            ),
        })
    chat_groups.configure_store(memory.db_path)
    try:
        payload = chat_groups.read_group_session_snapshots(
            current_session_id,
            requested_session_ids=[str(item) for item in (requested or [])],
            message_offset=max(0, int(args.get("message_offset", 0) or 0)),
            message_limit=max(1, min(int(args.get("message_limit", 20) or 20), 200)),
        )
    except PermissionError:
        return json_result({
            "status": "error",
            "type": "permission_denied",
            "message": plugin_localized(
                context,
                "The requested chat-group sessions are not accessible.",
                "无权访问请求的对话组会话。",
            ),
        })
    return json_result(payload)


handler = _tool_read_chat_group_sessions
plugin = create_tool(TOOL_DEF, handler, allow_parallel=True)

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "handler",
    "plugin",
    "_tool_read_chat_group_sessions",
]
