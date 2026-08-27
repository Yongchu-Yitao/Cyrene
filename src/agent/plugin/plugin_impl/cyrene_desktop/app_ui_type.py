from __future__ import annotations
from typing import Any
from agent.plugin import PluginContext
from ._app_ui import ACTION_METADATA, action_tool_def, run_action
TOOL_NAME = "AppUIType"
TOOL_DEF = action_tool_def(TOOL_NAME, "type", "Set or append text through a native editable accessibility interface.", {
    "text": {"type": "string", "maxLength": 100000}, "replace": {"type": "boolean"},
}, ("text",))
TOOL_METADATA = ACTION_METADATA
async def handler(args: dict[str, Any], _context: PluginContext) -> str: return await run_action("type", args)
__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
