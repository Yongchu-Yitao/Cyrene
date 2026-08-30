"""Tool implementation for ShowShell."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import json_result

from .definitions import get_native_tool_def
from .services import terminal_service

TOOL_NAME = "ShowShell"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {"public_errors": True}


async def _tool_show_shell(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    terminals = terminal_service(context)
    terminal = await terminals.resolve(
        context,
        terminal_id=str(args.get("shell_id") or ""),
        name=str(args.get("name") or ""),
    )
    terminal = await terminals.show(context, str(terminal.get("id") or ""))
    return json_result({
        "shell_id": terminal.get("id", ""),
        "terminal_id": terminal.get("id", ""),
        "shown": True,
        "display": "split",
    })


handler = _tool_show_shell

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_show_shell"]
