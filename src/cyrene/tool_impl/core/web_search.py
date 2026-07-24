"""Tool implementation for WebSearch."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_support import (
    deep_search,
)

TOOL_NAME = 'WebSearch'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_websearch(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    query = str(args.get("query", ""))
    if not query:
        return "No query provided."
    return await deep_search(query)


handler = _tool_websearch

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_websearch"]
