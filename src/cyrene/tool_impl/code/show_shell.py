"""Tool implementation for ShowShell."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import json_result

TOOL_NAME = "ShowShell"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_show_shell(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.tooling.backends.terminals import resolve_terminal, show_terminal

    terminal = await resolve_terminal(
        terminal_id=str(args.get("shell_id") or ""),
        name=str(args.get("name") or ""),
        access="show",
    )
    terminal = await show_terminal(str(terminal.get("id") or ""))
    return json_result({
        "shell_id": terminal.get("id", ""),
        "terminal_id": terminal.get("id", ""),
        "shown": True,
        "display": "split",
    })


handler = _tool_show_shell

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_show_shell"]
