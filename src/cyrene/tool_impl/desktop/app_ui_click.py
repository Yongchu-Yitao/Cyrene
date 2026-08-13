from __future__ import annotations
from typing import Any
from cyrene.tool_impl.desktop._app_ui import ACTION_METADATA, action_tool_def, run_action
TOOL_NAME = "AppUIClick"
TOOL_DEF = action_tool_def(TOOL_NAME, "click", "Invoke, select, or toggle an action declared by an external application's accessibility tree.")
TOOL_METADATA = ACTION_METADATA
async def handler(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify: Any) -> str: return await run_action("click", args)
__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]

