from __future__ import annotations

from typing import Any

from cyrene.tool_impl.application._ui_gesture import gesture_tool_def, run_gesture

TOOL_NAME = "CyreneUIClick"
TOOL_DEF = gesture_tool_def(
    TOOL_NAME,
    "Click, toggle, open a context menu, or dismiss a currently declared Cyrene component action by stable node and action IDs; never uses coordinates or focus.",
)
TOOL_METADATA = {"read_only": False, "resource_keys": ("cyrene:current-surface",), "requires_order": True}


async def handler(args: dict[str, Any], bot: Any, chat_id: int, db_path: str, notify: Any) -> str:
    return await run_gesture(
        "cyrene.ui.click", {"invoke", "toggle", "open_menu", "dismiss"},
        args, bot, chat_id, db_path, notify,
    )


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
