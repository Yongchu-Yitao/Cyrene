from __future__ import annotations

from typing import Any
from cyrene.core.plugin import PluginContext

from ._ui_gesture import gesture_tool_def, run_gesture

TOOL_NAME = "CyreneUIScroll"
TOOL_DEF = gesture_tool_def(
    TOOL_NAME,
    "Scroll a currently declared Cyrene viewport or list action by stable node and action IDs; does not depend on pointer position or focus.",
)
TOOL_METADATA = {"read_only": False, "resource_keys": ("cyrene:current-surface",), "requires_order": True}


async def handler(args: dict[str, Any], context: PluginContext) -> str:
    return await run_gesture(
        "cyrene.ui.scroll", {"scroll"},
        args, context,
    )


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
