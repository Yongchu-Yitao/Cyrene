"""Tool implementation for InterruptShell."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import json_result

from .definitions import get_native_tool_def
from .services import terminal_service

TOOL_NAME = "InterruptShell"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_interrupt_shell(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    terminals = terminal_service(context)
    terminal = await terminals.resolve(
        context,
        terminal_id=str(args.get("shell_id") or ""),
        name=str(args.get("name") or ""),
    )
    await terminals.animate(context, str(terminal.get("id") or ""), "interrupt")
    snap = await terminals.interrupt(str(terminal.get("id") or ""))
    return json_result({
        "shell_id": terminal.get("id", ""),
        "terminal_id": terminal.get("id", ""),
        "status": (snap.get("terminal") or {}).get("status", ""),
        "screen_text": snap.get("screenText", ""),
    })


handler = _tool_interrupt_shell

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_interrupt_shell"]
