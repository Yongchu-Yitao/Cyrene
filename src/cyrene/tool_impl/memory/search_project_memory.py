"""Tool implementation for search_project_memory."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_support import _json_result
from cyrene.workbench_context import resolve_workbench_project_id_for_session

TOOL_NAME = "search_project_memory"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


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

    project_id = resolve_workbench_project_id_for_session(_current_session_id.get())
    if project_id is None:
        return _json_result({
            "status": "error",
            "type": "not_found",
            "message": "Project memory is only available inside a Workbench project task/chat.",
        })

    from cyrene.workbench_memory_service import configure_store, search_project_memories

    configure_store(_db_path)

    memories = search_project_memories(
        project_id,
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
