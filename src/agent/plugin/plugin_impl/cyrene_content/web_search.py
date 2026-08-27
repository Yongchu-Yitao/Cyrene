"""Tool implementation for WebSearch."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.plugin import PluginContext
from .definitions import get_native_tool_def
from .search_backend import deep_search

TOOL_NAME = 'WebSearch'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_websearch(args: dict[str, Any], context: PluginContext) -> str:
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
    raw_run_context = context.data.get("run_context")
    run_context = raw_run_context if isinstance(raw_run_context, Mapping) else {}
    options = {
        "db_path": str(context.data.get("db_path") or ""),
        "session_id": str(
            run_context.get("session_id")
            or context.data.get("session_id")
            or context.data.get("chat_id")
            or ""
        ),
        "round_id": str(
            run_context.get("round_id") or context.data.get("run_id") or ""
        ),
        "detail": detail,
        "max_results": max_results,
    }
    service = context.services.get("web_search")
    search = getattr(service, "search", None)
    if callable(search):
        return str(await search(query, **options))
    return await deep_search(query, **options)


handler = _tool_websearch

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_websearch"]
