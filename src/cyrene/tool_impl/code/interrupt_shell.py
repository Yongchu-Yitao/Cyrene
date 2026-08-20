"""Tool implementation for InterruptShell."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import json_result

TOOL_NAME = "InterruptShell"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_interrupt_shell(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.terminal.client import get_terminal_daemon_client
    from cyrene.tooling.backends.terminals import animate_terminal_control, resolve_terminal

    terminal = await resolve_terminal(
        terminal_id=str(args.get("shell_id") or ""),
        name=str(args.get("name") or ""),
        access="write",
    )
    await animate_terminal_control(str(terminal.get("id") or ""), "interrupt")
    snap = await get_terminal_daemon_client().interrupt(str(terminal.get("id") or ""))
    return json_result({
        "shell_id": terminal.get("id", ""),
        "terminal_id": terminal.get("id", ""),
        "status": (snap.get("terminal") or {}).get("status", ""),
        "screen_text": snap.get("screenText", ""),
    })


handler = _tool_interrupt_shell

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_interrupt_shell"]
