from __future__ import annotations
from typing import Any
from agent.plugin import PluginContext
from ._app_ui import ACTION_METADATA, action_tool_def, run_action
TOOL_NAME = "AppUIDrag"
TOOL_DEF = action_tool_def(TOOL_NAME, "drag", "Invoke a native semantic drag, move, reorder, or resize action explicitly declared by the target node.")
TOOL_METADATA = ACTION_METADATA
async def handler(args: dict[str, Any], context: PluginContext) -> str: return await run_action("drag", args, context)
__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
