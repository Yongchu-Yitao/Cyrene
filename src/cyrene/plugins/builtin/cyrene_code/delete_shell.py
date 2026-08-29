"""Tool implementation for DeleteShell."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import (
    json_result,
    plugin_localized,
    run_context_value,
)

from .definitions import get_native_tool_def
from .services import terminal_service

TOOL_NAME = "DeleteShell"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_delete_shell(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    terminals = terminal_service(context)
    terminal = await terminals.resolve(
        context,
        terminal_id=str(args.get("shell_id") or ""),
        name=str(args.get("name") or ""),
    )
    if (
        str(terminal.get("createdBy") or "") != "agent"
        or str(terminal.get("ownerChatId") or "")
        != str(run_context_value(context, "session_id") or "")
    ):
        raise PermissionError(plugin_localized(
            context,
            "The Agent can delete only terminals it created in this conversation.",
            "Agent 只能删除自己在当前会话中创建的终端。",
        ))
    result = await terminals.remove(str(terminal.get("id") or ""))
    return json_result({
        "shell_id": terminal.get("id", ""),
        "terminal_id": terminal.get("id", ""),
        "deleted": bool(result.get("deleted")),
        "wake_cancelled": bool(result.get("wakeCancelled")),
    })


handler = _tool_delete_shell

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_delete_shell"]
