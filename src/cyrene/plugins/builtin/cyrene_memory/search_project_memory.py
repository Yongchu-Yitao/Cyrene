"""Tool implementation for search_project_memory."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from ._native import create_tool, service as memory_service
from .definitions import get_native_tool_def
from cyrene.plugins.native_runtime import json_result, plugin_localized

TOOL_NAME = "search_project_memory"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_search_project_memory(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    """Search durable memory using keyword matching within one project."""
    query = str(args.get("query", "") or "").strip()
    if not query:
        return json_result({
            "status": "error",
            "type": "invalid_arguments",
            "message": plugin_localized(
                context,
                "query is required",
                "必须提供 query",
            ),
        })

    category = str(args.get("category", "") or "").strip().lower()
    source = str(args.get("source", "") or "").strip().lower()
    limit = max(1, min(int(args.get("limit", 10) or 10), 20))
    include_stale = bool(args.get("include_stale", False))

    memory = memory_service(context)
    project_id = memory.project_id
    if project_id is None:
        return json_result({
            "status": "error",
            "type": "not_found",
            "message": plugin_localized(
                context,
                "Project memory is only available inside a Workbench project task/chat.",
                "项目记忆仅可在 Workbench 项目任务或对话中使用。",
            ),
        })

    from .structured import search_project_memories

    memory.configure_stores()

    memories = search_project_memories(
        project_id,
        query=query,
        category=category,
        source=source,
        limit=limit,
        include_stale=include_stale,
    )
    return json_result({
        "status": "success",
        "search_mode": "keyword",
        "uses_embeddings": False,
        "query": query,
        "category": category,
        "source": source,
        "count": len(memories),
        "memories": memories,
        **(
            {
                "note": plugin_localized(
                    context,
                    "No project memories match the requested filters.",
                    "没有匹配当前筛选条件的项目记忆。",
                )
            }
            if not memories else {}
        ),
    })


handler = _tool_search_project_memory
plugin = create_tool(TOOL_DEF, handler, allow_parallel=True)

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "handler",
    "plugin",
    "_tool_search_project_memory",
]
