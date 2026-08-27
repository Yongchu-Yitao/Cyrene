from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext

from ._ui_gesture import gesture_tool_def, run_gesture

TOOL_NAME = "CyreneUIDrag"
TOOL_DEF = gesture_tool_def(
    TOOL_NAME,
    "Drag, reorder, move, or resize through a currently declared Cyrene semantic action by stable node and action IDs; never accepts raw coordinates.",
)
TOOL_METADATA = {"read_only": False, "resource_keys": ("cyrene:current-surface",), "requires_order": True}


async def handler(args: dict[str, Any], context: PluginContext) -> str:
    return await run_gesture(
        "cyrene.ui.drag", {"move", "set_frame", "adjust"},
        args, context,
    )


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
