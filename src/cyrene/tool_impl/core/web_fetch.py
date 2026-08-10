"""Tool implementation for WebFetch."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    truncate,
    httpx,
)

TOOL_NAME = 'WebFetch'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_webfetch(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    url = str(args["url"])
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
    return truncate(response.text)


handler = _tool_webfetch

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_webfetch"]
