from __future__ import annotations

from typing import Any
from agent.plugin import PluginContext

from ._ui_gesture import gesture_tool_def, run_gesture

TOOL_NAME = "CyreneUIDoubleClick"
TOOL_DEF = gesture_tool_def(
    TOOL_NAME,
    "Double-click a currently declared Cyrene component action by stable node and action IDs. The action must explicitly advertise a double-click gesture; never uses coordinates or focus.",
)
TOOL_METADATA = {"read_only": False, "resource_keys": ("cyrene:current-surface",), "requires_order": True}


async def handler(args: dict[str, Any], context: PluginContext) -> str:
    return await run_gesture(
        "cyrene.ui.double_click",
        {"invoke"},
        args,
        context,
        required_gesture_aliases={"double_press", "double_click"},
    )


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
