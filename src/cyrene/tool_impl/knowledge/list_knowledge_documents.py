"""Tool implementation for ListKnowledgeDocuments."""

from __future__ import annotations

from typing import Any
from pathlib import Path

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import logger

TOOL_NAME = "ListKnowledgeDocuments"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_list_knowledge_documents(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    """List files available to the current Workbench agent session."""
    limit = max(1, min(int(args.get("limit", 100) or 100), 500))
    status = str(args.get("status", "") or "").strip() or None

    try:
        from cyrene.agent.context import get_current_session_id
        from cyrene.knowledge import store
        from cyrene.workbench.context import ensure_knowledge_db_for_session

        db_path = await ensure_knowledge_db_for_session(get_current_session_id())
        documents = await store.list_documents(db_path, status=status, limit=limit)
        if not documents:
            return "The knowledge base contains no documents matching the requested filters."

        searchable = sum(int(document.get("chunk_count") or 0) > 0 for document in documents)
        lines = [
            f"Knowledge base files: {len(documents)} returned; "
            f"{searchable} searchable and {len(documents) - searchable} without searchable text."
        ]
        for index, document in enumerate(documents, start=1):
            chunk_count = int(document.get("chunk_count") or 0)
            availability = "searchable" if chunk_count > 0 else "not searchable"
            lines.append(
                f"[{index}] {document.get('name') or 'Untitled'} "
                f"(status={document.get('status') or 'unknown'}, "
                f"chunks={chunk_count}, size={int(document.get('size') or 0)}, "
                f"{availability}, id={document.get('id')}, "
                f"file={Path(str(document.get('path') or '')).name})"
            )
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("Knowledge base listing failed: %s", exc)
        return f"Error listing knowledge base documents: {exc}"


handler = _tool_list_knowledge_documents

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "handler",
    "_tool_list_knowledge_documents",
]
