"""Tool definition entry for quit."""

from __future__ import annotations

from typing import Any

from .definitions import get_native_tool_def

TOOL_NAME = "quit"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def handler(
    _arguments: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    """Keep old prompts valid while the Plugin loop ends naturally."""

    return (
        "Compatibility no-op: finish by returning the final assistant response "
        "without another tool call."
    )

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler"]
