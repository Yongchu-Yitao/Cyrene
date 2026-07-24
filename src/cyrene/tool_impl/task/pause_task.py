"""Tool implementation for pause_task."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_support import (
    db,
)

TOOL_NAME = 'pause_task'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_pause_task(args: dict[str, Any], _bot: Any, _chat_id: int, db_path: str, _notify_state: dict[str, bool] | None) -> str:
    task_id = str(args["task_id"])
    ok = await db.update_task_status(db_path, task_id, "paused")
    return f"Task {task_id} paused." if ok else f"Task {task_id} not found."


handler = _tool_pause_task

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_pause_task"]
