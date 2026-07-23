"""Tool implementation for listing the current project's literature library."""

from __future__ import annotations

from typing import Any


TOOL_NAME = "ListLibraryItems"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "List structured literature items in the current Workbench project. "
            "Returns stable paper IDs, bibliographic metadata, reading state, and citekeys. "
            "Use SearchLibrary when looking for papers about a topic or supporting evidence."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional title, author, DOI, venue, or abstract filter."},
                "status": {"type": "string", "description": "Optional reading status: unread, reading, read, or archived."},
                "collection_id": {"type": "string", "description": "Optional project-library collection ID."},
                "tag": {"type": "string", "description": "Optional exact tag filter."},
                "limit": {"type": "integer", "description": "Maximum results (default 50, maximum 200)."},
            },
            "required": [],
        },
    },
}
TOOL_METADATA = {
    "read_only": True,
    "resource_keys": ("library:project",),
    "requires_order": False,
}


def _creator_label(creators: list[dict[str, Any]]) -> str:
    names: list[str] = []
    for creator in creators:
        literal = str(creator.get("name") or "").strip()
        first = str(creator.get("first_name") or "").strip()
        last = str(creator.get("last_name") or "").strip()
        label = literal or " ".join(value for value in (first, last) if value)
        if label:
            names.append(label)
    return ", ".join(names)


async def _tool_list_library_items(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    try:
        from cyrene.agent.state import _current_session_id
        from cyrene.knowledge import library
        from cyrene.workbench_context import ensure_knowledge_db_for_session

        db_path = await ensure_knowledge_db_for_session(_current_session_id.get())
        limit = max(1, min(int(args.get("limit") or 50), 200))
        items, total = await library.list_items(
            db_path,
            q=str(args.get("query") or "").strip(),
            collection=str(args.get("collection_id") or "").strip(),
            status=str(args.get("status") or "").strip(),
            tag=str(args.get("tag") or "").strip(),
            limit=limit,
        )
        if not items:
            return "The current project literature library contains no matching items."

        lines = [f"Project literature library: {len(items)} returned of {total} matching item(s)."]
        for index, item in enumerate(items, start=1):
            authors = _creator_label(item.get("creators") or []) or "Unknown author"
            lines.append(
                f"[{index}] {item.get('title') or 'Untitled'} | authors={authors} | "
                f"year={item.get('year') or ''} | venue={item.get('venue') or ''} | "
                f"doi={item.get('doi') or ''} | citekey={item.get('citekey') or ''} | "
                f"status={item.get('reading_status') or 'unread'} | paper_id={item.get('id')}"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error listing the project literature library: {exc}"


handler = _tool_list_library_items

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_list_library_items"]
