"""Tool implementation for WebSearch."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized
from .definitions import get_native_tool_def

TOOL_NAME = 'WebSearch'
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {"agent_exposure": "direct"}


async def _tool_websearch(args: dict[str, Any], context: PluginContext) -> str:
    query = str(args.get("query", ""))
    if not query:
        return plugin_localized(context, "No query was provided.", "请提供搜索内容。")
    detail = str(args.get("detail") or "preview").strip().lower()
    if detail not in {"preview", "content"}:
        return plugin_localized(
            context,
            'Invalid detail. Use "preview" or "content".',
            'detail 无效，请使用 "preview" 或 "content"。',
        )
    try:
        max_results = max(1, min(8, int(args.get("max_results") or 5)))
    except (TypeError, ValueError):
        return plugin_localized(
            context,
            "Invalid max_results. Use an integer from 1 to 8.",
            "max_results 无效，请使用 1 到 8 的整数。",
        )
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
    if not callable(search) or context.services.get("content") is None:
        raise RuntimeError("cyrene_content application service is unavailable")
    return str(await search(query, **options))


handler = _tool_websearch

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_websearch"]
