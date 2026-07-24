"""Tool implementation for SearchKnowledge."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_support import (
    logger,
)

TOOL_NAME = 'SearchKnowledge'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_search_knowledge(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    """Search the user's knowledge base for relevant passages."""
    query = str(args.get("query", "") or "").strip()
    if not query:
        return "Error: query is required."

    k = max(1, int(args.get("k", 6) or 6))

    try:
        from cyrene.knowledge import retrieve
        from cyrene.workbench_context import ensure_knowledge_db_for_session
        from cyrene.agent.state import _current_session_id

        db_path = await ensure_knowledge_db_for_session(_current_session_id.get())
        results = await retrieve.search_knowledge(db_path, query, k=k)
        if not results:
            return "No matching documents found in the knowledge base."

        output_lines = [f"Found {len(results)} matching passage(s) from your knowledge base:\n"]
        for i, result in enumerate(results, start=1):
            doc_name = result.get("document_name", "Unknown")
            content = result.get("content", "")[:400]
            score = result.get("score", 0)
            output_lines.append(f"[{i}. {doc_name}] (score: {score:.2f})\n{content}\n")
        return "\n".join(output_lines)
    except Exception as e:
        logger.debug(f"Knowledge base search failed: {e}")
        return f"Error searching knowledge base: {str(e)}"


handler = _tool_search_knowledge

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_search_knowledge"]
