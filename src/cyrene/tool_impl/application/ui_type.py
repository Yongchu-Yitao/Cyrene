from __future__ import annotations

from typing import Any

from cyrene.tool_impl.application._ui_gesture import gesture_tool_def, run_gesture

TOOL_NAME = "CyreneUIType"
TOOL_DEF = gesture_tool_def(
    TOOL_NAME,
    "Type or select a value through a currently declared Cyrene component action by stable node and action IDs; does not depend on keyboard focus.",
)
TOOL_METADATA = {"read_only": False, "resource_keys": ("cyrene:current-surface",), "requires_order": True}


async def handler(args: dict[str, Any], bot: Any, chat_id: int, db_path: str, notify: Any) -> str:
    return await run_gesture(
        "cyrene.ui.type", {"set_value", "select"},
        args, bot, chat_id, db_path, notify,
    )


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
