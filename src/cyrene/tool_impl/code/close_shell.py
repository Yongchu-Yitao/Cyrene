"""Tool implementation for CloseShell."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    close_shell_session,
    json_result,
)

TOOL_NAME = 'CloseShell'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_close_shell(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    snap = await close_shell_session(str(args.get("shell_id", "")))
    return json_result({
        "shell_id": snap.get("id", ""),
        "status": snap.get("status", ""),
        "elapsed": snap.get("elapsed", "—"),
    })


handler = _tool_close_shell

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_close_shell"]
