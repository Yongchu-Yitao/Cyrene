"""Tool implementation for cancel_task."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_support import (
    db,
)

TOOL_NAME = 'cancel_task'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_cancel_task(args: dict[str, Any], _bot: Any, _chat_id: int, db_path: str, _notify_state: dict[str, bool] | None) -> str:
    task_id = str(args["task_id"])
    ok = await db.delete_task(db_path, task_id)
    return f"Task {task_id} cancelled." if ok else f"Task {task_id} not found."


handler = _tool_cancel_task

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_cancel_task"]
