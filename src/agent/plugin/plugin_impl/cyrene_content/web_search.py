"""Tool implementation for WebSearch."""

from __future__ import annotations

from typing import Any

from .definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    deep_search,
)

TOOL_NAME = 'WebSearch'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_websearch(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    query = str(args.get("query", ""))
    if not query:
        return "No query provided."
    detail = str(args.get("detail") or "preview").strip().lower()
    if detail not in {"preview", "content"}:
        return 'Invalid detail. Use "preview" or "content".'
    try:
        max_results = max(1, min(8, int(args.get("max_results") or 5)))
    except (TypeError, ValueError):
        return "Invalid max_results. Use an integer from 1 to 8."
    from cyrene.agent.context import current_run_context

    run_context = current_run_context()
    return await deep_search(
        query,
        db_path=str(_db_path or ""),
        session_id=run_context.session_id or str(_chat_id or ""),
        round_id=run_context.round_id,
        detail=detail,
        max_results=max_results,
    )


handler = _tool_websearch

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_websearch"]
