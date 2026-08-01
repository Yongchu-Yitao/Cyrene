"""Read authorized public snapshots from peer chats in the current group."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import json_result

TOOL_NAME = "ReadChatGroupSessions"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_read_chat_group_sessions(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    from cyrene.agent.context import current_agent_id, get_current_session_id
    from cyrene.workbench import chat, chat_groups, context as workbench_context

    if current_agent_id() != "main":
        return json_result({
            "status": "error",
            "type": "permission_denied",
            "message": "Chat-group session reads are available only to the main agent.",
        })
    current_session_id = get_current_session_id()
    requested = args.get("session_ids")
    if requested is not None and not isinstance(requested, list):
        return json_result({
            "status": "error",
            "type": "invalid_arguments",
            "message": "session_ids must be an array of strings.",
        })
    chat.configure_store(db_path)
    chat_groups.configure_store(db_path)
    workbench_context.configure_store(db_path)
    try:
        payload = chat_groups.read_group_session_snapshots(
            current_session_id,
            requested_session_ids=[str(item) for item in (requested or [])],
            message_offset=max(0, int(args.get("message_offset", 0) or 0)),
            message_limit=max(1, min(int(args.get("message_limit", 20) or 20), 200)),
        )
    except PermissionError as exc:
        return json_result({
            "status": "error",
            "type": "permission_denied",
            "message": str(exc),
        })
    return json_result(payload)


handler = _tool_read_chat_group_sessions

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "handler",
    "_tool_read_chat_group_sessions",
]
