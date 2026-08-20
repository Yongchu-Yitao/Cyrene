"""Tool implementation for DeleteShell."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import json_result

TOOL_NAME = "DeleteShell"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_delete_shell(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.agent.context import get_current_session_id
    from cyrene.terminal.client import get_terminal_daemon_client
    from cyrene.tooling.backends.terminals import resolve_terminal

    terminal = await resolve_terminal(
        terminal_id=str(args.get("shell_id") or ""),
        name=str(args.get("name") or ""),
        access="write",
    )
    if (
        str(terminal.get("createdBy") or "") != "agent"
        or str(terminal.get("ownerChatId") or "") != str(get_current_session_id() or "")
    ):
        raise PermissionError("The Agent can delete only terminals it created in this conversation.")
    result = await get_terminal_daemon_client().remove(str(terminal.get("id") or ""))
    return json_result({
        "shell_id": terminal.get("id", ""),
        "terminal_id": terminal.get("id", ""),
        "deleted": bool(result.get("deleted")),
        "wake_cancelled": bool(result.get("wakeCancelled")),
    })


handler = _tool_delete_shell

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_delete_shell"]
