"""Tool implementation for ListShells."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    json_result,
    list_shell_sessions,
)

TOOL_NAME = 'ListShells'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_list_shells(_args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    shells = list_shell_sessions(include_exited=False)
    if not shells:
        return "No independent shells are currently running."
    return json_result([
        {
            "shell_id": item.get("id", ""),
            "title": item.get("title", "independent shell"),
            "cwd": item.get("cwd", "."),
            "status": item.get("status", ""),
            "elapsed": item.get("elapsed", "—"),
        }
        for item in shells
    ])


handler = _tool_list_shells

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_list_shells"]
