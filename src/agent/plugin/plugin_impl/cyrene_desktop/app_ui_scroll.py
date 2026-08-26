from __future__ import annotations
from typing import Any
from ._app_ui import ACTION_METADATA, action_tool_def, run_action
TOOL_NAME = "AppUIScroll"
TOOL_DEF = action_tool_def(TOOL_NAME, "scroll", "Invoke a semantic scroll action declared by an accessible container.", {
    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]}, "amount": {"type": "integer", "minimum": 1, "maximum": 100},
}, ("direction",))
TOOL_METADATA = ACTION_METADATA
async def handler(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify: Any) -> str: return await run_action("scroll", args)
__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
