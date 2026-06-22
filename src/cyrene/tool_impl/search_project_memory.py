"""Tool implementation for search_project_memory."""

from __future__ import annotations

from typing import Any

from cyrene import tool_legacy as _legacy
from cyrene.tool_legacy import _json_result
from cyrene.workbench_context import resolve_workbench_project_data_key_for_session

TOOL_NAME = "search_project_memory"
TOOL_DEF = next(td for td in _legacy.TOOL_DEFS if td["function"]["name"] == TOOL_NAME)


async def _tool_search_project_memory(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    """Search durable memory scoped to the current Workbench project."""
    from cyrene.agent.state import _current_session_id

    query = str(args.get("query", "") or "").strip()
    if not query:
        return _json_result({
            "status": "error",
            "type": "invalid_arguments",
            "message": "query is required",
        })

    category = str(args.get("category", "") or "").strip().lower()
    source = str(args.get("source", "") or "").strip().lower()
    limit = max(1, min(int(args.get("limit", 10) or 10), 20))
    include_stale = bool(args.get("include_stale", False))

    data_key = resolve_workbench_project_data_key_for_session(_current_session_id.get())
    if data_key is None:
        return _json_result({
            "status": "error",
            "type": "not_found",
            "message": "Project memory is only available inside a Workbench project task/chat.",
        })

    from webui.routes_workbench_memory import configure_store, search_project_memories

    configure_store(_db_path)

    memories = search_project_memories(
        data_key,
        query=query,
        category=category,
        source=source,
        limit=limit,
        include_stale=include_stale,
    )
    return _json_result({
        "status": "success",
        "query": query,
        "category": category,
        "source": source,
        "count": len(memories),
        "memories": memories,
        **(
            {"note": "No project memory matches found for the given filters."}
            if not memories else {}
        ),
    })


handler = _tool_search_project_memory

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "handler",
    "_tool_search_project_memory",
]
