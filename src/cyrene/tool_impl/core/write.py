"""Tool implementation for Write."""

from __future__ import annotations

import asyncio
from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.execution_context import (
    is_system_initiated_round as _is_system_initiated_round,
)
from cyrene.tooling.runtime_api import (
    request_write_elevation,
    resolve_workspace_write_target,
)

TOOL_NAME = 'Write'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_write(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.runtime.settings_store import is_workspace_active
    if not is_workspace_active():
        return "Workspace access is disabled. Ask the user to add workspace via '+ add context' in the chat input, or set a workspace directory in Settings."
    try:
        path = resolve_workspace_write_target(str(args["path"]))
    except ValueError:
        elev = await request_write_elevation(tool_name="Write", path_hint=str(args.get("path", "")))
        if elev is not None:
            return elev
        # 已放行（完全访问 / 审核 agent 批准）：full-access 已置位，重新解析即成功
        path = resolve_workspace_write_target(str(args["path"]))
    if _is_system_initiated_round() and path.exists():
        return (
            "Not written: proactive system-initiated rounds may only create "
            f"new files, and this path already exists: {path}"
        )
    def write_file() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(args.get("content", "")), encoding="utf-8")

    await asyncio.to_thread(write_file)
    return f"Wrote {path}"


handler = _tool_write

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_write"]
