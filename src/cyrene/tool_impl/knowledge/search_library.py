"""Tool implementation for project-scoped bibliographic and evidence search."""

from __future__ import annotations

from typing import Any

import aiosqlite

from cyrene.tool_impl.knowledge.list_library_items import _creator_label


TOOL_NAME = "SearchLibrary"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Search the current Workbench project's literature library. Combines structured "
            "bibliographic matching with the existing hybrid keyword/vector knowledge search, "
            "and returns stable paper IDs and evidence passages. Use this before suggesting or "
            "inserting citations; never invent a citation that is not returned by the library."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Topic, title, author, DOI, or evidence phrase."},
                "k": {"type": "integer", "description": "Maximum papers to return (default 8, maximum 30)."},
                "status": {"type": "string", "description": "Optional reading-status filter."},
                "tag": {"type": "string", "description": "Optional exact tag filter."},
            },
            "required": ["query"],
        },
    },
}
TOOL_METADATA = {
    "read_only": True,
    "resource_keys": ("library:project", "knowledge:project"),
    "requires_order": False,
}


async def _document_item_map(db_path: str, document_ids: list[str]) -> dict[str, str]:
    if not document_ids:
        return {}
    placeholders = ",".join("?" for _ in document_ids)
    async with aiosqlite.connect(db_path, timeout=30) as db:
        cursor = await db.execute(
            f"SELECT kb_document_id,item_id FROM library_attachments "
            f"WHERE kb_document_id IN ({placeholders})",
            document_ids,
        )
        return {str(row[0]): str(row[1]) for row in await cursor.fetchall() if row[0] and row[1]}


async def _tool_search_library(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return "Error: query is required."

    try:
        from cyrene.agent.state import _current_session_id
        from cyrene.knowledge import library, retrieve
        from cyrene.workbench_context import ensure_knowledge_db_for_session

        db_path = await ensure_knowledge_db_for_session(_current_session_id.get())
        k = max(1, min(int(args.get("k") or 8), 30))
        metadata_items, _total = await library.list_items(
            db_path,
            q=query,
            status=str(args.get("status") or "").strip(),
            tag=str(args.get("tag") or "").strip(),
            limit=k,
        )
        passages = await retrieve.search_knowledge(db_path, query, k=max(k * 2, 8))
        document_map = await _document_item_map(
            db_path,
            list(dict.fromkeys(str(hit.get("document_id") or "") for hit in passages if hit.get("document_id"))),
        )

        items_by_id = {str(item.get("id")): item for item in metadata_items}
        evidence: dict[str, list[dict[str, Any]]] = {}
        for hit in passages:
            item_id = document_map.get(str(hit.get("document_id") or ""))
            if not item_id:
                continue
            evidence.setdefault(item_id, []).append(hit)
            if item_id not in items_by_id:
                item = await library.get_item(db_path, item_id)
                if item:
                    items_by_id[item_id] = item

        ranked_ids = [str(item.get("id")) for item in metadata_items]
        ranked_ids.extend(item_id for item_id in evidence if item_id not in ranked_ids)
        ranked_ids = ranked_ids[:k]
        if not ranked_ids:
            return "No matching papers or indexed evidence were found in the current project library."

        lines = [f"Found {len(ranked_ids)} project-library paper(s) for: {query}"]
        for index, item_id in enumerate(ranked_ids, start=1):
            item = items_by_id[item_id]
            authors = _creator_label(item.get("creators") or []) or "Unknown author"
            lines.append(
                f"\n[{index}] {item.get('title') or 'Untitled'}\n"
                f"paper_id={item_id}; authors={authors}; year={item.get('year') or ''}; "
                f"venue={item.get('venue') or ''}; doi={item.get('doi') or ''}; "
                f"citekey={item.get('citekey') or ''}; status={item.get('reading_status') or 'unread'}"
            )
            abstract = str(item.get("abstract") or "").strip()
            if abstract:
                lines.append(f"Abstract: {abstract[:500]}")
            for hit in evidence.get(item_id, [])[:2]:
                content = str(hit.get("content") or "").strip().replace("\n", " ")
                if content:
                    lines.append(
                        f"Evidence ({hit.get('document_name') or 'attachment'}, "
                        f"mode={hit.get('mode') or 'search'}): {content[:500]}"
                    )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error searching the project literature library: {exc}"


handler = _tool_search_library

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_search_library"]
