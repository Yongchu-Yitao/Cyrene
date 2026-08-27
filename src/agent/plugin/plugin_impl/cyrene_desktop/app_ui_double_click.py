from __future__ import annotations
from typing import Any
from agent.plugin import PluginContext
from ._app_ui import ACTION_METADATA, action_tool_def, run_action
TOOL_NAME = "AppUIDoubleClick"
TOOL_DEF = action_tool_def(TOOL_NAME, "double_click", "Invoke a native double-click action only when the accessibility provider explicitly exposes one.")
TOOL_METADATA = ACTION_METADATA
async def handler(args: dict[str, Any], context: PluginContext) -> str: return await run_action("double_click", args, context)
__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
