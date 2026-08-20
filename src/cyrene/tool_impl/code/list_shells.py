"""Tool implementation for ListShells."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import json_result

TOOL_NAME = 'ListShells'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_list_shells(_args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.tooling.backends.terminals import list_agent_terminals

    shells = await list_agent_terminals(include_exited=True)
    if not shells:
        return "No terminals are bound to this conversation."
    return json_result([
        {
            "shell_id": item.get("id", ""),
            "title": item.get("title", "independent shell"),
            "cwd": item.get("cwd", "."),
            "status": item.get("status", ""),
            "exit_code": item.get("exitCode"),
            "wake_id": item.get("wakeId", ""),
            "created_by": item.get("createdBy", ""),
            "last_actor": item.get("lastActor", ""),
            "last_input_at": item.get("lastInputAt", ""),
            "input_event_count": item.get("inputEventCount", 0),
        }
        for item in shells
    ])


handler = _tool_list_shells

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_list_shells"]
